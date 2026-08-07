import json

import app as smu_app
from conftest import create_post, create_user
from smu_core.models import BrandBrief, PostRevision
from smu_core.services import captions


class FakeOpenAIResponse:
    def __init__(self, text):
        self.output_text = text


class FakeResponses:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeOpenAIResponse(self.text)


class FakeOpenAIClient:
    def __init__(self, text):
        self.responses = FakeResponses(text)


def test_caption_service_exports_and_app_compatibility(module):
    for name in {
        "build_brand_context",
        "save_post_revision",
        "update_brand_coach",
        "rewrite_caption_with_action",
        "grade_post_with_ai",
        "extract_overall_score",
    }:
        assert callable(getattr(captions, name))
        assert callable(getattr(smu_app, name))
        assert getattr(smu_app, name).__module__ == "app"


def test_app_wrappers_delegate_to_caption_service(app, module, monkeypatch):
    user = create_user(module)
    post = create_post(module, user)
    calls = []

    monkeypatch.setattr(
        module.captions_service,
        "build_brand_context",
        lambda user_id: calls.append(("context", user_id)) or "BRAND",
    )
    monkeypatch.setattr(
        module.captions_service,
        "save_post_revision",
        lambda revision_post, source="manual": (
            calls.append(("revision", revision_post.id, source)) or "revision"
        ),
    )
    monkeypatch.setattr(
        module.captions_service,
        "extract_overall_score",
        lambda grade_result: calls.append(("score", grade_result)) or 7.5,
    )
    monkeypatch.setattr(
        module.captions_service,
        "update_brand_coach",
        lambda coached_post, brand_context, **kwargs: (
            calls.append(
                (
                    "coach",
                    coached_post.id,
                    brand_context,
                    sorted(kwargs.keys()),
                )
            )
            or {"overall_score": 9.0}
        ),
    )
    monkeypatch.setattr(
        module.captions_service,
        "rewrite_caption_with_action",
        lambda rewrite_post, brand_context, action, **kwargs: (
            calls.append(
                (
                    "rewrite",
                    rewrite_post.id,
                    brand_context,
                    action,
                    sorted(kwargs.keys()),
                )
            )
            or "rewritten"
        ),
    )
    monkeypatch.setattr(
        module.captions_service,
        "grade_post_with_ai",
        lambda graded_post, brand_context, **kwargs: (
            calls.append(
                (
                    "grade",
                    graded_post.id,
                    brand_context,
                    sorted(kwargs.keys()),
                )
            )
            or "grade result"
        ),
    )

    assert module.build_brand_context(user.id) == "BRAND"
    assert module.save_post_revision(post, source="test") == "revision"
    assert module.extract_overall_score("OVERALL_SCORE: 7.5/10") == 7.5
    assert module.update_brand_coach(post, "BRAND") == {"overall_score": 9.0}
    assert module.rewrite_caption_with_action(post, "BRAND", "hook") == "rewritten"
    assert module.grade_post_with_ai(post, "BRAND") == "grade result"
    assert calls == [
        ("context", user.id),
        ("revision", post.id, "test"),
        ("score", "OVERALL_SCORE: 7.5/10"),
        (
            "coach",
            post.id,
            "BRAND",
            ["evaluate_brand_match_func", "parse_brand_feedback_func"],
        ),
        ("rewrite", post.id, "BRAND", "hook", ["openai_client"]),
        ("grade", post.id, "BRAND", ["openai_client"]),
    ]


def test_existing_caption_bridges_remain_present_and_callable(module):
    assert sorted(
        key
        for key in module.app.extensions
        if key in ["smu_caption_helpers", "smu_ai_editor_helpers", "smu_studio_helpers"]
    ) == ["smu_ai_editor_helpers", "smu_caption_helpers", "smu_studio_helpers"]

    for bridge_name in ["smu_caption_helpers", "smu_ai_editor_helpers", "smu_studio_helpers"]:
        for helper in module.app.extensions[bridge_name].values():
            assert callable(helper)


