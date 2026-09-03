from datetime import datetime
from html import unescape

import app as smu_app
from conftest import create_carousel, create_post, create_user, login
from flask import url_for
from smu_core.models import Post


def rules_for(app, path):
    return [rule for rule in app.url_map.iter_rules() if rule.rule == path]


def set_publish_helper(app, monkeypatch, name, helper):
    monkeypatch.setitem(app.extensions["smu_manual_publish_helpers"], name, helper)


def mark_single_sent(post, user_id):
    post.status = "sent_to_make"
    post.sent_at = datetime(2026, 7, 10, 8, 0)


def mark_carousel_sent(module, group_id, user_id):
    for post in Post.query.filter_by(group_id=group_id, user_id=user_id).all():
        post.status = "sent_to_make"
        post.sent_at = datetime(2026, 7, 10, 8, 0)


def test_manual_publish_routes_registered_once_with_old_endpoints(module):
    expected = {
        "/send/<int:post_id>": ("send_to_make", {"POST"}),
        "/send-carousel/<group_id>": ("send_carousel_to_make", {"POST"}),
    }

    assert "posts" in module.app.blueprints
    assert list(module.app.blueprints).count("posts") == 1

    for path, (endpoint, methods) in expected.items():
        rules = rules_for(module.app, path)

        assert len(rules) == 1
        assert rules[0].endpoint == endpoint
        assert methods.issubset(rules[0].methods)


def test_manual_publish_url_for_compatibility(module):
    with module.app.test_request_context():
        assert url_for("send_to_make", post_id=1) == "/send/1"
        assert url_for("send_carousel_to_make", group_id="abc") == "/send-carousel/abc"


def test_manual_publish_routes_require_login(client, module):
    user = create_user(module)
    post = create_post(module, user)
    group_id, _posts = create_carousel(module, user)

    single_response = client.post(f"/send/{post.id}")
    carousel_response = client.post(f"/send-carousel/{group_id}")

    assert single_response.status_code == 302
    assert "/login" in single_response.location
    assert carousel_response.status_code == 302
    assert "/login" in carousel_response.location


def test_owner_can_publish_single_with_post_instance_and_user_id(
    client,
    app,
    module,
    monkeypatch,
):
    user = create_user(module)
    post = create_post(module, user, platforms="instagram,pinterest")
    original = (post.file_url, post.caption, post.prompt, post.platforms)
    calls = []
    events = []

    def fake_publish(published_post, user_id):
        calls.append((published_post, user_id))
        mark_single_sent(published_post, user_id)

    set_publish_helper(app, monkeypatch, "publish_post_to_make", fake_publish)
    set_publish_helper(app, monkeypatch, "log_event", lambda *args, **kwargs: events.append((args, kwargs)))
    login(client, user)

    response = client.post(f"/send/{post.id}", follow_redirects=True)
    updated = module.db.session.get(Post, post.id)

    assert response.status_code == 200
    assert "Post sent for publishing successfully." in response.get_data(as_text=True)
    assert calls == [(updated, user.id)]
    assert updated.status == "sent_to_make"
    assert updated.sent_at == datetime(2026, 7, 10, 8, 0)
    assert (updated.file_url, updated.caption, updated.prompt, updated.platforms) == original
    assert events[0][0] == ("publishing_success",)
    assert events[0][1]["post_type"] == "single"
    assert events[0][1]["source"] == "manual"


def test_another_user_cannot_publish_single(client, app, module, monkeypatch):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    post = create_post(module, owner)
    calls = []
    set_publish_helper(app, monkeypatch, "publish_post_to_make", lambda *args: calls.append(args))
    login(client, other)

    response = client.post(f"/send/{post.id}")

    assert response.status_code == 404
    assert calls == []
    assert module.db.session.get(Post, post.id).status == "draft"


def test_missing_single_publish_preserves_404(client, module):
    user = create_user(module)
    login(client, user)

    response = client.post("/send/999")

    assert response.status_code == 404


