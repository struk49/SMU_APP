from contextlib import contextmanager

from flask import template_rendered, url_for

import app as smu_app
from conftest import create_user, login
from smu_core.models import BrandBrief, Post
from smu_core.blueprints.content_pack import routes as content_pack_routes
from smu_core.services import carousel_generation


CONTENT_PACK_RESULT = """INSTAGRAM_CAPTION:
Instagram caption

FACEBOOK_POST:
Facebook caption

CAROUSEL_IDEA:
Slide 1: First slide
Slide 2: Second slide
Slide 3: Third slide

PINTEREST_PIN_TITLE:
Pin title

PINTEREST_PIN_DESCRIPTION:
Pin description

REDDIT_POST:
Reddit caption

X_POST:
X caption

LINKEDIN_POST:
LinkedIn caption

IMAGE_PROMPT:
Bright image direction

HASHTAGS:
#one #two
"""


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


def set_content_pack_helper(app, monkeypatch, name, helper):
    monkeypatch.setitem(app.extensions["smu_content_pack_helpers"], name, helper)


def test_content_pack_blueprint_is_registered_once(module):
    assert "content_pack" in module.app.blueprints
    assert list(module.app.blueprints).count("content_pack") == 1


def test_content_pack_routes_preserve_old_endpoints_and_methods(module):
    expected = {
        "/content-pack": ("content_pack", {"GET", "POST"}),
        "/content-pack/create-carousel": (
            "create_content_pack_carousel",
            {"POST"},
        ),
        "/content-pack/create-platform-draft": (
            "create_content_pack_platform_draft",
            {"POST"},
        ),
    }

    for path, (endpoint, methods) in expected.items():
        rules = rules_for(module.app, path)

        assert len(rules) == 1
        assert rules[0].endpoint == endpoint
        assert methods.issubset(rules[0].methods)


def test_content_pack_url_for_compatibility(module):
    with module.app.test_request_context():
        assert url_for("content_pack") == "/content-pack"
        assert url_for("create_content_pack_carousel") == (
            "/content-pack/create-carousel"
        )
        assert url_for("create_content_pack_platform_draft") == (
            "/content-pack/create-platform-draft"
        )


def test_content_pack_requires_login(client):
    response = client.get("/content-pack")

    assert response.status_code == 302
    assert "/login" in response.location


def test_content_pack_get_preserves_template_context_and_session(client, app, module):
    user = create_user(module)
    login(client, user)

    with captured_templates(app) as templates:
        response = client.get("/content-pack")

    assert response.status_code == 200
    assert templates[0][0] == "content_pack.html"
    assert templates[0][1]["source_text"] == ""
    assert templates[0][1]["content_pack_result"] is None
    with client.session_transaction() as session:
        assert session["content_pack_started"] is True


def test_content_pack_post_uses_current_user_brand_context(client, app, module, monkeypatch):
    user = create_user(module)
    module.db.session.add(
        module.BrandBrief(user_id=user.id, business_name="User Brand")
    )
    module.db.session.commit()
    login(client, user)
    calls = {}

    def fake_build_brand_context(user_id):
        calls["user_id"] = user_id
        return "BRAND CONTEXT"

    def fake_generate_content_pack(source_text, brand_context):
        calls["source_text"] = source_text
        calls["brand_context"] = brand_context
        return CONTENT_PACK_RESULT

    set_content_pack_helper(app, monkeypatch, "build_brand_context", fake_build_brand_context)
    set_content_pack_helper(app, monkeypatch, "generate_content_pack", fake_generate_content_pack)

    with captured_templates(app) as templates:
        response = client.post(
            "/content-pack",
            data={"source_type": "text", "source_input": "  Topic idea  "},
        )

    assert response.status_code == 200
    assert calls == {
        "user_id": user.id,
        "source_text": "Topic idea",
        "brand_context": "BRAND CONTEXT",
    }
    assert templates[0][1]["source_text"] == "Topic idea"
    assert templates[0][1]["content_pack_result"] == CONTENT_PACK_RESULT


