import json
from contextlib import contextmanager

from flask import template_rendered, url_for

import app as smu_app
from conftest import create_carousel, create_post, create_user, login
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


def test_post_edit_routes_are_registered_once_with_old_endpoints(module):
    expected = {
        "/edit-post/<int:post_id>": ("edit_post", {"GET", "POST"}),
        "/edit-carousel/<group_id>": ("edit_carousel", {"GET", "POST"}),
    }

    assert "posts" in module.app.blueprints
    assert list(module.app.blueprints).count("posts") == 1

    for path, (endpoint, methods) in expected.items():
        rules = rules_for(module.app, path)

        assert len(rules) == 1
        assert rules[0].endpoint == endpoint
        assert methods.issubset(rules[0].methods)


def test_post_edit_url_for_compatibility(module):
    with module.app.test_request_context():
        assert url_for("edit_post", post_id=1) == "/edit-post/1"
        assert url_for("edit_carousel", group_id="group-1") == "/edit-carousel/group-1"


def test_post_edit_routes_require_login(client, module):
    user = create_user(module)
    post = create_post(module, user)
    group_id, _posts = create_carousel(module, user)

    single_response = client.get(f"/edit-post/{post.id}")
    carousel_response = client.get(f"/edit-carousel/{group_id}")

    assert single_response.status_code == 302
    assert "/login" in single_response.location
    assert carousel_response.status_code == 302
    assert "/login" in carousel_response.location


def test_owner_can_open_single_edit_form(client, app, module):
    user = create_user(module)
    post = create_post(module, user, platforms="instagram,pinterest")
    post.caption = "Original caption"
    post.prompt = "Original prompt"
    module.db.session.commit()
    login(client, user)

    with captured_templates(app) as templates:
        response = client.get(f"/edit-post/{post.id}")

    assert response.status_code == 200
    assert templates[0][0] == "edit_post.html"
    assert templates[0][1]["post"].id == post.id


def test_single_edit_post_updates_existing_post_and_redirects(client, module):
    user = create_user(module)
    post = create_post(module, user, status="scheduled", platforms="instagram")
    post.caption = "Old caption"
    post.prompt = "Old prompt"
    original_file_url = post.file_url
    original_status = post.status
    module.db.session.commit()
    login(client, user)

    response = client.post(
        f"/edit-post/{post.id}",
        data={
            "caption": "  New caption  ",
            "prompt": "  New prompt  ",
            "platforms": ["facebook", "pinterest"],
        },
    )
    updated = module.db.session.get(Post, post.id)

    assert response.status_code == 302
    assert response.location.endswith(f"/post/{post.id}")
    assert updated.caption == "New caption"
    assert updated.prompt == "New prompt"
    assert updated.platforms == "facebook,pinterest"
    assert updated.file_url == original_file_url
    assert updated.status == original_status


def test_single_edit_defaults_platforms_when_none_selected(client, module):
    user = create_user(module)
    post = create_post(module, user, platforms="pinterest")
    login(client, user)

    client.post(
        f"/edit-post/{post.id}",
        data={
            "caption": "Caption",
            "prompt": "Prompt",
        },
    )
    updated = module.db.session.get(Post, post.id)

    assert updated.platforms == "instagram,facebook"


def test_single_edit_regeneration_uses_helper_bridge(client, module, monkeypatch):
    user = create_user(module)
    post = create_post(module, user, file_url="https://cdn.test/original.jpg")
    calls = []

    def fake_generate(prompt):
        calls.append(prompt)
        return "https://cdn.test/regenerated.jpg"

    monkeypatch.setitem(
        module.app.extensions["smu_post_edit_helpers"],
        "generate_openai_image",
        fake_generate,
    )
    login(client, user)

    response = client.post(
        f"/edit-post/{post.id}",
        data={
            "caption": "Caption",
            "prompt": "  Better image prompt  ",
            "platforms": ["instagram"],
            "regenerate_image": "on",
        },
    )
    updated = module.db.session.get(Post, post.id)

    assert response.status_code == 302
    assert calls == ["Better image prompt"]
    assert updated.file_url == "https://cdn.test/regenerated.jpg"
    assert updated.file_type == "image"


def test_single_edit_rejects_another_users_post(client, module):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    post = create_post(module, owner)
    original_caption = post.caption
    login(client, other)

    response = client.post(
        f"/edit-post/{post.id}",
        data={
            "caption": "Changed",
            "prompt": "Changed",
            "platforms": ["facebook"],
        },
    )
    unchanged = module.db.session.get(Post, post.id)

    assert response.status_code == 302
    assert response.location.endswith("/")
    assert unchanged.caption == original_caption


def test_single_edit_redirects_carousel_posts_to_view(client, module):
    user = create_user(module)
    _group_id, posts = create_carousel(module, user)
    login(client, user)

    response = client.get(f"/edit-post/{posts[0].id}")

    assert response.status_code == 302
    assert response.location.endswith(f"/post/{posts[0].id}")


