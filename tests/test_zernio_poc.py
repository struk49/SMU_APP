from datetime import datetime, timedelta

import pytest
from flask import url_for

from conftest import (
    MockMakeResponse,
    create_accounts,
    create_carousel,
    create_post,
    create_user,
    login,
)
from smu_core.services import zernio
from smu_core.services.time_utils import utc_now


def rules_for(app, path):
    return [rule for rule in app.url_map.iter_rules() if rule.rule == path]


def test_zernio_poc_routes_are_registered_once(module):
    expected = {
        "/accounts/zernio/connect/<platform>": ("zernio_connect", {"GET"}),
        "/accounts/zernio/callback": ("zernio_callback", {"GET"}),
        "/post/<int:post_id>/zernio-publish": ("publish_with_zernio", {"POST"}),
        "/post/<int:post_id>/zernio-status": ("refresh_zernio_status", {"POST"}),
    }

    for path, (endpoint, methods) in expected.items():
        rules = rules_for(module.app, path)
        assert len(rules) == 1
        assert rules[0].endpoint == endpoint
        assert methods.issubset(rules[0].methods)


def test_zernio_url_for_compatibility(module):
    with module.app.test_request_context():
        assert url_for("zernio_connect", platform="instagram") == (
            "/accounts/zernio/connect/instagram"
        )
        assert url_for("zernio_callback") == "/accounts/zernio/callback"
        assert url_for("publish_with_zernio", post_id=1) == (
            "/post/1/zernio-publish"
        )


def test_zernio_connect_requires_login(client):
    response = client.get("/accounts/zernio/connect/instagram")

    assert response.status_code == 302
    assert "/login" in response.location


def test_zernio_connect_creates_profile_and_redirects_to_hosted_flow(
    client,
    app,
    module,
    monkeypatch,
):
    app.config.update(
        ZERNIO_API_KEY="sk_test",
        ZERNIO_BASE_URL="https://zernio.test/api/v1",
    )
    user = create_user(module)
    calls = []
    login(client, user)

    def fake_ensure_profile(current_user, accounts, **kwargs):
        calls.append(("profile", current_user.id, accounts.user_id, kwargs))
        accounts.zernio_profile_id = "prof_123"
        return "prof_123"

    def fake_connection_url(**kwargs):
        calls.append(("connect", kwargs))
        return "https://connect.zernio.test/start"

    monkeypatch.setattr(zernio, "ensure_profile_for_user", fake_ensure_profile)
    monkeypatch.setattr(zernio, "create_connection_url", fake_connection_url)

    response = client.get("/accounts/zernio/connect/instagram")
    account = module.ConnectedAccount.query.filter_by(user_id=user.id).first()

    assert response.status_code == 302
    assert response.location == "https://connect.zernio.test/start"
    assert account.zernio_profile_id == "prof_123"
    assert calls[0][0] == "profile"
    assert calls[0][1:3] == (user.id, user.id)
    assert calls[1][0] == "connect"
    assert calls[1][1]["profile_id"] == "prof_123"
    assert calls[1][1]["platform"] == "instagram"
    assert calls[1][1]["api_key"] == "sk_test"


def test_zernio_callback_stores_connected_account_ids(
    client,
    app,
    module,
    monkeypatch,
):
    app.config["ZERNIO_API_KEY"] = "sk_test"
    user = create_user(module)
    account = create_accounts(module, user)
    account.zernio_profile_id = "prof_123"
    module.db.session.commit()
    login(client, user)

    def fake_sync(accounts, **kwargs):
        accounts.zernio_instagram_account_id = "acct_ig"
        return [{"_id": "acct_ig", "platform": "instagram"}]

    monkeypatch.setattr(zernio, "sync_connected_account_ids", fake_sync)

    response = client.get(
        "/accounts/zernio/callback?connected=instagram",
        follow_redirects=True,
    )
    saved = module.db.session.get(module.ConnectedAccount, account.id)

    assert response.status_code == 200
    assert "Social account connection confirmed." in response.get_data(as_text=True)
    assert saved.zernio_instagram_account_id == "acct_ig"