def test_content_pack_tiktok_source_uses_transcript_helper(client, app, module, monkeypatch):
    user = create_user(module)
    login(client, user)
    calls = {}

    def fake_extract_tiktok_transcript(url):
        calls["url"] = url
        return "Transcript text"

    set_content_pack_helper(
        app,
        monkeypatch,
        "extract_tiktok_transcript",
        fake_extract_tiktok_transcript,
    )
    set_content_pack_helper(app, monkeypatch, "build_brand_context", lambda user_id: "")
    set_content_pack_helper(
        app,
        monkeypatch,
        "generate_content_pack",
        lambda source_text, brand_context: f"Generated from {source_text}",
    )

    with captured_templates(app) as templates:
        response = client.post(
            "/content-pack",
            data={"source_type": "tiktok", "source_input": "https://tiktok.test/video"},
        )

    assert response.status_code == 200
    assert calls["url"] == "https://tiktok.test/video"
    assert templates[0][1]["source_text"] == "Transcript text"
    assert templates[0][1]["content_pack_result"] == "Generated from Transcript text"


def test_content_pack_missing_source_redirects_without_generation(client, app, module, monkeypatch):
    user = create_user(module)
    login(client, user)

    def fail_generate_content_pack(source_text, brand_context):
        raise AssertionError("generation should not run without source input")

    set_content_pack_helper(
        app,
        monkeypatch,
        "generate_content_pack",
        fail_generate_content_pack,
    )

    response = client.post(
        "/content-pack",
        data={"source_type": "text", "source_input": ""},
    )

    assert response.status_code == 302
    assert response.location.endswith("/content-pack")


def test_create_content_pack_carousel_creates_grouped_posts(client, app, module, monkeypatch):
    user = create_user(module)
    login(client, user)
    set_content_pack_helper(app, monkeypatch, "get_placeholder_image_url", lambda: "https://cdn.test/placeholder.jpg")
    set_content_pack_helper(
        app,
        monkeypatch,
        "apply_image_style",
        lambda prompt, style: f"{style}:{prompt}",
    )

    response = client.post(
        "/content-pack/create-carousel",
        data={
            "content_pack_result": CONTENT_PACK_RESULT,
            "image_style": "viral_carousel",
        },
    )
    posts = module.Post.query.order_by(module.Post.sort_order.asc()).all()

    assert response.status_code == 302
    assert len(posts) == 3
    assert len({post.group_id for post in posts}) == 1
    assert [post.sort_order for post in posts] == [0, 1, 2]
    assert [post.is_cover for post in posts] == [True, False, False]
    assert {post.user_id for post in posts} == {user.id}
    assert {post.post_type for post in posts} == {"carousel"}
    assert {post.status for post in posts} == {"generating"}
    assert {post.platforms for post in posts} == {"instagram"}
    assert {post.file_url for post in posts} == {"https://cdn.test/placeholder.jpg"}
    assert posts[0].caption == "Instagram caption\n\n#one #two"
    payloads = [carousel_generation.parse_overlay_prompt(post.prompt) for post in posts]
    assert [payload["overlay"]["title"] for payload in payloads] == [
        "First slide",
        "Second slide",
        "Third slide",
    ]
    assert all(post.prompt.startswith("SMU_OVERLAY_V1:") for post in posts)
    for title, payload in zip(
        ["First slide", "Second slide", "Third slide"], payloads
    ):
        assert title not in payload["background_prompt"]
    assert all("no readable text" in payload["background_prompt"] for payload in payloads)
    assert all(payload["overlay"]["body"] is None for payload in payloads)
    assert response.location.endswith(f"/post/{posts[0].id}")


def test_content_pack_carousel_parser_maps_structural_fields():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1:
Title: Speak Polish in Simple Steps
Subtitle: Essential Polish words and phrases for beginners
Body: Zażółć gęślą jaźń

Slide 2:
Phrase: Jak się masz?
Translation: How are you?
CTA: Miłego dnia!

Slide 3:

Slide 4:
Szczęśliwej podróży!

