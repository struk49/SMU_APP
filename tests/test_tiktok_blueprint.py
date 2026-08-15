import logging
from contextlib import contextmanager

from flask import template_rendered, url_for

import app as smu_app
from conftest import create_user, login
from smu_core.models import Post
from smu_core.services.tiktok import TikTokRepurposeError, TikTokRepurposeResult


def tiktok_result(**overrides):
    values = {
        "instagram_caption": "Instagram caption",
        "facebook_caption": "Facebook caption",
        "carousel_idea": "Slide 1: First slide\nSlide 2: Second slide",
        "image_prompt": "Image prompt",
        "hashtags": "#one #two",
    }
    values.update(overrides)
    return TikTokRepurposeResult(**values)


@contextmanager
def captured_templates(app):
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append((template.name, context))

    template_rendered.connect(record, app)
    try:
        yield recorded
    finally:
        template_rendered.disconnect(record, app)


def rules_for(app, path):
    return [rule for rule in app.url_map.iter_rules() if rule.rule == path]


def set_tiktok_helper(app, monkeypatch, name, helper):
    monkeypatch.setitem(app.extensions["smu_tiktok_helpers"], name, helper)


def log_contexts(caplog, message):
    return [
        getattr(record, "smu_context", {})
        for record in caplog.records
        if record.message == message
    ]


def test_tiktok_blueprint_is_registered_once(module):
    assert "tiktok" in module.app.blueprints
    assert list(module.app.blueprints).count("tiktok") == 1


def test_tiktok_routes_preserve_old_endpoints_and_methods(module):
    expected = {
        "/tiktok": ("tiktok_repurpose", {"GET", "POST"}),
        "/tiktok/create-draft": ("create_tiktok_draft", {"POST"}),
        "/tiktok/create-carousel-draft": (
            "create_tiktok_carousel_draft",
            {"POST"},
        ),
    }

    for path, (endpoint, methods) in expected.items():
        rules = rules_for(module.app, path)

        assert len(rules) == 1
        assert rules[0].endpoint == endpoint
        assert methods.issubset(rules[0].methods)


def test_tiktok_url_for_compatibility(module):
    with module.app.test_request_context():
        assert url_for("tiktok_repurpose") == "/tiktok"
        assert url_for("create_tiktok_draft") == "/tiktok/create-draft"
        assert url_for("create_tiktok_carousel_draft") == (
            "/tiktok/create-carousel-draft"
        )


def test_tiktok_requires_login(client):
    response = client.get("/tiktok")

    assert response.status_code == 302
    assert "/login" in response.location


def test_tiktok_get_renders_existing_template_context(client, app, module):
    user = create_user(module)
    login(client, user)

    with captured_templates(app) as templates:
        response = client.get("/tiktok")

    assert response.status_code == 200
    assert templates[0][0] == "tiktok.html"
    assert templates[0][1]["tiktok_url"] == ""
    assert templates[0][1]["transcript"] is None
    assert templates[0][1]["repurpose_result"] is None


def test_tiktok_post_extracts_transcript_and_repurposes_content(
    client, app, module, monkeypatch
):
    user = create_user(module)
    login(client, user)
    calls = {}

    def fake_extract_tiktok_transcript(url):
        calls["url"] = url
        return "Transcript text"

    def fake_build_brand_context(user_id):
        calls["user_id"] = user_id
        return "Brand context"

    def fake_repurpose_tiktok_content(transcript, brand_context):
        calls["transcript"] = transcript
        calls["brand_context"] = brand_context
        return tiktok_result()

    set_tiktok_helper(
        app,
        monkeypatch,
        "extract_tiktok_transcript",
        fake_extract_tiktok_transcript,
    )
    set_tiktok_helper(app, monkeypatch, "build_brand_context", fake_build_brand_context)
    set_tiktok_helper(
        app,
        monkeypatch,
        "repurpose_tiktok_content",
        fake_repurpose_tiktok_content,
    )

    with captured_templates(app) as templates:
        response = client.post(
            "/tiktok",
            data={"tiktok_url": "  https://tiktok.test/video  "},
        )

    assert response.status_code == 200
    assert calls == {
        "url": "https://tiktok.test/video",
        "user_id": user.id,
        "transcript": "Transcript text",
        "brand_context": "Brand context",
    }
    assert templates[0][1]["tiktok_url"] == "https://tiktok.test/video"
    assert templates[0][1]["transcript"] == "Transcript text"
    assert templates[0][1]["repurpose_result"] == tiktok_result()