def test_zernio_publish_captures_provider_identifier_and_status(
    client,
    app,
    module,
    monkeypatch,
):
    app.config["ZERNIO_API_KEY"] = "sk_test"
    user = create_user(module)
    account = create_accounts(module, user)
    account.zernio_profile_id = "prof_123"
    account.zernio_instagram_account_id = "acct_ig"
    post = create_post(
        module,
        user,
        platforms="instagram",
        file_url="https://res.cloudinary.com/demo/image/upload/post.jpg",
    )
    module.db.session.commit()
    calls = []
    login(client, user)

    def fake_publish(published_post, connected_account, **kwargs):
        calls.append((published_post.id, connected_account.id, kwargs))
        return zernio.ZernioPublishResult(
            provider_post_id="zp_123",
            status="publishing",
            platforms=["instagram"],
        )

    monkeypatch.setattr(zernio, "publish_single_image", fake_publish)

    response = client.post(f"/post/{post.id}/zernio-publish", follow_redirects=True)
    saved = module.db.session.get(module.Post, post.id)

    assert response.status_code == 200
    assert "Post sent for publishing successfully." in response.get_data(as_text=True)
    assert saved.zernio_post_id == "zp_123"
    assert saved.zernio_status == "publishing"
    assert saved.zernio_platforms == "instagram"
    assert saved.status == "publishing"
    assert saved.sent_at is not None
    assert calls == [(post.id, account.id, {"api_key": "sk_test", "base_url": app.config["ZERNIO_BASE_URL"]})]


def test_zernio_publish_is_owner_scoped(client, app, module, monkeypatch):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    post = create_post(module, owner)
    calls = []
    monkeypatch.setattr(zernio, "publish_single_image", lambda *args, **kwargs: calls.append(args))
    login(client, other)

    response = client.post(f"/post/{post.id}/zernio-publish")

    assert response.status_code == 404
    assert calls == []


