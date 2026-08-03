from datetime import datetime

from conftest import create_post, create_user, login


def test_calendar_page_requires_login(client):
    response = client.get("/calendar")

    assert response.status_code == 302
    assert "/login" in response.location


def test_calendar_events_require_login(client):
    response = client.get(
        "/calendar/events?start=2026-07-01T00:00:00+00:00&end=2026-08-01T00:00:00+00:00"
    )

    assert response.status_code == 302
    assert "/login" in response.location


def test_calendar_events_do_not_expose_other_users_posts(client, module):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    create_post(
        module,
        owner,
        scheduled_time=datetime(2026, 7, 10, 8, 0),
        status="scheduled",
    )
    create_post(
        module,
        other,
        scheduled_time=datetime(2026, 7, 10, 9, 0),
        status="scheduled",
    )
    login(client, owner)

    response = client.get(
        "/calendar/events?start=2026-07-01T00:00:00+00:00&end=2026-08-01T00:00:00+00:00"
    )
    events = response.get_json()

    assert response.status_code == 200
    assert len(events) == 1
    assert events[0]["detail_url"] == "/post/1"


def test_scheduled_posts_inside_range_are_returned(client, module):
    user = create_user(module)
    post = create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 10, 8, 0),
        status="scheduled",
        platforms="instagram",
    )
    login(client, user)

    response = client.get(
        "/calendar/events?start=2026-07-01T00:00:00+00:00&end=2026-08-01T00:00:00+00:00"
    )
    events = response.get_json()

    assert response.status_code == 200
    assert events == [
        {
            "id": post.id,
            "title": "09:00 Instagram · Single",
            "start": "2026-07-10T09:00:00+01:00",
            "status": "scheduled",
            "status_label": "Scheduled",
            "post_type": "single",
            "post_type_label": "Single",
            "platforms": ["instagram"],
            "platform_label": "Instagram",
            "detail_url": f"/post/{post.id}",
            "backgroundColor": "#0d6efd",
            "borderColor": "#0d6efd",
            "textColor": "#ffffff",
            "tooltip": {
                "title": "Caption",
                "platform": "Instagram",
                "post_type": "Single",
                "status": "Scheduled",
                "scheduled_time": "10 Jul 2026 09:00",
            },
        }
    ]


def test_posts_outside_range_are_excluded(client, module):
    user = create_user(module)
    create_post(
        module,
        user,
        scheduled_time=datetime(2026, 8, 1, 0, 0),
        status="scheduled",
    )
    login(client, user)

    response = client.get(
        "/calendar/events?start=2026-07-01T00:00:00+00:00&end=2026-08-01T00:00:00+00:00"
    )

    assert response.status_code == 200
    assert response.get_json() == []


def test_posts_without_scheduled_time_are_excluded(client, module):
    user = create_user(module)
    create_post(module, user, status="draft")
    login(client, user)

    response = client.get(
        "/calendar/events?start=2026-07-01T00:00:00+00:00&end=2026-08-01T00:00:00+00:00"
    )

    assert response.status_code == 200
    assert response.get_json() == []


def test_invalid_calendar_date_parameters_return_400(client, module):
    user = create_user(module)
    login(client, user)

    response = client.get("/calendar/events?start=not-a-date&end=2026-08-01")

    assert response.status_code == 400
    assert response.get_json()["error"] == "Invalid start or end date."


def test_single_image_posts_appear_once(client, module):
    user = create_user(module)
    create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 10, 8, 0),
        status="scheduled",
    )
    login(client, user)

    response = client.get(
        "/calendar/events?start=2026-07-01T00:00:00+00:00&end=2026-08-01T00:00:00+00:00"
    )

    assert response.status_code == 200
    assert len(response.get_json()) == 1


def test_one_carousel_group_appears_once(client, module):
    user = create_user(module)
    group_id = "carousel-one"
    cover = create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 10, 8, 0),
        status="scheduled",
        group_id=group_id,
        sort_order=0,
        is_cover=True,
    )
    create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 10, 8, 0),
        status="scheduled",
        group_id=group_id,
        sort_order=1,
    )
    login(client, user)

    response = client.get(
        "/calendar/events?start=2026-07-01T00:00:00+00:00&end=2026-08-01T00:00:00+00:00"
    )
    events = response.get_json()

    assert response.status_code == 200
    assert len(events) == 1
    assert events[0]["id"] == cover.id
    assert events[0]["post_type"] == "carousel"
    assert events[0]["title"] == "09:00 Instagram · Carousel"