def test_missing_single_edit_returns_404(client, module):
    user = create_user(module)
    login(client, user)

    response = client.get("/edit-post/999")

    assert response.status_code == 404


def test_owner_can_open_carousel_edit_form(client, app, module):
    user = create_user(module)
    group_id, posts = create_carousel(module, user)
    login(client, user)

    with captured_templates(app) as templates:
        response = client.get(f"/edit-carousel/{group_id}")

    assert response.status_code == 200
    assert templates[0][0] == "edit_carousel.html"
    assert [post.id for post in templates[0][1]["posts"]] == [post.id for post in posts]
    assert templates[0][1]["carousel"].id == posts[0].id


def test_carousel_edit_updates_caption_platforms_order_and_cover(client, module):
    user = create_user(module)
    group_id, posts = create_carousel(module, user)
    first_id = posts[0].id
    second_id = posts[1].id
    login(client, user)

    response = client.post(
        f"/edit-carousel/{group_id}",
        data={
            "caption": "  Carousel caption  ",
            "platforms": ["instagram", "facebook"],
            "carousel_order": json.dumps([second_id, first_id]),
            "cover_post_id": str(second_id),
        },
    )
    first = module.db.session.get(Post, first_id)
    second = module.db.session.get(Post, second_id)

    assert response.status_code == 302
    assert response.location.endswith(f"/post/{first_id}")
    assert first.caption == "Carousel caption"
    assert second.caption == "Carousel caption"
    assert first.platforms == "instagram,facebook"
    assert second.platforms == "instagram,facebook"
    assert first.sort_order == 1
    assert second.sort_order == 0
    assert first.is_cover is False
    assert second.is_cover is True


def test_carousel_edit_defaults_platforms_when_none_selected(client, module):
    user = create_user(module)
    group_id, posts = create_carousel(module, user)
    login(client, user)

    client.post(
        f"/edit-carousel/{group_id}",
        data={
            "caption": "Caption",
            "carousel_order": json.dumps([post.id for post in posts]),
            "cover_post_id": str(posts[0].id),
        },
    )
    updated = Post.query.filter_by(group_id=group_id).order_by(Post.id).all()

    assert {post.platforms for post in updated} == {"instagram,facebook"}


def test_carousel_edit_rejects_another_users_group(client, module):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    group_id, posts = create_carousel(module, owner)
    original = [(post.id, post.caption, post.sort_order, post.is_cover) for post in posts]
    login(client, other)

    response = client.post(
        f"/edit-carousel/{group_id}",
        data={
            "caption": "Changed",
            "platforms": ["facebook"],
            "carousel_order": json.dumps([post.id for post in posts]),
            "cover_post_id": str(posts[1].id),
        },
    )
    unchanged = [
        (post.id, post.caption, post.sort_order, post.is_cover)
        for post in Post.query.filter_by(group_id=group_id).order_by(Post.id).all()
    ]

    assert response.status_code == 302
    assert response.location.endswith("/")
    assert unchanged == original


def test_missing_carousel_edit_redirects_to_dashboard(client, module):
    user = create_user(module)
    login(client, user)

    response = client.get("/edit-carousel/missing-group")

    assert response.status_code == 302
    assert response.location.endswith("/")


def test_post_edit_helper_bridge_and_model_compatibility(module):
    helpers = module.app.extensions["smu_post_edit_helpers"]

    assert callable(helpers["get_ordered_carousel_posts"])
    assert callable(helpers["generate_openai_image"])
    assert smu_app.Post is Post


def test_unrelated_post_endpoints_remain_registered(module):
    expected = {
        "/create": "create_post",
        "/rewrite-caption/<int:post_id>": "rewrite_caption",
        "/rewrite-carousel-caption/<group_id>": "rewrite_carousel_caption",
        "/duplicate-post/<int:post_id>": "duplicate_post",
        "/duplicate-carousel/<group_id>": "duplicate_carousel",
        "/send/<int:post_id>": "send_to_make",
        "/send-carousel/<group_id>": "send_carousel_to_make",
        "/schedule/<int:post_id>": "schedule_post",
        "/delete/<int:post_id>": "delete_post",
        "/delete-carousel/<group_id>": "delete_carousel",
        "/post/<int:post_id>/use-improved": "use_improved_caption",
        "/post/<int:post_id>/custom-caption": "use_custom_caption",
        "/post/<int:post_id>/discard-improved": "discard_improved_caption",
        "/post/<int:post_id>/ai-editor": "ai_editor",
        "/post/<int:post_id>/studio": "post_studio",
        "/post/<int:post_id>/studio/action/<action>": "studio_action",
        "/post/<int:post_id>/studio/regrade": "studio_regrade",
    }

    for path, endpoint in expected.items():
        rules = rules_for(module.app, path)

        assert len(rules) == 1
        assert rules[0].endpoint == endpoint