def test_zernio_publish_rejects_carousel_without_provider_call(
    client,
    app,
    module,
    monkeypatch,
):
    user = create_user(module)
    group_id, posts = create_carousel(module, user)
    calls = []
    monkeypatch.setattr(zernio, "publish_single_image", lambda *args, **kwargs: calls.append(args))
    login(client, user)

    response = client.get(f"/post/{posts[0].id}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Publish Single Image" not in html
    assert group_id
    assert calls == []


def test_supported_single_image_post_shows_one_direct_publish_action(client, module):
    user = create_user(module)
    create_accounts(module, user, facebook=True)
    post = create_post(
        module,
        user,
        platforms="instagram,facebook",
        file_url="https://res.cloudinary.com/demo/image/upload/post.jpg",
    )
    login(client, user)

    response = client.get(f"/post/{post.id}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert html.count("Publish Post") == 1
    assert f'action="/post/{post.id}/zernio-publish"' in html
    assert f'action="/send/{post.id}"' not in html
    assert "Publish Single Image" not in html


def test_carousel_post_keeps_existing_carousel_publish_action(client, module):
    user = create_user(module)
    group_id, posts = create_carousel(module, user)
    login(client, user)

    response = client.get(f"/post/{posts[0].id}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Publish Carousel" in html
    assert f'action="/send-carousel/{group_id}"' in html
    assert "Publish Single Image" not in html


def test_linkedin_post_keeps_existing_publish_route(client, module):
    user = create_user(module)
    accounts = create_accounts(module, user)
    accounts.linkedin_connected = True
    post = create_post(
        module,
        user,
        platforms="linkedin",
        file_url="",
    )
    post.file_type = "text"
    module.db.session.commit()
    login(client, user)

    response = client.get(f"/post/{post.id}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert html.count("Publish Post") == 1
    assert f'action="/send/{post.id}"' in html
    assert f'action="/post/{post.id}/zernio-publish"' not in html


def test_scheduled_single_image_post_does_not_show_manual_publish_action(client, module):
    user = create_user(module)
    create_accounts(module, user)
    post = create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=datetime(2026, 7, 10, 8, 0),
        platforms="instagram",
        file_url="https://res.cloudinary.com/demo/image/upload/post.jpg",
    )
    login(client, user)

    response = client.get(f"/post/{post.id}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert f'action="/send/{post.id}"' not in html
    assert f'action="/post/{post.id}/zernio-publish"' not in html
    assert "Scheduled for 10 Jul 2026 08:00" in html


def test_zernio_status_refresh_updates_post_result(client, app, module, monkeypatch):
    app.config["ZERNIO_API_KEY"] = "sk_test"
    user = create_user(module)
    post = create_post(module, user)
    post.zernio_post_id = "zp_123"
    post.zernio_status = "publishing"
    module.db.session.commit()
    login(client, user)

    monkeypatch.setattr(
        zernio,
        "get_post_status",
        lambda *args, **kwargs: zernio.ZernioPublishResult(
            provider_post_id="zp_123",
            status="published",
            platforms=["instagram"],
            published_url="https://instagram.test/p/1",
        ),
    )

    response = client.post(f"/post/{post.id}/zernio-status", follow_redirects=True)
    saved = module.db.session.get(module.Post, post.id)

    assert response.status_code == 200
    assert saved.zernio_status == "published"
    assert saved.zernio_published_url == "https://instagram.test/p/1"


def test_existing_make_route_remains_available(module):
    endpoints = {rule.endpoint for rule in module.app.url_map.iter_rules()}

    assert "send_to_make" in endpoints
    assert "send_carousel_to_make" in endpoints


def test_scheduled_instagram_single_image_uses_zernio_without_make(
    app,
    module,
    monkeypatch,
):
    app.config["ZERNIO_API_KEY"] = "sk_test"
    user = create_user(module)
    accounts = create_accounts(
        module,
        user,
        instagram=False,
        facebook=False,
        single_webhook="",
        carousel_webhook="",
    )
    accounts.zernio_instagram_account_id = "acct_ig"
    post = create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=utc_now() - timedelta(minutes=1),
        platforms="instagram",
        file_url="https://res.cloudinary.com/demo/image/upload/post.jpg",
    )
    module.db.session.commit()
    calls = []

    def fake_publish(published_post, connected_account, **kwargs):
        calls.append((published_post.id, connected_account.id, kwargs))
        return zernio.ZernioPublishResult(
            provider_post_id="zp_ig",
            status="publishing",
            platforms=["instagram"],
        )

    monkeypatch.setattr(zernio, "publish_single_image", fake_publish)
    monkeypatch.setattr(
        module.requests,
        "post",
        lambda *args, **kwargs: pytest.fail("Make should not be called"),
    )

    module.check_scheduled_posts()
    module.db.session.expire_all()
    saved = module.db.session.get(module.Post, post.id)

    assert calls == [(post.id, accounts.id, {"api_key": "sk_test", "base_url": app.config["ZERNIO_BASE_URL"]})]
    assert saved.status == "publishing"
    assert saved.sent_at is not None
    assert saved.zernio_post_id == "zp_ig"
    assert saved.zernio_status == "publishing"
    assert saved.zernio_platforms == "instagram"


def test_scheduled_facebook_single_image_uses_zernio_without_make(
    app,
    module,
    monkeypatch,
):
    app.config["ZERNIO_API_KEY"] = "sk_test"
    user = create_user(module)
    accounts = create_accounts(
        module,
        user,
        instagram=False,
        facebook=False,
        single_webhook="",
        carousel_webhook="",
    )
    accounts.zernio_facebook_account_id = "acct_fb"
    post = create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=utc_now() - timedelta(minutes=1),
        platforms="facebook",
        file_url="https://res.cloudinary.com/demo/image/upload/post.jpg",
    )
    module.db.session.commit()
    calls = []

    def fake_publish(published_post, connected_account, **kwargs):
        calls.append((published_post.id, connected_account.id, kwargs))
        return zernio.ZernioPublishResult(
            provider_post_id="zp_fb",
            status="publishing",
            platforms=["facebook"],
        )

    monkeypatch.setattr(zernio, "publish_single_image", fake_publish)
    monkeypatch.setattr(
        module.requests,
        "post",
        lambda *args, **kwargs: pytest.fail("Make should not be called"),
    )

    module.check_scheduled_posts()
    module.db.session.expire_all()
    saved = module.db.session.get(module.Post, post.id)

    assert calls == [(post.id, accounts.id, {"api_key": "sk_test", "base_url": app.config["ZERNIO_BASE_URL"]})]
    assert saved.status == "publishing"
    assert saved.zernio_post_id == "zp_fb"
    assert saved.zernio_platforms == "facebook"


def test_scheduled_instagram_and_facebook_uses_one_zernio_request_without_make(
    app,
    module,
    monkeypatch,
):
    app.config["ZERNIO_API_KEY"] = "sk_test"
    user = create_user(module)
    accounts = create_accounts(
        module,
        user,
        instagram=False,
        facebook=False,
        single_webhook="",
        carousel_webhook="",
    )
    accounts.zernio_instagram_account_id = "acct_ig"
    accounts.zernio_facebook_account_id = "acct_fb"
    post = create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=utc_now() - timedelta(minutes=1),
        platforms="instagram,facebook",
        file_url="https://res.cloudinary.com/demo/image/upload/post.jpg",
    )
    module.db.session.commit()
    calls = []

    def fake_publish(published_post, connected_account, **kwargs):
        calls.append((published_post.id, connected_account.id, published_post.platforms))
        return zernio.ZernioPublishResult(
            provider_post_id="zp_both",
            status="publishing",
            platforms=["instagram", "facebook"],
        )

    monkeypatch.setattr(zernio, "publish_single_image", fake_publish)
    monkeypatch.setattr(
        module.requests,
        "post",
        lambda *args, **kwargs: pytest.fail("Make should not be called"),
    )

    module.check_scheduled_posts()
    module.db.session.expire_all()
    saved = module.db.session.get(module.Post, post.id)

    assert calls == [(post.id, accounts.id, "instagram,facebook")]
    assert saved.status == "publishing"
    assert saved.zernio_post_id == "zp_both"
    assert saved.zernio_platforms == "instagram,facebook"


def test_scheduled_direct_publish_missing_connection_fails_without_make(
    app,
    module,
    monkeypatch,
):
    app.config["ZERNIO_API_KEY"] = "sk_test"
    user = create_user(module)
    create_accounts(
        module,
        user,
        instagram=False,
        facebook=False,
        single_webhook="",
        carousel_webhook="",
    )
    post = create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=utc_now() - timedelta(minutes=1),
        platforms="instagram",
        file_url="https://res.cloudinary.com/demo/image/upload/post.jpg",
    )

    monkeypatch.setattr(
        module.requests,
        "post",
        lambda *args, **kwargs: pytest.fail("Make should not be called"),
    )

    module.check_scheduled_posts()
    module.db.session.expire_all()
    saved = module.db.session.get(module.Post, post.id)

    assert saved.status == "schedule_failed"
    assert saved.sent_at is None
    assert saved.zernio_post_id is None