def test_build_brand_context_returns_empty_string_without_brief(app, module):
    user = create_user(module)

    assert captions.build_brand_context(user.id) == ""


def test_build_brand_context_returns_exact_brand_brief_structure(app, module):
    user = create_user(module)
    brief = BrandBrief(
        user_id=user.id,
        business_name="SMU",
        niche="AI content",
        target_audience="Small business owners",
        offer="Content automation",
        tone_of_voice="Warm and practical",
        content_goals="Book demos",
        main_platforms="Instagram, Facebook",
        cta_style="Direct",
        words_to_avoid="jargon",
    )
    module.db.session.add(brief)
    module.db.session.commit()

    context = captions.build_brand_context(user.id)

    assert context == """
BRAND BRIEF

Business Name:
SMU

Niche:
AI content

Target Audience:
Small business owners

Offer:
Content automation

Tone Of Voice:
Warm and practical

Content Goals:
Book demos

Platforms:
Instagram, Facebook

CTA Style:
Direct

Words To Avoid:
jargon

IMPORTANT:
All content must match this brand brief.
Do not create generic content.
Use the tone, audience and offer above.
"""


def test_save_post_revision_creates_exact_row_without_commit(app, module, monkeypatch):
    user = create_user(module)
    post = create_post(module, user)
    post.caption = "Snapshot caption"
    post.grade_score = 8.0
    module.db.session.commit()
    commit_calls = []
    original_commit = module.db.session.commit
    monkeypatch.setattr(module.db.session, "commit", lambda: commit_calls.append(True))

    revision = captions.save_post_revision(post, source="before_test")

    assert revision.post_id == post.id
    assert revision.user_id == user.id
    assert revision.version_number == 1
    assert revision.caption == "Snapshot caption"
    assert revision.score == 8.0
    assert revision.source == "before_test"
    assert commit_calls == []

    original_commit()
    assert PostRevision.query.filter_by(post_id=post.id).one().id == revision.id


def test_save_post_revision_increments_version_and_preserves_relationship(app, module):
    user = create_user(module)
    post = create_post(module, user)
    first = captions.save_post_revision(post, source="first")
    post.caption = "Second"
    second = captions.save_post_revision(post, source="second")
    module.db.session.commit()

    assert first.version_number == 1
    assert second.version_number == 2
    assert second.post is post


def test_update_brand_coach_updates_score_feedback_and_does_not_commit(app, module, monkeypatch):
    user = create_user(module)
    post = create_post(module, user)
    commit_calls = []
    monkeypatch.setattr(module.db.session, "commit", lambda: commit_calls.append(True))

    parsed = captions.update_brand_coach(
        post,
        "BRAND",
        evaluate_brand_match_func=lambda coached_post, context: json.dumps(
            {
                "overall_score": 8.5,
                "tone": True,
                "audience": True,
                "offer": False,
                "cta": True,
                "brand_voice": True,
                "recommendations": ["Mention the offer earlier."],
            }
        ),
        parse_brand_feedback_func=module.parse_brand_feedback,
    )

    assert parsed["overall_score"] == 8.5
    assert post.brand_score == 8.5
    assert json.loads(post.brand_feedback)["recommendations"] == [
        "Mention the offer earlier."
    ]
    assert commit_calls == []


def test_update_brand_coach_returns_none_on_helper_failure(app, module):
    user = create_user(module)
    post = create_post(module, user)

    result = captions.update_brand_coach(
        post,
        "",
        evaluate_brand_match_func=lambda *args: (_ for _ in ()).throw(
            RuntimeError("coach failed")
        ),
        parse_brand_feedback_func=module.parse_brand_feedback,
    )

    assert result is None
    assert post.brand_score is None
    assert post.brand_feedback is None


def test_app_update_brand_coach_wrapper_passes_actual_post_and_context(app, module, monkeypatch):
    user = create_user(module)
    post = create_post(module, user)
    calls = []

    def fake_evaluate(coached_post, brand_context):
        calls.append((coached_post, brand_context))
        return json.dumps({"overall_score": 6})

    monkeypatch.setattr(module, "evaluate_brand_match", fake_evaluate)

    result = module.update_brand_coach(post, "BRAND")

    assert calls == [(post, "BRAND")]
    assert result["overall_score"] == 6.0
    assert post.brand_score == 6.0


