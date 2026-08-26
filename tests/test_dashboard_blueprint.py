from contextlib import contextmanager
from datetime import timedelta

from flask import template_rendered, url_for

import app as smu_app
from conftest import create_post, create_user, login
from smu_core.models import BrandBrief, ConnectedAccount, Post
from smu_core.services.time_utils import utc_now


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


def test_dashboard_blueprint_is_registered_once(module):
    assert "dashboard" in module.app.blueprints
    assert list(module.app.blueprints).count("dashboard") == 1


def test_dashboard_route_preserves_endpoint_and_methods(module):
    rules = rules_for(module.app, "/")

    assert len(rules) == 1
    assert rules[0].endpoint == "index"
    assert "GET" in rules[0].methods


def test_dashboard_url_for_compatibility(module):
    with module.app.test_request_context():
        assert url_for("index") == "/"


def test_dashboard_root_renders_public_landing_for_anonymous_users(client):
    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Turn one idea into content for every platform." in html
    assert "Get Started" in html


def test_dashboard_renders_same_template_and_context_keys(client, app, module):
    user = create_user(module)
    login(client, user)

    with captured_templates(app) as templates:
        response = client.get("/")

    template_name, context = templates[0]

    assert response.status_code == 200
    assert template_name == "index.html"
    assert {
        "posts",
        "status_filter",
        "type_filter",
        "platform_filter",
        "search_query",
        "stats",
        "onboarding",
        "connected_platforms",
    }.issubset(context)
    assert context["status_filter"] == "all"
    assert context["type_filter"] == "all"
    assert context["platform_filter"] == "all"
    assert context["search_query"] == ""
    assert "No posts yet" in response.get_data(as_text=True)


def test_authenticated_navbar_uses_horizontal_logo_and_grouped_links(client, module):
    user = create_user(module)
    login(client, user)

    with module.app.test_request_context():
        accounts_path = url_for("connected_accounts")

    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "smu-horizontal-logo.png" in html
    assert "SMU Create Edit Publish" in html
    assert "navbar navbar-expand-xl" in html
    assert "container-fluid px-3 px-lg-4" in html
    assert "navbar-nav app-nav-links ms-xl-auto" in html
    assert 'data-bs-target="#primaryNavbar"' in html
    assert 'aria-controls="primaryNavbar"' in html
    assert "Dashboard" in html
    assert "Create" in html
    assert "Content" in html
    assert "Publish" in html
    assert "TikTok" in html
    assert "Content Pack" in html
    assert "Brand Brief" in html
    assert "Calendar" in html
    assert "Accounts" in html
    assert "Account" in html
    assert user.email in html
    assert "Logout" in html
    assert 'href="/tiktok"' in html
    assert 'href="/content-pack"' in html
    assert 'href="/brand-brief"' in html
    assert 'href="/calendar"' in html
    assert f'href="{accounts_path}"' in html


def test_dashboard_only_shows_current_user_posts(client, app, module):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    owner_post = create_post(module, owner, file_url="https://cdn.test/owner.jpg")
    other_post = create_post(module, other, file_url="https://cdn.test/other.jpg")
    login(client, owner)

    with captured_templates(app) as templates:
        response = client.get("/")
    posts = templates[0][1]["posts"]

    assert response.status_code == 200
    assert [post.id for post in posts] == [owner_post.id]
    assert other_post.id not in [post.id for post in posts]
    assert "https://cdn.test/owner.jpg" in response.get_data(as_text=True)
    assert "https://cdn.test/other.jpg" not in response.get_data(as_text=True)


def test_dashboard_filters_preserve_existing_behaviour(client, app, module):
    user = create_user(module)
    expected = create_post(
        module,
        user,
        status="sent_to_make",
        platforms="instagram,pinterest",
        file_url="https://cdn.test/expected.jpg",
    )
    expected.caption = "Needle caption"
    expected.prompt = "Prompt"
    create_post(
        module,
        user,
        status="draft",
        platforms="facebook",
        file_url="https://cdn.test/other.jpg",
    )
    module.db.session.commit()
    login(client, user)

    with captured_templates(app) as templates:
        response = client.get(
            "/?status=sent_to_make&type=single&platform=pinterest&q=Needle"
        )
    posts = templates[0][1]["posts"]
    context = templates[0][1]

    assert response.status_code == 200
    assert [post.id for post in posts] == [expected.id]
    assert context["status_filter"] == "sent_to_make"
    assert context["type_filter"] == "single"
    assert context["platform_filter"] == "pinterest"
    assert context["search_query"] == "Needle"