def test_calendar_detail_urls_use_existing_post_detail_route(client, module):
    user = create_user(module)
    post = create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 10, 8, 0),
        status="scheduled",
    )
    login(client, user)

    response = client.get(
        "/calendar/events?start=2026-07-01T00:00:00+00:00&end=2026-08-01T00:00:00+00:00"
    )

    assert response.status_code == 200
    assert response.get_json()[0]["detail_url"] == f"/post/{post.id}"


def test_calendar_summary_counts_current_month(client, module):
    user = create_user(module)
    group_id = "summary-carousel"
    create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 10, 8, 0),
        status="scheduled",
    )
    create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 11, 8, 0),
        status="sent_to_make",
    )
    create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 12, 8, 0),
        status="draft",
    )
    create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 13, 8, 0),
        status="schedule_failed",
    )
    create_post(
        module,
        user,
        scheduled_time=datetime(2026, 8, 1, 8, 0),
        status="scheduled",
    )
    create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 14, 8, 0),
        status="scheduled",
        group_id=group_id,
        sort_order=0,
        is_cover=True,
    )
    create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 14, 8, 0),
        status="scheduled",
        group_id=group_id,
        sort_order=1,
    )
    login(client, user)

    response = client.get(
        "/calendar/summary?start=2026-07-01T00:00:00+00:00&end=2026-08-01T00:00:00+00:00"
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "scheduled": 2,
        "published": 1,
        "draft": 1,
        "failed": 1,
    }


def test_calendar_summary_rejects_invalid_dates(client, module):
    user = create_user(module)
    login(client, user)

    response = client.get("/calendar/summary?start=bad-date&end=2026-08-01")

    assert response.status_code == 400
    assert response.get_json()["error"] == "Invalid start or end date."


def test_calendar_event_colours_map_by_status(client, module):
    user = create_user(module)
    statuses = [
        ("draft", "#6c757d", "draft"),
        ("scheduled", "#0d6efd", "scheduled"),
        ("sent_to_make", "#198754", "published"),
        ("generation_failed", "#dc3545", "failed"),
    ]

    for index, (status, _, _) in enumerate(statuses, start=1):
        create_post(
            module,
            user,
            scheduled_time=datetime(2026, 7, 10, 8 + index, 0),
            status=status,
        )

    login(client, user)

    response = client.get(
        "/calendar/events?start=2026-07-01T00:00:00+00:00&end=2026-08-01T00:00:00+00:00"
    )
    events = response.get_json()

    assert response.status_code == 200
    assert [
        (event["backgroundColor"], event["borderColor"], event["status"])
        for event in events
    ] == [
        (color, color, status_key)
        for _, color, status_key in statuses
    ]


def test_calendar_platform_filter(client, module):
    user = create_user(module)
    instagram_post = create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 10, 8, 0),
        platforms="instagram",
    )
    create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 10, 9, 0),
        platforms="facebook",
    )
    login(client, user)

    response = client.get(
        "/calendar/events?start=2026-07-01T00:00:00+00:00&end=2026-08-01T00:00:00+00:00&platform=instagram"
    )
    events = response.get_json()

    assert response.status_code == 200
    assert len(events) == 1
    assert events[0]["id"] == instagram_post.id


def test_calendar_status_filter(client, module):
    user = create_user(module)
    draft_post = create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 10, 8, 0),
        status="draft",
    )
    create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 10, 9, 0),
        status="scheduled",
    )
    login(client, user)

    response = client.get(
        "/calendar/events?start=2026-07-01T00:00:00+00:00&end=2026-08-01T00:00:00+00:00&status=draft"
    )
    events = response.get_json()

    assert response.status_code == 200
    assert len(events) == 1
    assert events[0]["id"] == draft_post.id


