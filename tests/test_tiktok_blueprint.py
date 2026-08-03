from contextlib import contextmanager

from flask import template_rendered, url_for

import app as smu_app
from conftest import create_user, login
from smu_core.models import Post


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
    assert templates[0][1]["generated_content"] is None


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
        return "Generated content"

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
    assert templates[0][1]["generated_content"] == "Generated content"


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
