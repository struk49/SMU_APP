from contextlib import contextmanager
from datetime import datetime

from flask import template_rendered, url_for

import app as smu_app
from conftest import create_post, create_user, login
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


def test_posts_blueprint_is_registered_once(module):
    assert "posts" in module.app.blueprints
    assert list(module.app.blueprints).count("posts") == 1


def test_post_detail_route_preserves_endpoint_and_methods(module):
    rules = rules_for(module.app, "/post/<int:post_id>")

    assert len(rules) == 1
    assert rules[0].endpoint == "view_post"
    assert "GET" in rules[0].methods


def test_post_detail_url_for_compatibility(module):
    with module.app.test_request_context():
        assert url_for("view_post", post_id=1) == "/post/1"


def test_post_detail_requires_login(client, module):
    user = create_user(module)
    post = create_post(module, user)

    response = client.get(f"/post/{post.id}")

    assert response.status_code == 302
    assert "/login" in response.location


def test_owner_can_view_single_post_with_same_template_context(client, app, module):
    user = create_user(module)
    post = create_post(
        module,
        user,
        status="scheduled",
        platforms="instagram,pinterest",
        file_url="https://cdn.test/single.jpg",
    )
    post.caption = "Single caption"
    post.prompt = "Single prompt"
    post.scheduled_time = datetime(2026, 7, 10, 8, 0)
    module.db.session.commit()
    login(client, user)

    with captured_templates(app) as templates:
        response = client.get(f"/post/{post.id}")
    template_name, context = templates[0]
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert template_name == "view_post.html"
    assert context["post"] is post
    assert context["carousel_posts"] == []
    assert "Single caption" in html
    assert "Single prompt" in html
    assert "Instagram" in html
    assert "Pinterest" in html
    assert "Scheduled" in html
    assert "https://cdn.test/single.jpg" in html


def test_another_user_is_redirected_from_post_detail(client, module):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    post = create_post(module, other)
    login(client, owner)

    response = client.get(f"/post/{post.id}")

    assert response.status_code == 302
    assert response.location.endswith("/")


def test_missing_post_preserves_404_behaviour(client, module):
    user = create_user(module)
    login(client, user)

    response = client.get("/post/999")

    assert response.status_code == 404


def test_carousel_post_loads_current_user_group_in_existing_order(
    client, app, module
):
    user = create_user(module)
    group_id = "detail-carousel"
    cover = create_post(
        module,
        user,
        group_id=group_id,
        sort_order=0,
        is_cover=True,
        file_url="https://cdn.test/cover.jpg",
    )
    child = create_post(
        module,
        user,
        group_id=group_id,
        sort_order=1,
        is_cover=False,
        file_url="https://cdn.test/child.jpg",
    )
    login(client, user)

    with captured_templates(app) as templates:
        response = client.get(f"/post/{child.id}")
    context = templates[0][1]
    carousel_posts = context["carousel_posts"]
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert context["post"] is child
    assert [post.id for post in carousel_posts] == [cover.id, child.id]
    assert [post.is_cover for post in carousel_posts] == [True, False]
    assert "Carousel · 2 images" in html
    assert "Cover" in html
    assert "https://cdn.test/cover.jpg" in html
    assert "https://cdn.test/child.jpg" in html


def test_carousel_group_lookup_is_user_scoped(client, app, module):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    group_id = "shared-looking-group"
    owner_post = create_post(
        module,
        owner,
        group_id=group_id,
        sort_order=0,
        is_cover=True,
        file_url="https://cdn.test/owner.jpg",
    )
    other_post = create_post(
        module,
        other,
        group_id=group_id,
        sort_order=1,
        file_url="https://cdn.test/other.jpg",
    )
    login(client, owner)

    with captured_templates(app) as templates:
        response = client.get(f"/post/{owner_post.id}")
    carousel_posts = templates[0][1]["carousel_posts"]

    assert response.status_code == 200
    assert [post.id for post in carousel_posts] == [owner_post.id]
    assert other_post.id not in [post.id for post in carousel_posts]


def test_incomplete_carousel_group_preserves_existing_rendering(client, app, module):
    user = create_user(module)
    post = create_post(
        module,
        user,
        group_id="single-item-carousel",
        sort_order=0,
        is_cover=True,
    )
    login(client, user)

    with captured_templates(app) as templates:
        response = client.get(f"/post/{post.id}")

    assert response.status_code == 200
    assert templates[0][1]["carousel_posts"] == [post]


def test_post_detail_helper_bridges_and_existing_bridges_remain(module):
    extension_keys = {
        key for key in module.app.extensions if key.startswith("smu_")
    }

    assert {
        "smu_post_detail_helpers",
        "smu_dashboard_helpers",
        "smu_calendar_helpers",
        "smu_tiktok_helpers",
        "smu_content_pack_helpers",
    }.issubset(extension_keys)
    assert callable(
        module.app.extensions["smu_post_detail_helpers"][
            "get_ordered_carousel_posts"
        ]
    )


def test_post_detail_model_and_app_import_compatibility_remain(module):
    assert smu_app.Post is Post
    assert module.Post is Post


def test_post_management_and_unrelated_endpoints_remain_registered(module):
    endpoints = {rule.endpoint for rule in module.app.url_map.iter_rules()}

    assert {
        "index",
        "create_post",
        "edit_post",
        "edit_carousel",
        "delete_post",
        "delete_carousel",
        "duplicate_post",
        "duplicate_carousel",
        "schedule_post",
        "send_to_make",
        "send_carousel_to_make",
        "post_studio",
        "landing_page",
        "register",
        "login",
        "brand_brief",
        "connected_accounts",
        "content_pack",
        "tiktok_repurpose",
        "calendar_view",
    }.issubset(endpoints)


def test_scheduler_and_publishing_references_are_untouched(module):
    assert module.scheduler is smu_app.scheduler
    assert module.publish_post_to_make is smu_app.publish_post_to_make
