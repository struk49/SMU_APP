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


def test_calendar_blueprint_is_registered_once(module):
    assert "calendar" in module.app.blueprints
    assert list(module.app.blueprints).count("calendar") == 1


def test_calendar_routes_preserve_old_endpoints_and_methods(module):
    expected = {
        "/calendar": ("calendar_view", {"GET"}),
        "/calendar/events": ("calendar_events", {"GET"}),
        "/calendar/summary": ("calendar_summary", {"GET"}),
        "/calendar/events/<int:post_id>/reschedule": (
            "calendar_reschedule_event",
            {"POST"},
        ),
        "/calendar/events/<int:post_id>/duplicate": (
            "calendar_duplicate_event",
            {"POST"},
        ),
    }

    for path, (endpoint, methods) in expected.items():
        rules = rules_for(module.app, path)

        assert len(rules) == 1
        assert rules[0].endpoint == endpoint
        assert methods.issubset(rules[0].methods)


def test_calendar_url_for_compatibility(module):
    with module.app.test_request_context():
        assert url_for("calendar_view") == "/calendar"
        assert url_for("calendar_events") == "/calendar/events"
        assert url_for("calendar_summary") == "/calendar/summary"
        assert url_for("calendar_reschedule_event", post_id=1) == (
            "/calendar/events/1/reschedule"
        )
        assert url_for("calendar_duplicate_event", post_id=1) == (
            "/calendar/events/1/duplicate"
        )


def test_calendar_logged_out_requests_preserve_redirects(client):
    responses = [
        client.get("/calendar"),
        client.get("/calendar/events?start=2026-07-01&end=2026-08-01"),
        client.get("/calendar/summary?start=2026-07-01&end=2026-08-01"),
        client.post("/calendar/events/1/reschedule", json={"date": "2026-07-12"}),
        client.post("/calendar/events/1/duplicate"),
    ]

    for response in responses:
        assert response.status_code == 302
        assert "/login" in response.location


def test_calendar_page_renders_same_template_and_sets_viewed_session(
    client, app, module
):
    user = create_user(module)
    login(client, user)

    with captured_templates(app) as templates:
        response = client.get("/calendar")

    assert response.status_code == 200
    assert templates[0][0] == "calendar.html"
    assert "calendar" in response.get_data(as_text=True).lower()
    with client.session_transaction() as session:
        assert session["calendar_viewed"] is True


def test_calendar_events_json_shape_filters_and_carousel_dedup(client, module):
    user = create_user(module)
    other = create_user(module, email="other@example.com")
    expected = create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 10, 8, 0),
        status="sent_to_make",
        platforms="instagram,pinterest",
    )
    group_id = "calendar-blueprint-group"
    cover = create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 11, 8, 0),
        status="scheduled",
        platforms="instagram",
        group_id=group_id,
        sort_order=0,
        is_cover=True,
    )
    create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 11, 8, 0),
        status="scheduled",
        platforms="instagram",
        group_id=group_id,
        sort_order=1,
    )
    create_post(
        module,
        other,
        scheduled_time=datetime(2026, 7, 10, 8, 0),
        status="sent_to_make",
        platforms="instagram",
    )
    login(client, user)

    filtered_response = client.get(
        "/calendar/events?start=2026-07-01T00:00:00+00:00"
        "&end=2026-08-01T00:00:00+00:00"
        "&platform=pinterest&status=published"
    )
    all_response = client.get(
        "/calendar/events?start=2026-07-01T00:00:00+00:00"
        "&end=2026-08-01T00:00:00+00:00"
    )
    filtered_events = filtered_response.get_json()
    all_events = all_response.get_json()

    assert filtered_response.status_code == 200
    assert len(filtered_events) == 1
    assert filtered_events[0] == {
        "id": expected.id,
        "title": "09:00 Instagram · Single",
        "start": "2026-07-10T09:00:00+01:00",
        "status": "published",
        "status_label": "Published",
        "post_type": "single",
        "post_type_label": "Single",
        "platforms": ["instagram", "pinterest"],
        "platform_label": "Instagram",
        "detail_url": f"/post/{expected.id}",
        "backgroundColor": "#198754",
        "borderColor": "#198754",
        "textColor": "#ffffff",
        "tooltip": {
            "title": "Caption",
            "platform": "Instagram",
            "post_type": "Single",
            "status": "Published",
            "scheduled_time": "10 Jul 2026 09:00",
        },
    }
    assert [event["id"] for event in all_events] == [expected.id, cover.id]
    assert all_events[1]["post_type"] == "carousel"


def test_calendar_events_invalid_range_behaviour(client, module):
    user = create_user(module)
    login(client, user)

    bad_date = client.get("/calendar/events?start=bad&end=2026-08-01")
    reversed_range = client.get(
        "/calendar/events?start=2026-08-01T00:00:00+00:00"
        "&end=2026-07-01T00:00:00+00:00"
    )

    assert bad_date.status_code == 400
    assert bad_date.get_json() == {"error": "Invalid start or end date."}
    assert reversed_range.status_code == 400
    assert reversed_range.get_json() == {"error": "Invalid start or end date."}