def test_rewrite_caption_actions_preserve_prompt_mapping(app, module):
    user = create_user(module)
    post = create_post(module, user, platforms="instagram")
    fake_client = FakeOpenAIClient(" Rewritten caption ")
    expected = {
        "hook": "Improve the opening hook. Make the first line more attention-grabbing.",
        "cta": "Improve the call to action. Make the next step clearer and more persuasive.",
        "shorten": "Shorten the caption while keeping the main message.",
        "professional": "Rewrite the caption in a more professional tone.",
        "friendly": "Rewrite the caption in a warmer, friendlier tone.",
        "alternatives": "Create 3 alternative versions of this caption.",
    }

    for action, instruction in expected.items():
        assert captions.rewrite_caption_with_action(
            post,
            "BRAND",
            action,
            openai_client=fake_client,
        ) == "Rewritten caption"
        call = fake_client.responses.calls[-1]
        assert call["model"] == "gpt-4.1-mini"
        assert call["input"].count(instruction) == 1
        assert "Current Caption:\nCaption" in call["input"]
        assert "Platform:\ninstagram" in call["input"]


def test_rewrite_caption_unknown_action_preserves_hook_fallback(app, module):
    user = create_user(module)
    post = create_post(module, user)
    fake_client = FakeOpenAIClient("Fallback")

    captions.rewrite_caption_with_action(
        post,
        "",
        "unknown",
        openai_client=fake_client,
    )

    assert (
        "Improve the opening hook. Make the first line more attention-grabbing."
        in fake_client.responses.calls[0]["input"]
    )


def test_rewrite_caption_exception_propagates(app, module):
    user = create_user(module)
    post = create_post(module, user)

    class FailingResponses:
        def create(self, **kwargs):
            raise RuntimeError("OpenAI failed")

    class FailingClient:
        responses = FailingResponses()

    try:
        captions.rewrite_caption_with_action(post, "", "hook", openai_client=FailingClient())
    except RuntimeError as exc:
        assert str(exc) == "OpenAI failed"
    else:
        raise AssertionError("Expected RuntimeError")


def test_grade_post_with_ai_preserves_request_shape(app, module):
    user = create_user(module)
    post = create_post(module, user, platforms="instagram")
    post.post_type = "carousel"
    fake_client = FakeOpenAIClient(" OVERALL_SCORE: 7/10 ")

    result = captions.grade_post_with_ai(post, "BRAND", openai_client=fake_client)
    call = fake_client.responses.calls[0]

    assert result == "OVERALL_SCORE: 7/10"
    assert call["model"] == "gpt-4.1-mini"
    assert "Post Caption:\nCaption" in call["input"]
    assert "Platform:\ninstagram" in call["input"]
    assert "Post Type:\ncarousel" in call["input"]
    assert "OVERALL_SCORE: X/10" in call["input"]


def test_grade_post_exception_propagates(app, module):
    user = create_user(module)
    post = create_post(module, user)

    class FailingResponses:
        def create(self, **kwargs):
            raise RuntimeError("grade failed")

    class FailingClient:
        responses = FailingResponses()

    try:
        captions.grade_post_with_ai(post, "", openai_client=FailingClient())
    except RuntimeError as exc:
        assert str(exc) == "grade failed"
    else:
        raise AssertionError("Expected RuntimeError")


def test_extract_overall_score_preserves_parsing_rules():
    assert captions.extract_overall_score("OVERALL_SCORE: 8/10") == 8.0
    assert captions.extract_overall_score("OVERALL_SCORE: 8.5/10") == 8.5
    assert captions.extract_overall_score("OVERALL_SCORE: 100/10") == 100.0
    assert captions.extract_overall_score("OVERALL_SCORE: 80%") is None
    assert captions.extract_overall_score("Score: 8/10") is None
    assert captions.extract_overall_score("") is None
    assert captions.extract_overall_score(None) is None