def test_tiktok_post_renders_structured_result_fields(client, app, module, monkeypatch):
    user = create_user(module)
    login(client, user)
    result = tiktok_result(
        instagram_caption="Instagram caption with \"quotes\"\nand newlines",
        facebook_caption="Facebook caption",
        carousel_idea="Slide 1: Hook\nSlide 2: Payoff",
        image_prompt="Bright image prompt",
        hashtags="#one #two",
    )

    set_tiktok_helper(app, monkeypatch, "extract_tiktok_transcript", lambda url: "Transcript text")
    set_tiktok_helper(app, monkeypatch, "build_brand_context", lambda user_id: "Brand context")
    set_tiktok_helper(app, monkeypatch, "repurpose_tiktok_content", lambda *_: result)

    response = client.post("/tiktok", data={"tiktok_url": "https://tiktok.test/video"})
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Instagram caption with &#34;quotes&#34;" in html
    assert "Facebook caption" in html
    assert "Slide 1: Hook" in html
    assert "Bright image prompt" in html
    assert "#one #two" in html
    assert "extractSection" not in html
    assert "INSTAGRAM_CAPTION:" not in html
    assert "FACEBOOK_CAPTION:" not in html
    assert "CAROUSEL_IDEA:" not in html
    assert "IMAGE_PROMPT:" not in html
    assert "HASHTAGS:" not in html


