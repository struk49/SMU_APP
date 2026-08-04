from datetime import datetime

import pytest
from flask import url_for
from pytz.exceptions import AmbiguousTimeError, NonExistentTimeError

import app as smu_app
from conftest import create_carousel, create_post, create_user, login
from smu_core.models import Post


def rules_for(app, path):
    return [rule for rule in app.url_map.iter_rules() if rule.rule == path]


def set_schedule_helper(app, monkeypatch, name, helper):
    monkeypatch.setitem(app.extensions["smu_post_schedule_helpers"], name, helper)


def test_schedule_route_registered_once_with_old_endpoint(module):
    rules = rules_for(module.app, "/schedule/<int:post_id>")

    assert "posts" in module.app.blueprints
    assert list(module.app.blueprints).count("posts") == 1
    assert len(rules) == 1
    assert rules[0].endpoint == "schedule_post"
    assert "POST" in rules[0].methods


def test_schedule_url_for_compatibility(module):
    with module.app.test_request_context():
        assert url_for("schedule_post", post_id=1) == "/schedule/1"


def test_schedule_requires_login(client, module):
    user = create_user(module)
    post = create_post(module, user)

    response = client.post(
        f"/schedule/{post.id}",
        data={"scheduled_time": "2026-07-10T09:00"},
    )

    assert response.status_code == 302
    assert "/login" in response.location


def test_owner_can_schedule_single_post_with_uk_to_utc_conversion(client, app, module, monkeypatch):
    user = create_user(module)
    post = create_post(module, user, platforms="instagram,pinterest")
    post.sent_at = datetime(2026, 7, 1, 9, 0)
    original_file_url = post.file_url
    diagnostics = []

    set_schedule_helper(
        app,
        monkeypatch,
        "log_scheduled_post_diagnostics",
        lambda scheduled_post, input_local_time: diagnostics.append(
            (scheduled_post.id, input_local_time)
        ),
    )
    login(client, user)

    response = client.post(
        f"/schedule/{post.id}",
        data={"scheduled_time": "2026-01-10T09:00"},
        follow_redirects=True,
    )
    updated = module.db.session.get(Post, post.id)

    assert response.status_code == 200
    assert "Post scheduled successfully." in response.get_data(as_text=True)
    assert updated.scheduled_time == datetime(2026, 1, 10, 9, 0)
    assert updated.status == "scheduled"
    assert updated.user_id == user.id
    assert updated.sent_at == datetime(2026, 7, 1, 9, 0)
    assert updated.file_url == original_file_url
    assert updated.platforms == "instagram,pinterest"
    assert diagnostics == [(post.id, "2026-01-10T09:00")]


def test_summer_bst_conversion_stores_utc(client, app, module, monkeypatch):
    user = create_user(module)
    post = create_post(module, user)
    set_schedule_helper(app, monkeypatch, "log_scheduled_post_diagnostics", lambda *a, **k: None)
    login(client, user)

    client.post(
        f"/schedule/{post.id}",
        data={"scheduled_time": "2026-07-10T09:00"},
    )
    updated = module.db.session.get(Post, post.id)

    assert updated.scheduled_time == datetime(2026, 7, 10, 8, 0)


def test_another_user_cannot_schedule_post(client, module):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    post = create_post(module, owner)
    login(client, other)

    response = client.post(
        f"/schedule/{post.id}",
        data={"scheduled_time": "2026-07-10T09:00"},
    )
    unchanged = module.db.session.get(Post, post.id)

    assert response.status_code == 404
    assert unchanged.status == "draft"
    assert unchanged.scheduled_time is None


def test_missing_post_preserves_404(client, module):
    user = create_user(module)
    login(client, user)

    response = client.post(
        "/schedule/999",
        data={"scheduled_time": "2026-07-10T09:00"},
    )

    assert response.status_code == 404


def test_missing_datetime_preserves_validation_response(client, module):
    user = create_user(module)
    post = create_post(module, user)
    login(client, user)

    response = client.post(
        f"/schedule/{post.id}",
        data={},
        follow_redirects=True,
    )
    unchanged = module.db.session.get(Post, post.id)

    assert response.status_code == 200
    assert "Please select a date and time." in response.get_data(as_text=True)
    assert unchanged.status == "draft"
    assert unchanged.scheduled_time is None


def test_malformed_datetime_rolls_back_and_reports_error(client, module):
    user = create_user(module)
    post = create_post(module, user)
    login(client, user)

    response = client.post(
        f"/schedule/{post.id}",
        data={"scheduled_time": "not-a-date"},
        follow_redirects=True,
    )
    unchanged = module.db.session.get(Post, post.id)

    assert response.status_code == 200
    assert "Error scheduling post:" in response.get_data(as_text=True)
    assert unchanged.status == "draft"
    assert unchanged.scheduled_time is None