def test_scheduled_direct_publish_requires_all_selected_zernio_accounts(
    app,
    module,
    monkeypatch,
):
    app.config["ZERNIO_API_KEY"] = "sk_test"
    user = create_user(module)
    accounts = create_accounts(
        module,
        user,
        instagram=False,
        facebook=False,
        single_webhook="",
        carousel_webhook="",
    )
    accounts.zernio_instagram_account_id = "acct_ig"
    post = create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=utc_now() - timedelta(minutes=1),
        platforms="instagram,facebook",
        file_url="https://res.cloudinary.com/demo/image/upload/post.jpg",
    )
    module.db.session.commit()

    monkeypatch.setattr(
        zernio,
        "publish_single_image",
        lambda *args, **kwargs: pytest.fail("Zernio should not be called"),
    )
    monkeypatch.setattr(
        module.requests,
        "post",
        lambda *args, **kwargs: pytest.fail("Make should not be called"),
    )

    module.check_scheduled_posts()
    module.db.session.expire_all()
    saved = module.db.session.get(module.Post, post.id)

    assert saved.status == "schedule_failed"
    assert saved.sent_at is None
    assert saved.zernio_post_id is None


def test_scheduled_direct_publish_provider_exception_fails_without_make(
    app,
    module,
    monkeypatch,
):
    app.config["ZERNIO_API_KEY"] = "sk_test"
    user = create_user(module)
    accounts = create_accounts(
        module,
        user,
        instagram=False,
        facebook=False,
        single_webhook="",
        carousel_webhook="",
    )
    accounts.zernio_instagram_account_id = "acct_ig"
    post = create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=utc_now() - timedelta(minutes=1),
        platforms="instagram",
        file_url="https://res.cloudinary.com/demo/image/upload/post.jpg",
    )
    module.db.session.commit()

    def fail_publish(*args, **kwargs):
        raise zernio.ZernioError("Zernio rejected the request.", status_code=503)

    monkeypatch.setattr(zernio, "publish_single_image", fail_publish)
    monkeypatch.setattr(
        module.requests,
        "post",
        lambda *args, **kwargs: pytest.fail("Make should not be called"),
    )

    module.check_scheduled_posts()
    module.db.session.expire_all()
    saved = module.db.session.get(module.Post, post.id)

    assert saved.status == "schedule_failed"
    assert saved.sent_at is None
    assert saved.zernio_post_id is None


def test_scheduled_carousel_still_uses_make_not_zernio(
    app,
    module,
    monkeypatch,
):
    app.config["ZERNIO_API_KEY"] = "sk_test"
    user = create_user(module)
    accounts = create_accounts(module, user)
    accounts.zernio_instagram_account_id = "acct_ig"
    accounts.zernio_facebook_account_id = "acct_fb"
    group_id, posts = create_carousel(module, user, status="scheduled", scheduled=True)
    sent = []

    monkeypatch.setattr(
        zernio,
        "publish_single_image",
        lambda *args, **kwargs: pytest.fail("Carousel should not use Zernio"),
    )
    monkeypatch.setattr(
        module.requests,
        "post",
        lambda url, json, timeout: sent.append((url, json, timeout)) or MockMakeResponse(),
    )

    module.check_scheduled_posts()
    module.db.session.expire_all()

    assert len(sent) == 1
    assert sent[0][0] == "https://make.test/carousel"
    assert sent[0][1]["post_type"] == "carousel"
    assert sent[0][1]["group_id"] == group_id
    assert {
        module.db.session.get(module.Post, post.id).status for post in posts
    } == {"sent_to_make"}