def test_calendar_platform_and_status_filters_work_together(client, module):
    user = create_user(module)
    expected_post = create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 10, 8, 0),
        status="sent_to_make",
        platforms="pinterest",
    )
    create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 10, 9, 0),
        status="sent_to_make",
        platforms="instagram",
    )
    create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 10, 10, 0),
        status="scheduled",
        platforms="pinterest",
    )
    login(client, user)

    response = client.get(
        "/calendar/events?start=2026-07-01T00:00:00+00:00&end=2026-08-01T00:00:00+00:00&platform=pinterest&status=published"
    )
    events = response.get_json()

    assert response.status_code == 200
    assert len(events) == 1
    assert events[0]["id"] == expected_post.id


def test_calendar_tooltip_data_exists_in_event_payload(client, module):
    user = create_user(module)
    create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 10, 8, 0),
        status="sent_to_make",
        platforms="facebook",
    )
    login(client, user)

    response = client.get(
        "/calendar/events?start=2026-07-01T00:00:00+00:00&end=2026-08-01T00:00:00+00:00"
    )
    tooltip = response.get_json()[0]["tooltip"]

    assert response.status_code == 200
    assert tooltip == {
        "title": "Caption",
        "platform": "Facebook",
        "post_type": "Single",
        "status": "Published",
        "scheduled_time": "10 Jul 2026 09:00",
    }


def test_calendar_drag_update_preserves_time_of_day(client, module):
    user = create_user(module)
    post = create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 10, 8, 0),
        status="scheduled",
    )
    login(client, user)

    response = client.post(
        f"/calendar/events/{post.id}/reschedule",
        json={"date": "2026-07-12"},
    )
    updated_post = module.db.session.get(module.Post, post.id)

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert updated_post.scheduled_time == datetime(2026, 7, 12, 8, 0)
    assert response.get_json()["event"]["start"] == "2026-07-12T09:00:00+01:00"


def test_calendar_reschedule_rejects_other_users_posts(client, module):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    other_post = create_post(
        module,
        other,
        scheduled_time=datetime(2026, 7, 10, 8, 0),
        status="scheduled",
    )
    login(client, owner)

    response = client.post(
        f"/calendar/events/{other_post.id}/reschedule",
        json={"date": "2026-07-12"},
    )
    unchanged_post = module.db.session.get(module.Post, other_post.id)

    assert response.status_code == 404
    assert unchanged_post.scheduled_time == datetime(2026, 7, 10, 8, 0)


def test_calendar_reschedule_requires_login(client, module):
    user = create_user(module)
    post = create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 10, 8, 0),
        status="scheduled",
    )

    response = client.post(
        f"/calendar/events/{post.id}/reschedule",
        json={"date": "2026-07-12"},
    )

    assert response.status_code == 302
    assert "/login" in response.location


def test_calendar_duplicate_creates_draft_copy(client, module):
    user = create_user(module)
    post = create_post(
        module,
        user,
        scheduled_time=datetime(2026, 7, 10, 8, 0),
        status="sent_to_make",
        platforms="instagram,pinterest",
        file_url="https://cdn.test/original.jpg",
    )
    post.sent_at = datetime(2026, 7, 10, 9, 0)
    post.brand_score = 8.5
    post.brand_feedback = '{"brand_voice": true}'
    module.db.session.commit()
    login(client, user)

    response = client.post(f"/calendar/events/{post.id}/duplicate")
    data = response.get_json()
    duplicated_post = module.db.session.get(module.Post, data["post_id"])

    assert response.status_code == 200
    assert data["success"] is True
    assert duplicated_post.id != post.id
    assert duplicated_post.status == "draft"
    assert duplicated_post.caption == post.caption
    assert duplicated_post.prompt == post.prompt
    assert duplicated_post.file_url == post.file_url
    assert duplicated_post.file_type == post.file_type
    assert duplicated_post.platforms == post.platforms
    assert duplicated_post.brand_score == post.brand_score
    assert duplicated_post.brand_feedback == post.brand_feedback
    assert duplicated_post.scheduled_time is None
    assert duplicated_post.sent_at is None


def test_calendar_empty_day_navigation_is_wired(client, module):
    user = create_user(module)
    login(client, user)

    response = client.get("/calendar")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "dateClick" in html
    assert "scheduled_date=" in html


def test_create_post_uses_selected_calendar_date(client, module):
    user = create_user(module)
    login(client, user)

    response = client.get("/create?scheduled_date=2026-07-14")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'value="2026-07-14T09:00"' in html