def test_dashboard_invalid_filter_values_preserve_empty_result(client, app, module):
    user = create_user(module)
    create_post(module, user, status="draft")
    login(client, user)

    with captured_templates(app) as templates:
        response = client.get("/?status=unknown-status")

    assert response.status_code == 200
    assert templates[0][1]["status_filter"] == "unknown-status"
    assert templates[0][1]["posts"] == []


def test_dashboard_ordering_and_carousel_template_grouping(client, app, module):
    user = create_user(module)
    older = create_post(
        module,
        user,
        status="draft",
        file_url="https://cdn.test/older.jpg",
    )
    newer = create_post(
        module,
        user,
        status="draft",
        file_url="https://cdn.test/newer.jpg",
    )
    group_id = "dashboard-carousel"
    cover = create_post(
        module,
        user,
        status="draft",
        group_id=group_id,
        sort_order=0,
        is_cover=True,
        file_url="https://cdn.test/cover.jpg",
    )
    child = create_post(
        module,
        user,
        status="draft",
        group_id=group_id,
        sort_order=1,
        file_url="https://cdn.test/child.jpg",
    )
    older.created_at = utc_now() - timedelta(days=2)
    newer.created_at = utc_now()
    cover.created_at = utc_now() - timedelta(days=1)
    child.created_at = cover.created_at
    module.db.session.commit()
    login(client, user)

    with captured_templates(app) as templates:
        response = client.get("/")
    posts = templates[0][1]["posts"]
    html = response.get_data(as_text=True)

    assert [post.id for post in posts] == [newer.id, cover.id, child.id, older.id]
    assert html.count("Carousel · 2") == 1


def test_dashboard_statistics_are_preserved(client, app, module):
    user = create_user(module)
    create_post(module, user, status="draft")
    create_post(module, user, status="scheduled")
    create_post(module, user, status="sent_to_make")
    create_post(module, user, group_id="stats-carousel", sort_order=0, is_cover=True)
    create_post(module, user, group_id="stats-carousel", sort_order=1)
    login(client, user)

    with captured_templates(app) as templates:
        response = client.get("/")

    assert response.status_code == 200
    assert templates[0][1]["stats"] == {
        "total": 5,
        "drafts": 3,
        "scheduled": 1,
        "sent": 1,
        "carousels": 2,
    }


def test_dashboard_onboarding_and_connected_platforms_are_preserved(
    client, app, module
):
    user = create_user(module)
    module.db.session.add(
        module.BrandBrief(user_id=user.id, business_name="SMU", niche="Social")
    )
    module.db.session.add(
        module.ConnectedAccount(
            user_id=user.id,
            instagram_connected=True,
            facebook_connected=False,
            pinterest_connected=True,
            linkedin_connected=False,
        )
    )
    create_post(module, user, status="sent_to_make")
    module.db.session.commit()
    login(client, user)

    with client.session_transaction() as session:
        session["content_pack_started"] = True
    with captured_templates(app) as templates:
        response = client.get("/")
    context = templates[0][1]

    assert response.status_code == 200
    assert [item["label"] for item in context["onboarding"]["items"]] == [
        "Brand Brief",
        "Content Pack",
        "First Post",
        "Scheduled Post",
        "Calendar Viewed",
        "First Published Post",
    ]
    assert context["connected_platforms"] == [
        {"name": "Instagram", "connected": True},
        {"name": "Facebook", "connected": False},
        {"name": "Pinterest", "connected": True},
        {"name": "LinkedIn", "connected": False},
    ]


def test_dashboard_helper_bridges_and_existing_bridges_remain(module):
    extension_keys = {
        key for key in module.app.extensions if key.startswith("smu_")
    }

    assert {
        "smu_dashboard_helpers",
        "smu_calendar_helpers",
        "smu_tiktok_helpers",
        "smu_content_pack_helpers",
    }.issubset(extension_keys)
    assert callable(
        module.app.extensions["smu_dashboard_helpers"]["build_onboarding_progress"]
    )
    assert callable(
        module.app.extensions["smu_dashboard_helpers"][
            "build_connected_platform_cards"
        ]
    )


def test_dashboard_model_and_app_import_compatibility_remain(module):
    assert smu_app.Post is Post
    assert smu_app.BrandBrief is BrandBrief
    assert smu_app.ConnectedAccount is ConnectedAccount
    assert module.Post is Post
    assert module.BrandBrief is BrandBrief
    assert module.ConnectedAccount is ConnectedAccount


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
        "tiktok_repurpose",
        "calendar_view",
        "calendar_events",
        "create_post",
        "post_studio",
        "send_to_make",
    }.issubset(endpoints)


def test_scheduler_reference_is_untouched(module):
    assert module.scheduler is smu_app.scheduler