def test_single_duplicate_send_is_blocked_before_helper_call(client, app, module, monkeypatch):
    user = create_user(module)
    post = create_post(module, user, status="sent_to_make")
    calls = []
    set_publish_helper(app, monkeypatch, "publish_post_to_make", lambda *args: calls.append(args))
    login(client, user)

    response = client.post(f"/send/{post.id}", follow_redirects=True)

    assert response.status_code == 200
    assert "This post has already been sent for publishing." in response.get_data(as_text=True)
    assert calls == []


def test_single_publishing_status_is_blocked_before_helper_call(client, app, module, monkeypatch):
    user = create_user(module)
    post = create_post(module, user, status="publishing")
    calls = []
    set_publish_helper(app, monkeypatch, "publish_post_to_make", lambda *args: calls.append(args))
    login(client, user)

    response = client.post(f"/send/{post.id}", follow_redirects=True)

    assert response.status_code == 200
    assert "This post has already been sent for publishing." in response.get_data(as_text=True)
    assert calls == []


def test_single_helper_exception_rolls_back_and_reports_error(client, app, module, monkeypatch):
    user = create_user(module)
    post = create_post(module, user)
    events = []

    def fail_publish(published_post, user_id):
        published_post.status = "sent_to_make"
        raise RuntimeError("No single-post webhook is configured. Add it in Connected Accounts.")

    set_publish_helper(app, monkeypatch, "publish_post_to_make", fail_publish)
    set_publish_helper(app, monkeypatch, "log_event", lambda *args, **kwargs: events.append((args, kwargs)))
    login(client, user)

    response = client.post(f"/send/{post.id}", follow_redirects=True)
    updated = module.db.session.get(Post, post.id)

    assert response.status_code == 200
    html = unescape(response.get_data(as_text=True))
    assert "We couldn't publish this post. Please try again." in html
    assert "No single-post webhook is configured" not in html
    assert updated.status == "draft"
    assert updated.sent_at is None
    assert events[0][0] == ("publishing_failure",)
    assert events[0][1]["error_type"] == "RuntimeError"


def test_owner_can_publish_carousel_with_representative_post(
    client,
    app,
    module,
    monkeypatch,
):
    user = create_user(module)
    group_id, posts = create_carousel(module, user)
    representative_id = posts[0].id
    original = {
        post.id: (post.file_url, post.caption, post.prompt, post.platforms, post.sort_order, post.is_cover)
        for post in posts
    }
    calls = []
    events = []

    def fake_publish(representative, user_id):
        calls.append((representative.id, user_id))
        mark_carousel_sent(module, representative.group_id, user_id)

    set_publish_helper(app, monkeypatch, "publish_post_to_make", fake_publish)
    set_publish_helper(app, monkeypatch, "log_event", lambda *args, **kwargs: events.append((args, kwargs)))
    login(client, user)

    response = client.post(f"/send-carousel/{group_id}", follow_redirects=True)
    updated_posts = Post.query.filter_by(group_id=group_id).order_by(Post.sort_order).all()

    assert response.status_code == 200
    assert "Carousel sent for publishing successfully." in response.get_data(as_text=True)
    assert calls == [(representative_id, user.id)]
    assert {post.status for post in updated_posts} == {"sent_to_make"}
    assert {post.sent_at for post in updated_posts} == {datetime(2026, 7, 10, 8, 0)}
    assert events[0][0] == ("publishing_success",)
    assert events[0][1]["post_type"] == "carousel"

    for post in updated_posts:
        assert (
            post.file_url,
            post.caption,
            post.prompt,
            post.platforms,
            post.sort_order,
            post.is_cover,
        ) == original[post.id]


def test_another_user_cannot_publish_carousel(client, app, module, monkeypatch):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    group_id, posts = create_carousel(module, owner)
    calls = []
    set_publish_helper(app, monkeypatch, "publish_post_to_make", lambda *args: calls.append(args))
    login(client, other)

    response = client.post(f"/send-carousel/{group_id}", follow_redirects=True)

    assert response.status_code == 200
    assert "Carousel not found." in response.get_data(as_text=True)
    assert calls == []
    assert {post.status for post in Post.query.filter_by(group_id=group_id)} == {"draft"}
    assert Post.query.filter_by(group_id=group_id).count() == len(posts)