def test_calendar_summary_json_counts_and_ownership(client, module):
    user = create_user(module)
    other = create_user(module, email="other@example.com")
    create_post(module, user, scheduled_time=datetime(2026, 7, 10, 8), status="draft")
    create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 11, 8),
        status="scheduled",
    )
    create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 12, 8),
        status="sent_to_make",
    )
    create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 13, 8),
        status="generation_failed",
    )
    create_post(
        module,
        other,
        scheduled_time=datetime(2026, 7, 10, 8),
        status="scheduled",
    )
    login(client, user)

    response = client.get(
        "/calendar/summary?start=2026-07-01T00:00:00+00:00"
        "&end=2026-08-01T00:00:00+00:00"
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "scheduled": 1,
        "published": 1,
        "draft": 1,
        "failed": 1,
    }


def test_calendar_reschedule_single_and_rejects_other_user(client, module):
    user = create_user(module)
    other = create_user(module, email="other@example.com")
    own_post = create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 10, 8),
        status="scheduled",
    )
    other_post = create_post(
        module,
        other,
        scheduled_time=datetime(2026, 7, 10, 8),
        status="scheduled",
    )
    login(client, user)

    response = client.post(
        f"/calendar/events/{own_post.id}/reschedule",
        json={"date": "2026-07-12"},
    )
    rejected = client.post(
        f"/calendar/events/{other_post.id}/reschedule",
        json={"date": "2026-07-12"},
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert module.db.session.get(module.Post, own_post.id).scheduled_time == (
        datetime(2026, 7, 12, 8)
    )
    assert rejected.status_code == 404
    assert module.db.session.get(module.Post, other_post.id).scheduled_time == (
        datetime(2026, 7, 10, 8)
    )


def test_calendar_reschedule_carousel_updates_every_group_item(client, module):
    user = create_user(module)
    group_id = "calendar-reschedule-group"
    cover = create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 10, 8),
        status="scheduled",
        group_id=group_id,
        sort_order=0,
        is_cover=True,
    )
    child = create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 10, 8),
        status="scheduled",
        group_id=group_id,
        sort_order=1,
    )
    login(client, user)

    response = client.post(
        f"/calendar/events/{child.id}/reschedule",
        json={"date": "2026-07-12"},
    )

    assert response.status_code == 200
    assert module.db.session.get(module.Post, cover.id).scheduled_time == (
        datetime(2026, 7, 12, 8)
    )
    assert module.db.session.get(module.Post, child.id).scheduled_time == (
        datetime(2026, 7, 12, 8)
    )


def test_calendar_reschedule_invalid_json_preserves_response(client, module):
    user = create_user(module)
    post = create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 10, 8),
        status="scheduled",
    )
    login(client, user)

    response = client.post(
        f"/calendar/events/{post.id}/reschedule",
        json={"date": "not-a-date"},
    )

    assert response.status_code == 400
    assert "time data" in response.get_json()["error"]


def test_calendar_duplicate_single_and_carousel_behaviour(client, module):
    user = create_user(module)
    single = create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 10, 8),
        status="sent_to_make",
        platforms="instagram,pinterest",
        file_url="https://cdn.test/single.jpg",
    )
    single.sent_at = datetime(2026, 7, 10, 9)
    group_id = "calendar-duplicate-group"
    cover = create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 11, 8),
        status="sent_to_make",
        group_id=group_id,
        sort_order=0,
        is_cover=True,
    )
    child = create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 11, 8),
        status="sent_to_make",
        group_id=group_id,
        sort_order=1,
    )
    module.db.session.commit()
    login(client, user)

    single_response = client.post(f"/calendar/events/{single.id}/duplicate")
    carousel_response = client.post(f"/calendar/events/{cover.id}/duplicate")
    single_copy = module.db.session.get(module.Post, single_response.get_json()["post_id"])
    carousel_copy = module.db.session.get(
        module.Post,
        carousel_response.get_json()["post_id"],
    )
    copied_group = module.Post.query.filter_by(group_id=carousel_copy.group_id).order_by(
        module.Post.sort_order.asc()
    ).all()

    assert single_response.status_code == 200
    assert single_copy.status == "draft"
    assert single_copy.platforms == single.platforms
    assert single_copy.scheduled_time is None
    assert single_copy.sent_at is None
    assert carousel_response.status_code == 200
    assert carousel_copy.group_id != group_id
    assert len(copied_group) == 2
    assert [post.sort_order for post in copied_group] == [
        cover.sort_order,
        child.sort_order,
    ]
    assert [post.is_cover for post in copied_group] == [True, False]
    assert {post.status for post in copied_group} == {"draft"}
    assert {post.scheduled_time for post in copied_group} == {None}
    assert {post.sent_at for post in copied_group} == {None}


def test_calendar_duplicate_rejects_other_user(client, module):
    user = create_user(module)
    other = create_user(module, email="other@example.com")
    other_post = create_post(
        module,
        other,
        scheduled_time=datetime(2026, 7, 10, 8),
        status="scheduled",
    )
    login(client, user)

    response = client.post(f"/calendar/events/{other_post.id}/duplicate")

    assert response.status_code == 404
    assert module.Post.query.count() == 1


def test_calendar_model_and_app_import_compatibility_remain(module):
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
        "tiktok_repurpose",
        "index",
        "post_studio",
        "send_to_make",
    }.issubset(endpoints)


def test_scheduler_reference_is_untouched(module):
    assert module.scheduler is smu_app.scheduler