def test_nonexistent_local_time_preserves_current_error(client, module):
    user = create_user(module)
    post = create_post(module, user)
    login(client, user)

    response = client.post(
        f"/schedule/{post.id}",
        data={"scheduled_time": "2026-03-29T01:30"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Error scheduling post:" in response.get_data(as_text=True)
    assert module.db.session.get(Post, post.id).scheduled_time is None


def test_ambiguous_local_time_preserves_current_error(client, module):
    user = create_user(module)
    post = create_post(module, user)
    login(client, user)

    response = client.post(
        f"/schedule/{post.id}",
        data={"scheduled_time": "2026-10-25T01:30"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Error scheduling post:" in response.get_data(as_text=True)
    assert module.db.session.get(Post, post.id).scheduled_time is None


def test_timezone_helper_still_raises_for_dst_edge_cases(module):
    with pytest.raises(NonExistentTimeError):
        module.convert_uk_time_to_utc("2026-03-29T01:30")

    with pytest.raises(AmbiguousTimeError):
        module.convert_uk_time_to_utc("2026-10-25T01:30")


def test_scheduling_carousel_updates_every_owned_group_item(client, app, module, monkeypatch):
    user = create_user(module)
    group_id, posts = create_carousel(module, user)
    first_id = posts[0].id
    original = {
        post.id: (post.file_url, post.caption, post.prompt, post.platforms, post.sort_order, post.is_cover)
        for post in posts
    }
    diagnostics = []
    set_schedule_helper(
        app,
        monkeypatch,
        "log_scheduled_post_diagnostics",
        lambda scheduled_post, input_local_time: diagnostics.append(scheduled_post.id),
    )
    login(client, user)

    response = client.post(
        f"/schedule/{first_id}",
        data={"scheduled_time": "2026-07-10T09:00"},
        follow_redirects=True,
    )
    updated_posts = Post.query.filter_by(group_id=group_id).order_by(Post.sort_order).all()

    assert response.status_code == 200
    assert "Carousel scheduled successfully." in response.get_data(as_text=True)
    assert {post.scheduled_time for post in updated_posts} == {datetime(2026, 7, 10, 8, 0)}
    assert {post.status for post in updated_posts} == {"scheduled"}
    assert sorted(diagnostics) == sorted(post.id for post in posts)

    for post in updated_posts:
        assert (
            post.file_url,
            post.caption,
            post.prompt,
            post.platforms,
            post.sort_order,
            post.is_cover,
        ) == original[post.id]


def test_another_user_cannot_schedule_carousel(client, module):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    _group_id, posts = create_carousel(module, owner)
    login(client, other)

    response = client.post(
        f"/schedule/{posts[0].id}",
        data={"scheduled_time": "2026-07-10T09:00"},
    )

    assert response.status_code == 404
    assert {post.status for post in Post.query.all()} == {"draft"}
    assert {post.scheduled_time for post in Post.query.all()} == {None}


def test_carousel_commit_failure_rolls_back_all_group_items(client, app, module, monkeypatch):
    user = create_user(module)
    _group_id, posts = create_carousel(module, user)
    first_id = posts[0].id
    set_schedule_helper(app, monkeypatch, "log_scheduled_post_diagnostics", lambda *a, **k: None)

    def fail_commit():
        raise RuntimeError("commit failed")

    monkeypatch.setattr(module.db.session, "commit", fail_commit)
    login(client, user)

    response = client.post(
        f"/schedule/{first_id}",
        data={"scheduled_time": "2026-07-10T09:00"},
        follow_redirects=True,
    )
    updated_posts = Post.query.order_by(Post.id).all()

    assert response.status_code == 200
    assert "Error scheduling post: commit failed" in response.get_data(as_text=True)
    assert {post.status for post in updated_posts} == {"draft"}
    assert {post.scheduled_time for post in updated_posts} == {None}


def test_scheduled_post_remains_discoverable_by_scheduler_query(client, app, module, monkeypatch):
    user = create_user(module)
    post = create_post(module, user)
    set_schedule_helper(app, monkeypatch, "log_scheduled_post_diagnostics", lambda *a, **k: None)
    login(client, user)

    client.post(
        f"/schedule/{post.id}",
        data={"scheduled_time": "2026-01-10T09:00"},
    )
    due_posts = (
        Post.query.filter(
            Post.scheduled_time.isnot(None),
            Post.status == "scheduled",
            Post.scheduled_time <= datetime(2026, 1, 10, 9, 0),
        )
        .order_by(Post.scheduled_time.asc())
        .all()
    )

    assert [due_post.id for due_post in due_posts] == [post.id]
    assert hasattr(smu_app, "check_scheduled_posts")
    assert "check_scheduled_posts" in smu_app.check_scheduled_posts.__name__


def test_schedule_helper_bridge_and_model_compatibility(module):
    helpers = module.app.extensions["smu_post_schedule_helpers"]

    assert callable(helpers["convert_uk_time_to_utc"])
    assert callable(helpers["get_ordered_carousel_posts"])
    assert callable(helpers["log_scheduled_post_diagnostics"])
    assert smu_app.Post is Post


def test_existing_post_endpoints_and_bridges_remain_registered(module):
    assert "smu_post_detail_helpers" in module.app.extensions
    assert "smu_post_edit_helpers" in module.app.extensions
    assert "smu_post_delete_duplicate_helpers" in module.app.extensions
    assert "smu_post_create_helpers" in module.app.extensions
    assert "smu_post_schedule_helpers" in module.app.extensions

    expected = {
        "/create": "create_post",
        "/post/<int:post_id>": "view_post",
        "/edit-post/<int:post_id>": "edit_post",
        "/edit-carousel/<group_id>": "edit_carousel",
        "/delete/<int:post_id>": "delete_post",
        "/delete-carousel/<group_id>": "delete_carousel",
        "/duplicate-post/<int:post_id>": "duplicate_post",
        "/duplicate-carousel/<group_id>": "duplicate_carousel",
        "/send/<int:post_id>": "send_to_make",
        "/send-carousel/<group_id>": "send_carousel_to_make",
        "/post/<int:post_id>/studio": "post_studio",
    }

    for path, endpoint in expected.items():
        rules = rules_for(module.app, path)

        assert len(rules) == 1
        assert rules[0].endpoint == endpoint