def test_missing_carousel_publish_redirects_to_dashboard(client, module):
    user = create_user(module)
    login(client, user)

    response = client.post("/send-carousel/missing", follow_redirects=True)

    assert response.status_code == 200
    assert "Carousel not found." in response.get_data(as_text=True)


def test_carousel_duplicate_send_is_blocked_when_all_sent(client, app, module, monkeypatch):
    user = create_user(module)
    group_id, posts = create_carousel(module, user, status="sent_to_make")
    calls = []
    set_publish_helper(app, monkeypatch, "publish_post_to_make", lambda *args: calls.append(args))
    login(client, user)

    response = client.post(f"/send-carousel/{group_id}", follow_redirects=True)

    assert response.status_code == 200
    assert "This post has already been sent for publishing." in response.get_data(as_text=True)
    assert calls == []
    assert Post.query.filter_by(group_id=group_id).count() == len(posts)


def test_carousel_helper_exception_rolls_back_all_group_items(client, app, module, monkeypatch):
    user = create_user(module)
    group_id, posts = create_carousel(module, user)
    events = []

    def fail_publish(representative, user_id):
        for post in Post.query.filter_by(group_id=representative.group_id, user_id=user_id):
            post.status = "sent_to_make"
        raise RuntimeError("Make returned 500")

    set_publish_helper(app, monkeypatch, "publish_post_to_make", fail_publish)
    set_publish_helper(app, monkeypatch, "log_event", lambda *args, **kwargs: events.append((args, kwargs)))
    login(client, user)

    response = client.post(f"/send-carousel/{group_id}", follow_redirects=True)
    updated_posts = Post.query.filter_by(group_id=group_id).all()

    assert response.status_code == 200
    html = unescape(response.get_data(as_text=True))
    assert "We couldn't publish this carousel. Please try again." in html
    assert "Make returned 500" not in html
    assert {post.status for post in updated_posts} == {"draft"}
    assert {post.sent_at for post in updated_posts} == {None}
    assert Post.query.filter_by(group_id=group_id).count() == len(posts)
    assert events[0][0] == ("publishing_failure",)
    assert events[0][1]["error_type"] == "RuntimeError"


def test_real_publishing_helpers_remain_in_app(module):
    assert hasattr(smu_app, "publish_post_to_make")
    assert hasattr(smu_app, "build_single_payload")
    assert hasattr(smu_app, "build_carousel_payload")
    assert hasattr(smu_app, "send_payload_to_make")
    assert hasattr(smu_app, "check_scheduled_posts")
    assert hasattr(smu_app, "scheduler")


def test_manual_publish_helper_bridge_and_model_compatibility(module):
    helpers = module.app.extensions["smu_manual_publish_helpers"]

    assert callable(helpers["publish_post_to_make"])
    assert callable(helpers["get_ordered_carousel_posts"])
    assert callable(helpers["log_event"])
    assert smu_app.Post is Post


def test_existing_post_endpoints_and_bridges_remain_registered(module):
    for key in {
        "smu_post_detail_helpers",
        "smu_post_edit_helpers",
        "smu_post_delete_duplicate_helpers",
        "smu_post_create_helpers",
        "smu_post_schedule_helpers",
        "smu_manual_publish_helpers",
    }:
        assert key in module.app.extensions

    expected = {
        "/create": "create_post",
        "/post/<int:post_id>": "view_post",
        "/edit-post/<int:post_id>": "edit_post",
        "/edit-carousel/<group_id>": "edit_carousel",
        "/delete/<int:post_id>": "delete_post",
        "/delete-carousel/<group_id>": "delete_carousel",
        "/duplicate-post/<int:post_id>": "duplicate_post",
        "/duplicate-carousel/<group_id>": "duplicate_carousel",
        "/schedule/<int:post_id>": "schedule_post",
        "/post/<int:post_id>/improve": "improve_post",
        "/rewrite-caption/<int:post_id>": "rewrite_caption",
        "/rewrite-carousel-caption/<group_id>": "rewrite_carousel_caption",
        "/post/<int:post_id>/studio": "post_studio",
    }

    for path, endpoint in expected.items():
        rules = rules_for(module.app, path)

        assert len(rules) == 1
        assert rules[0].endpoint == endpoint