Slide 5: Często tu przychodzisz?"""
    )

    assert slides == [
        {
            "title": "Speak Polish in Simple Steps",
            "body": (
                "Essential Polish words and phrases for beginners\n"
                "Zażółć gęślą jaźń"
            ),
            "cta": None,
            "brand": None,
        },
        {
            "title": "Jak się masz?",
            "body": "How are you?",
            "cta": "Miłego dnia!",
            "brand": None,
        },
        {
            "title": "Szczęśliwej podróży!",
            "body": None,
            "cta": None,
            "brand": None,
        },
        {
            "title": "Często tu przychodzisz?",
            "body": None,
            "cta": None,
            "brand": None,
        },
    ]
    assert all(
        not value.startswith(
            ("Slide ", "Title:", "Subtitle:", "Phrase:", "Translation:")
        )
        for slide in slides
        for value in slide.values()
        if isinstance(value, str)
    )


def test_content_pack_carousel_parser_preserves_unlabelled_text():
    assert content_pack_routes._parse_content_pack_carousel_slides("Miłego dnia!") == [
        {"title": "Miłego dnia!", "body": None, "cta": None, "brand": None}
    ]


def test_content_pack_carousel_builds_six_distinct_text_free_backgrounds(
    client, app, module, monkeypatch
):
    user = create_user(module)
    login(client, user)
    structured_slides = """Slide 1:
Title: Speak Polish in Simple Steps
Subtitle: Essential Polish words and phrases for beginners
Slide 2:
Phrase: Jak się masz?
Translation: How are you?
Slide 3:
Phrase: Miłego dnia!
Translation: Have a nice day!
Slide 4:
Title: Zażółć gęślą jaźń
Body: Polish vocabulary practice
Slide 5:
Phrase: Szczęśliwej podróży!
Translation: Have a good trip!
Slide 6:
Title: Często tu przychodzisz?
CTA: Practise today"""
    content_pack_result = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        structured_slides,
    )
    set_content_pack_helper(
        app,
        monkeypatch,
        "get_placeholder_image_url",
        lambda: "https://cdn.test/placeholder.jpg",
    )
    set_content_pack_helper(
        app,
        monkeypatch,
        "apply_image_style",
        lambda prompt, style: "CONSISTENT BRAND STYLE",
    )

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": content_pack_result, "image_style": "minimal"},
    )
    posts = module.Post.query.order_by(module.Post.sort_order.asc()).all()
    payloads = [carousel_generation.parse_overlay_prompt(post.prompt) for post in posts]
    backgrounds = [payload["background_prompt"] for payload in payloads]

    assert response.status_code == 302
    assert len(payloads) == 6
    assert len(set(backgrounds)) == 6
    assert all("CONSISTENT BRAND STYLE" in prompt for prompt in backgrounds)
    assert all("Slide-specific visual concept:" in prompt for prompt in backgrounds)
    required_text_free_phrases = (
        "no readable text",
        "no words",
        "no letters",
        "no handwriting",
        "no pseudo-text",
        "no gibberish text",
        "no typography",
        "no captions",
        "no labels",
        "no readable logos",
        "no text on screens",
        "no text on paper",
        "no written signs",
    )
    assert all(
        phrase in prompt
        for prompt in backgrounds
        for phrase in required_text_free_phrases
    )
    for payload in payloads:
        for value in payload["overlay"].values():
            if value:
                assert value not in payload["background_prompt"]


def test_content_pack_carousel_preserves_exact_polish_slide_copy(
    client, app, module, monkeypatch
):
    user = create_user(module)
    login(client, user)
    polish_slides = [
        "Miłego dnia!",
        "Szczęśliwej podróży!",
        "Często tu przychodzisz?",
        "Zażółć gęślą jaźń",
    ]
    content_pack_result = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        "\n".join(
            f"Slide {index}: {text}"
            for index, text in enumerate(polish_slides, start=1)
        ),
    )
    set_content_pack_helper(
        app, monkeypatch, "get_placeholder_image_url", lambda: "https://cdn.test/placeholder.jpg"
    )
    set_content_pack_helper(app, monkeypatch, "apply_image_style", lambda prompt, style: prompt)

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": content_pack_result, "image_style": "minimal"},
    )
    posts = module.Post.query.order_by(module.Post.sort_order.asc()).all()
    payloads = [carousel_generation.parse_overlay_prompt(post.prompt) for post in posts]

    assert response.status_code == 302
    assert [payload["overlay"]["title"] for payload in payloads] == polish_slides
    for text, payload in zip(polish_slides, payloads):
        assert text not in payload["background_prompt"]


def test_content_pack_carousel_rejects_oversized_slide_without_rows(
    client, module
):
    user = create_user(module)
    login(client, user)
    oversized = "x" * (carousel_generation.MAX_OVERLAY_TITLE_LENGTH + 1)
    content_pack_result = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        f"Slide 1: {oversized}\nSlide 2: Valid slide",
    )

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": content_pack_result},
    )

    assert response.status_code == 302
    assert response.location.endswith("/content-pack")
    assert module.Post.query.count() == 0


def test_create_content_pack_carousel_validation_creates_no_rows(client, module):
    user = create_user(module)
    login(client, user)

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": "", "image_style": "viral_carousel"},
    )

    assert response.status_code == 302
    assert response.location.endswith("/content-pack")
    assert module.Post.query.count() == 0


def test_create_platform_draft_creates_single_post(client, app, module, monkeypatch):
    user = create_user(module)
    login(client, user)
    set_content_pack_helper(app, monkeypatch, "build_brand_context", lambda user_id: "BRAND")
    set_content_pack_helper(app, monkeypatch, "get_placeholder_image_url", lambda: "https://cdn.test/single.jpg")
    set_content_pack_helper(app, monkeypatch, "apply_image_style", lambda prompt, style: f"{style}:{prompt}")

    response = client.post(
        "/content-pack/create-platform-draft",
        data={
            "content_pack_result": CONTENT_PACK_RESULT,
            "platform": "linkedin",
            "image_style": "minimal",
        },
    )
    post = module.Post.query.first()

    assert response.status_code == 302
    assert post is not None
    assert post.user_id == user.id
    assert post.file_url == "https://cdn.test/single.jpg"
    assert post.file_type == "image"
    assert post.caption == "LinkedIn caption"
    assert post.platforms == "linkedin"
    assert post.post_type == "single"
    assert post.status == "generating"
    assert post.sort_order == 0
    assert post.is_cover is False
    assert "Brand Brief:\nBRAND" in post.prompt
    assert "LinkedIn caption" in post.prompt
    assert response.location.endswith(f"/post/{post.id}")


def test_create_platform_draft_validation_creates_no_row(client, module):
    user = create_user(module)
    login(client, user)

    response = client.post(
        "/content-pack/create-platform-draft",
        data={"content_pack_result": CONTENT_PACK_RESULT, "platform": "threads"},
    )

    assert response.status_code == 302
    assert response.location.endswith("/content-pack")
    assert module.Post.query.count() == 0


def test_content_pack_brand_lookup_is_user_specific(client, app, module, monkeypatch):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    module.db.session.add(module.BrandBrief(user_id=owner.id, business_name="Owner"))
    module.db.session.add(module.BrandBrief(user_id=other.id, business_name="Other"))
    module.db.session.commit()
    login(client, other)
    seen_user_ids = []

    def fake_build_brand_context(user_id):
        seen_user_ids.append(user_id)
        return "OTHER BRAND CONTEXT"

    set_content_pack_helper(app, monkeypatch, "build_brand_context", fake_build_brand_context)
    set_content_pack_helper(
        app,
        monkeypatch,
        "generate_content_pack",
        lambda source_text, brand_context: brand_context,
    )

    with captured_templates(app) as templates:
        response = client.post(
            "/content-pack",
            data={"source_type": "text", "source_input": "Idea"},
        )

    assert response.status_code == 200
    assert seen_user_ids == [other.id]
    assert templates[0][1]["content_pack_result"] == "OTHER BRAND CONTEXT"


def test_content_pack_model_and_app_import_compatibility_remain(module):
    assert smu_app.Post is Post
    assert smu_app.BrandBrief is BrandBrief
    assert module.Post is Post
    assert module.BrandBrief is BrandBrief


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
        "index",
        "calendar_view",
        "post_studio",
        "send_to_make",
        "tiktok_repurpose",
    }.issubset(endpoints)


def test_scheduler_reference_is_untouched(module):
    assert module.scheduler is smu_app.scheduler