def test_tiktok_malformed_repurpose_result_shows_friendly_error(
    client, app, module, monkeypatch
):
    user = create_user(module)
    login(client, user)

    set_tiktok_helper(app, monkeypatch, "extract_tiktok_transcript", lambda url: "Transcript text")
    set_tiktok_helper(app, monkeypatch, "build_brand_context", lambda user_id: "Brand context")
    set_tiktok_helper(
        app,
        monkeypatch,
        "repurpose_tiktok_content",
        lambda *_: {"instagram_caption": "missing other fields"},
    )

    response = client.post(
        "/tiktok",
        data={"tiktok_url": "https://tiktok.test/video"},
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "We couldn&#39;t generate usable social posts from this TikTok." in html
    assert "missing other fields" not in html
    assert "TikTok repurpose response was missing a field" not in html


def test_tiktok_service_exception_shows_friendly_error_without_raw_details(
    client, app, module, monkeypatch
):
    user = create_user(module)
    login(client, user)

    set_tiktok_helper(app, monkeypatch, "extract_tiktok_transcript", lambda url: "Transcript text")
    set_tiktok_helper(app, monkeypatch, "build_brand_context", lambda user_id: "Brand context")

    def fail_repurpose(*_):
        raise TikTokRepurposeError("parser detail")

    set_tiktok_helper(app, monkeypatch, "repurpose_tiktok_content", fail_repurpose)

    response = client.post(
        "/tiktok",
        data={"tiktok_url": "https://tiktok.test/video"},
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "We couldn&#39;t generate usable social posts from this TikTok." in html
    assert "parser detail" not in html


def test_tiktok_transcript_exception_shows_friendly_error_without_raw_details(
    client, app, module, monkeypatch, caplog
):
    user = create_user(module)
    login(client, user)
    caplog.set_level(logging.INFO, logger="smu_core.blueprints.tiktok.routes")

    def fail_extract(url):
        raise RuntimeError("yt-dlp secret stack detail")

    set_tiktok_helper(app, monkeypatch, "extract_tiktok_transcript", fail_extract)

    response = client.post("/tiktok", data={"tiktok_url": "https://tiktok.test/video"})
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "We couldn&#39;t extract usable content from that TikTok." in html
    assert "Please check the URL and try again." in html
    assert "yt-dlp secret stack detail" not in html
    assert "yt-dlp secret stack detail" not in caplog.text
    contexts = log_contexts(caplog, "tiktok_transcript_extraction_failed")
    assert contexts
    assert contexts[0]["stage"] == "transcript_extraction"
    assert contexts[0]["user_id"] == user.id
    assert contexts[0]["url_hostname"] == "tiktok.test"
    assert contexts[0]["exception_class"] == "RuntimeError"


def test_tiktok_unexpected_exception_shows_friendly_error_without_raw_details(
    client, app, module, monkeypatch, caplog
):
    user = create_user(module)
    login(client, user)
    caplog.set_level(logging.INFO, logger="smu_core.blueprints.tiktok.routes")

    set_tiktok_helper(app, monkeypatch, "extract_tiktok_transcript", lambda url: "Transcript text")

    def fail_brand_context(user_id):
        raise RuntimeError("internal OpenAI or brand detail")

    set_tiktok_helper(app, monkeypatch, "build_brand_context", fail_brand_context)

    response = client.post("/tiktok", data={"tiktok_url": "https://tiktok.test/video"})
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Something went wrong while processing this TikTok. Please try again." in html
    assert "internal OpenAI or brand detail" not in html
    assert "internal OpenAI or brand detail" not in caplog.text
    contexts = log_contexts(caplog, "tiktok_repurpose_unexpected_failure")
    assert contexts
    assert contexts[0]["stage"] == "repurpose_processing"
    assert contexts[0]["exception_class"] == "RuntimeError"


def test_tiktok_structured_content_does_not_break_script_context(
    client, app, module, monkeypatch
):
    user = create_user(module)
    login(client, user)
    result = tiktok_result(
        instagram_caption='Caption with </script><strong>"HTML"</strong>',
        image_prompt="Image with apostrophe's detail",
    )

    set_tiktok_helper(app, monkeypatch, "extract_tiktok_transcript", lambda url: "Transcript text")
    set_tiktok_helper(app, monkeypatch, "build_brand_context", lambda user_id: "Brand context")
    set_tiktok_helper(app, monkeypatch, "repurpose_tiktok_content", lambda *_: result)

    response = client.post("/tiktok", data={"tiktok_url": "https://tiktok.test/video"})
    html = response.get_data(as_text=True)

    assert "&lt;/script&gt;&lt;strong&gt;&#34;HTML&#34;&lt;/strong&gt;" in html
    assert "</script><strong>" not in html
    assert "Image with apostrophe&#39;s detail" in html


def test_tiktok_missing_url_redirects_without_external_work(
    client, app, module, monkeypatch
):
    user = create_user(module)
    login(client, user)

    def fail_extract_tiktok_transcript(url):
        raise AssertionError("transcript extraction should not run without a URL")

    set_tiktok_helper(
        app,
        monkeypatch,
        "extract_tiktok_transcript",
        fail_extract_tiktok_transcript,
    )

    response = client.post("/tiktok", data={"tiktok_url": "   "})

    assert response.status_code == 302
    assert response.location.endswith("/tiktok")


def test_create_tiktok_draft_creates_current_user_single_post(
    client, app, module, monkeypatch
):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    login(client, other)
    calls = {}

    def fake_apply_image_style(prompt, style):
        calls["prompt"] = prompt
        calls["style"] = style
        return f"{style}:{prompt}"

    def fake_generate_openai_image(prompt):
        calls["generated_prompt"] = prompt
        return "https://cdn.test/tiktok.jpg"

    set_tiktok_helper(app, monkeypatch, "apply_image_style", fake_apply_image_style)
    set_tiktok_helper(app, monkeypatch, "generate_openai_image", fake_generate_openai_image)

    response = client.post(
        "/tiktok/create-draft",
        data={
            "caption": "  Draft caption  ",
            "image_prompt": "  Image prompt  ",
            "image_style": "bold",
        },
    )
    post = module.Post.query.first()

    assert response.status_code == 302
    assert calls == {
        "prompt": "Image prompt",
        "style": "bold",
        "generated_prompt": "bold:Image prompt",
    }
    assert post is not None
    assert post.user_id == other.id
    assert post.user_id != owner.id
    assert post.file_url == "https://cdn.test/tiktok.jpg"
    assert post.file_type == "image"
    assert post.prompt == "bold:Image prompt"
    assert post.caption == "Draft caption"
    assert post.platforms == "instagram,facebook"
    assert post.post_type == "single"
    assert post.status == "draft"
    assert post.sort_order == 0
    assert post.is_cover is False
    assert response.location.endswith(f"/post/{post.id}")


def test_create_tiktok_draft_validation_creates_no_row(client, module):
    user = create_user(module)
    login(client, user)

    response = client.post(
        "/tiktok/create-draft",
        data={"caption": "", "image_prompt": "Image prompt"},
    )

    assert response.status_code == 302
    assert response.location.endswith("/tiktok")
    assert module.Post.query.count() == 0


def test_create_tiktok_draft_image_failure_rolls_back_without_post(
    client, app, module, monkeypatch, caplog
):
    user = create_user(module)
    login(client, user)
    caplog.set_level(logging.INFO, logger="smu_core.blueprints.tiktok.routes")
    rollback_calls = []
    original_rollback = module.db.session.rollback

    def fail_generate_image(prompt):
        raise RuntimeError("image provider raw detail")

    def rollback_spy():
        rollback_calls.append("rollback")
        return original_rollback()

    set_tiktok_helper(app, monkeypatch, "apply_image_style", lambda prompt, style: prompt)
    set_tiktok_helper(app, monkeypatch, "generate_openai_image", fail_generate_image)
    monkeypatch.setattr(module.db.session, "rollback", rollback_spy)

    response = client.post(
        "/tiktok/create-draft",
        data={
            "caption": "Caption",
            "image_prompt": "Image prompt",
            "image_style": "bold",
        },
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert rollback_calls == ["rollback"]
    assert module.Post.query.count() == 0
    assert "We couldn&#39;t generate the image for this draft. Please try again." in html
    assert "image provider raw detail" not in html
    assert "image provider raw detail" not in caplog.text
    contexts = log_contexts(caplog, "tiktok_single_draft_image_failed")
    assert contexts
    assert contexts[0]["stage"] == "single_draft_image_generation"
    assert contexts[0]["user_id"] == user.id


def test_create_tiktok_draft_commit_failure_rolls_back_without_post(
    client, app, module, monkeypatch, caplog
):
    user = create_user(module)
    login(client, user)
    caplog.set_level(logging.INFO, logger="smu_core.blueprints.tiktok.routes")
    rollback_calls = []
    original_rollback = module.db.session.rollback

    def fail_commit():
        raise RuntimeError("database raw detail")

    def rollback_spy():
        rollback_calls.append("rollback")
        return original_rollback()

    set_tiktok_helper(app, monkeypatch, "apply_image_style", lambda prompt, style: prompt)
    set_tiktok_helper(
        app,
        monkeypatch,
        "generate_openai_image",
        lambda prompt: "https://cdn.test/tiktok.jpg",
    )
    monkeypatch.setattr(module.db.session, "commit", fail_commit)
    monkeypatch.setattr(module.db.session, "rollback", rollback_spy)

    response = client.post(
        "/tiktok/create-draft",
        data={
            "caption": "Caption",
            "image_prompt": "Image prompt",
            "image_style": "bold",
        },
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert rollback_calls == ["rollback"]
    assert module.Post.query.count() == 0
    assert "We couldn&#39;t create this draft. Please try again." in html
    assert "database raw detail" not in html
    assert "database raw detail" not in caplog.text
    contexts = log_contexts(caplog, "tiktok_single_draft_creation_failed")
    assert contexts
    assert contexts[0]["stage"] == "single_draft_creation"
    assert contexts[0]["exception_class"] == "RuntimeError"


def test_create_tiktok_carousel_draft_creates_grouped_current_user_posts(
    client, app, module, monkeypatch
):
    user = create_user(module)
    login(client, user)
    seen_user_ids = []

    def fake_build_brand_context(user_id):
        seen_user_ids.append(user_id)
        return "Brand context"

    set_tiktok_helper(app, monkeypatch, "build_brand_context", fake_build_brand_context)
    set_tiktok_helper(
        app,
        monkeypatch,
        "apply_image_style",
        lambda prompt, style: f"{style}:{prompt}",
    )
    set_tiktok_helper(
        app,
        monkeypatch,
        "get_placeholder_image_url",
        lambda: "https://cdn.test/placeholder.jpg",
    )

    response = client.post(
        "/tiktok/create-carousel-draft",
        data={
            "caption": "  Carousel caption  ",
            "image_prompt": "Image prompt",
            "image_style": "viral",
            "carousel_idea": "\n".join(
                [
                    "Slide 1: First slide",
                    "Slide 2: Second slide",
                    "Slide 3: Third slide",
                ]
            ),
        },
    )
    posts = module.Post.query.order_by(module.Post.sort_order.asc()).all()

    assert response.status_code == 302
    assert seen_user_ids == [user.id]
    assert len(posts) == 3
    assert len({post.group_id for post in posts}) == 1
    assert [post.sort_order for post in posts] == [0, 1, 2]
    assert [post.is_cover for post in posts] == [True, False, False]
    assert {post.user_id for post in posts} == {user.id}
    assert {post.file_url for post in posts} == {"https://cdn.test/placeholder.jpg"}
    assert {post.caption for post in posts} == {"Carousel caption"}
    assert {post.platforms for post in posts} == {"instagram,facebook"}
    assert {post.post_type for post in posts} == {"carousel"}
    assert {post.status for post in posts} == {"generating"}
    assert "First slide" in posts[0].prompt
    assert "Second slide" in posts[1].prompt
    assert response.location.endswith(f"/post/{posts[0].id}")


def test_create_tiktok_carousel_commit_failure_rolls_back_without_partial_rows(
    client, app, module, monkeypatch, caplog
):
    user = create_user(module)
    login(client, user)
    caplog.set_level(logging.INFO, logger="smu_core.blueprints.tiktok.routes")
    rollback_calls = []
    original_rollback = module.db.session.rollback

    def fail_commit():
        raise RuntimeError("carousel database raw detail")

    def rollback_spy():
        rollback_calls.append("rollback")
        return original_rollback()

    set_tiktok_helper(app, monkeypatch, "build_brand_context", lambda user_id: "Brand context")
    set_tiktok_helper(app, monkeypatch, "apply_image_style", lambda prompt, style: prompt)
    set_tiktok_helper(
        app,
        monkeypatch,
        "get_placeholder_image_url",
        lambda: "https://cdn.test/placeholder.jpg",
    )
    monkeypatch.setattr(module.db.session, "commit", fail_commit)
    monkeypatch.setattr(module.db.session, "rollback", rollback_spy)

    response = client.post(
        "/tiktok/create-carousel-draft",
        data={
            "caption": "Carousel caption",
            "image_prompt": "Image prompt",
            "image_style": "viral",
            "carousel_idea": "Slide 1: First\nSlide 2: Second\nSlide 3: Third",
        },
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert rollback_calls == ["rollback"]
    assert module.Post.query.count() == 0
    assert "We couldn&#39;t create this carousel draft. Please try again." in html
    assert "carousel database raw detail" not in html
    assert "carousel database raw detail" not in caplog.text
    contexts = log_contexts(caplog, "tiktok_carousel_draft_creation_failed")
    assert contexts
    assert contexts[0]["stage"] == "carousel_draft_creation"
    assert contexts[0]["carousel_item_count"] == 3


def test_create_tiktok_carousel_validation_creates_no_rows(client, module):
    user = create_user(module)
    login(client, user)

    missing_idea = client.post(
        "/tiktok/create-carousel-draft",
        data={"caption": "Caption", "carousel_idea": ""},
    )
    one_slide = client.post(
        "/tiktok/create-carousel-draft",
        data={"caption": "Caption", "carousel_idea": "Only slide"},
    )

    assert missing_idea.status_code == 302
    assert missing_idea.location.endswith("/tiktok")
    assert one_slide.status_code == 302
    assert one_slide.location.endswith("/tiktok")
    assert module.Post.query.count() == 0


def test_tiktok_model_and_app_import_compatibility_remain(module):
    assert smu_app.Post is Post
    assert module.Post is Post


def test_existing_blueprints_and_unrelated_endpoints_remain_registered(module):
    endpoints = {rule.endpoint for rule in module.app.url_map.iter_rules()}

    assert {
        "landing_page",
        "privacy_policy",
        "terms_of_service",
        "maintenance",
        "help_centre",
        "contact",
        "register",
        "login",
        "logout",
        "beta_apply",
        "admin_beta",
        "submit_feedback",
        "brand_brief",
        "connected_accounts",
        "content_pack",
        "index",
        "calendar_view",
        "post_studio",
        "send_to_make",
    }.issubset(endpoints)


def test_scheduler_reference_is_untouched(module):
    assert module.scheduler is smu_app.scheduler
