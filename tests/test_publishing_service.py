from datetime import timedelta

import pytest

import app as smu_app
from conftest import MockMakeResponse, create_accounts, create_carousel, create_post, create_user
from smu_core.models import ConnectedAccount, Post
from smu_core.services import publishing
from smu_core.services.time_utils import utc_now


def create_full_accounts(module, user, **overrides):
    defaults = {
        "user_id": user.id,
        "instagram_connected": True,
        "facebook_connected": True,
        "linkedin_connected": True,
        "pinterest_connected": True,
        "reddit_connected": False,
        "x_connected": True,
        "make_webhook_single": "https://make.test/user-single",
        "make_webhook_carousel": "https://make.test/user-carousel",
    }
    defaults.update(overrides)
    accounts = ConnectedAccount(**defaults)
    module.db.session.add(accounts)
    module.db.session.commit()
    return accounts


def test_app_exports_remain_callable_and_delegate_to_service(module):
    for name in {
        "get_user_connected_accounts",
        "get_enabled_platforms_for_user",
        "get_user_make_webhook",
        "build_single_payload",
        "build_carousel_payload",
        "send_payload_to_make",
        "publish_post_to_make",
    }:
        assert callable(getattr(smu_app, name))
        assert callable(getattr(publishing, name))


def test_service_functions_are_the_implementation_source(module):
    assert publishing.ConnectedAccount is ConnectedAccount
    assert publishing.Post is Post
    assert smu_app.publish_post_to_make.__module__ == "app"
    assert publishing.publish_post_to_make.__module__ == "smu_core.services.publishing"


def test_user_specific_connected_account_lookup(app, module):
    user = create_user(module)
    other = create_user(module, email="other@example.com")
    accounts = create_full_accounts(module, user)
    create_full_accounts(module, other, make_webhook_single="https://make.test/other")

    assert publishing.get_user_connected_accounts(user.id).id == accounts.id
    assert publishing.get_user_connected_accounts(999999) is None


def test_enabled_platform_filtering_preserves_order_and_connected_flags(app, module):
    user = create_user(module)
    create_full_accounts(
        module,
        user,
        facebook_connected=False,
        reddit_connected=False,
    )

    enabled = publishing.get_enabled_platforms_for_user(
        [" Facebook ", "Instagram", "Pinterest", "reddit", "x", "unknown"],
        user_id=user.id,
    )

    assert enabled == ["instagram", "pinterest", "x"]


def test_enabled_platform_filtering_returns_empty_without_accounts(app, module):
    user = create_user(module)

    assert publishing.get_enabled_platforms_for_user(
        ["instagram", "facebook"],
        user_id=user.id,
    ) == []


def test_single_and_carousel_webhook_resolution_prefers_user_values(app,module):
    user = create_user(module)
    create_full_accounts(module, user)

    assert publishing.get_user_make_webhook("single", user_id=user.id) == (
        "https://make.test/user-single"
    )
    assert publishing.get_user_make_webhook("carousel", user_id=user.id) == (
        "https://make.test/user-carousel"
    )


def test_webhook_resolution_uses_global_fallback_when_user_value_missing(app,module):
    user = create_user(module)
    create_full_accounts(
        module,
        user,
        make_webhook_single="",
        make_webhook_carousel="",
    )

    assert publishing.get_user_make_webhook(
        "single",
        user_id=user.id,
        make_webhook_single="https://make.test/global-single",
    ) == "https://make.test/global-single"
    assert publishing.get_user_make_webhook(
        "carousel",
        user_id=user.id,
        make_webhook_carousel="https://make.test/global-carousel",
    ) == "https://make.test/global-carousel"


def test_webhook_resolution_returns_none_when_missing(app, module):
    user = create_user(module)

    assert publishing.get_user_make_webhook("single", user_id=user.id) is None
    assert publishing.get_user_make_webhook("carousel", user_id=user.id) is None


def test_app_webhook_wrapper_preserves_monkeypatched_global_constants(app, module, monkeypatch):
    user = create_user(module)
    create_full_accounts(
        module,
        user,
        make_webhook_single="",
        make_webhook_carousel="",
    )
    monkeypatch.setattr(module, "MAKE_WEBHOOK_SINGLE", "https://make.test/global-single")
    monkeypatch.setattr(module, "MAKE_WEBHOOK_CAROUSEL", "https://make.test/global-carousel")

    assert module.get_user_make_webhook("single", user_id=user.id) == (
        "https://make.test/global-single"
    )
    assert module.get_user_make_webhook("carousel", user_id=user.id) == (
        "https://make.test/global-carousel"
    )


def test_exact_single_payload_shape(app, module):
    user = create_user(module)
    post = create_post(module, user, platforms="instagram,facebook")

    assert publishing.build_single_payload(post) == {
        "post_type": "single",
        "post_id": post.id,
        "caption": "Caption",
        "prompt": "Prompt",
        "file_url": post.file_url,
        "file_type": "image",
        "platforms": ["instagram", "facebook"],
    }


def test_exact_carousel_payload_shape_and_user_filtering(app,module):
    user = create_user(module)
    other = create_user(module, email="other@example.com")
    group_id, posts = create_carousel(module, user)
    other_post = create_post(module, other, group_id=group_id)

    payload = publishing.build_carousel_payload(group_id, user_id=user.id)

    assert payload == {
        "post_type": "carousel",
        "group_id": group_id,
        "caption": posts[0].caption,
        "prompt": posts[0].prompt,
        "platforms": ["instagram", "facebook"],
        "media": [
            {
                "post_id": posts[0].id,
                "file_url": posts[0].file_url,
                "file_type": "image",
                "sort_order": 0,
                "is_cover": True,
            },
            {
                "post_id": posts[1].id,
                "file_url": posts[1].file_url,
                "file_type": "image",
                "sort_order": 1,
                "is_cover": False,
            },
        ],
    }
    assert other_post.id not in {item["post_id"] for item in payload["media"]}


def test_carousel_payload_applies_instagram_safe_media_urls(app,module):
    user = create_user(module)
    group_id, posts = create_carousel(
        module,
        user,
    )
    for post in posts:
        post.file_url = "https://res.cloudinary.com/demo/image/upload/v1/source.png"
    module.db.session.commit()

    payload = publishing.build_carousel_payload(group_id, user_id=user.id)

    assert all("/upload/c_fill,w_1080,h_1080,q_auto,f_jpg/" in item["file_url"] for item in payload["media"])


def test_send_payload_to_make_success(monkeypatch):
    sent = []

    def fake_post(url, json, timeout):
        sent.append((url, json, timeout))
        return MockMakeResponse()

    monkeypatch.setattr(publishing.requests, "post", fake_post)

    response = publishing.send_payload_to_make(
        {"post_type": "single", "platforms": ["instagram"]},
        "https://make.test/single",
    )

    assert response.status_code == 200
    assert sent == [
        ("https://make.test/single", {"post_type": "single", "platforms": ["instagram"]}, 30)
    ]


def test_send_payload_to_make_non_2xx_raises(monkeypatch):
    monkeypatch.setattr(
        publishing.requests,
        "post",
        lambda url, json, timeout: MockMakeResponse(status_code=500),
    )

    with pytest.raises(Exception, match="500 error"):
        publishing.send_payload_to_make(
            {"post_type": "single"},
            "https://make.test/single",
        )


def test_send_payload_to_make_exception_propagates(monkeypatch):
    def fail_post(*args, **kwargs):
        raise RuntimeError("network failed")

    monkeypatch.setattr(publishing.requests, "post", fail_post)

    with pytest.raises(RuntimeError, match="network failed"):
        publishing.send_payload_to_make(
            {"post_type": "single"},
            "https://make.test/single",
        )


def test_send_payload_to_make_missing_webhook_raises_without_request(monkeypatch):
    monkeypatch.setattr(
        publishing.requests,
        "post",
        lambda *args, **kwargs: pytest.fail("requests.post should not be called"),
    )

    with pytest.raises(Exception, match="No Make webhook configured for single posts."):
        publishing.send_payload_to_make({"post_type": "single"})


def test_single_publish_success_updates_status_without_committing(app, module, monkeypatch):
    user = create_user(module)
    create_accounts(module, user, facebook=True)
    post = create_post(module, user)
    sent = []

    monkeypatch.setattr(
        publishing.requests,
        "post",
        lambda url, json, timeout: sent.append((url, json, timeout)) or MockMakeResponse(),
    )

    response = publishing.publish_post_to_make(post, user.id)

    assert response.status_code == 200
    assert sent[0][0] == "https://make.test/single"
    assert sent[0][1]["post_type"] == "single"
    assert sent[0][1]["platforms"] == ["instagram", "facebook"]
    assert post.status == "sent_to_make"
    assert post.sent_at is not None


def test_carousel_publish_success_updates_all_group_posts(app, module, monkeypatch):
    user = create_user(module)
    create_accounts(module, user)
    group_id, posts = create_carousel(module, user)
    sent = []

    monkeypatch.setattr(
        publishing.requests,
        "post",
        lambda url, json, timeout: sent.append((url, json, timeout)) or MockMakeResponse(),
    )

    publishing.publish_post_to_make(posts[0], user.id)

    assert sent[0][0] == "https://make.test/carousel"
    assert sent[0][1]["post_type"] == "carousel"
    assert sent[0][1]["group_id"] == group_id
    assert {post.status for post in posts} == {"sent_to_make"}
    assert {post.sent_at is not None for post in posts} == {True}


def test_publish_requires_enabled_platforms_before_webhook_call(app, module, monkeypatch):
    user = create_user(module)
    create_accounts(module, user, instagram=False, facebook=False)
    post = create_post(module, user)
    monkeypatch.setattr(
        publishing.requests,
        "post",
        lambda *args, **kwargs: pytest.fail("requests.post should not be called"),
    )

    with pytest.raises(Exception, match="No connected platforms are enabled"):
        publishing.publish_post_to_make(post, user.id)

    assert post.status == "draft"
    assert post.sent_at is None


def test_publish_missing_webhook_does_not_call_make(app, module, monkeypatch):
    user = create_user(module)
    create_accounts(module, user, single_webhook="", carousel_webhook="")
    post = create_post(module, user)
    monkeypatch.setattr(
        publishing.requests,
        "post",
        lambda *args, **kwargs: pytest.fail("requests.post should not be called"),
    )

    with pytest.raises(Exception, match="No single-post webhook is configured"):
        publishing.publish_post_to_make(post, user.id)

    assert post.status == "draft"


def test_publish_failure_leaves_status_for_caller_rollback(app, module, monkeypatch):
    user = create_user(module)
    create_accounts(module, user)
    post = create_post(module, user)

    monkeypatch.setattr(
        publishing.requests,
        "post",
        lambda url, json, timeout: MockMakeResponse(status_code=500),
    )

    with pytest.raises(Exception, match="500 error"):
        publishing.publish_post_to_make(post, user.id)

    module.db.session.rollback()
    unchanged = module.db.session.get(Post, post.id)

    assert unchanged.status == "draft"
    assert unchanged.sent_at is None


def test_app_publish_wrapper_preserves_send_payload_monkeypatch(app, module, monkeypatch):
    user = create_user(module)
    create_accounts(module, user)
    post = create_post(module, user)
    calls = []

    def fake_send(payload, webhook_url=None):
        calls.append((payload, webhook_url))
        return MockMakeResponse()

    monkeypatch.setattr(module, "send_payload_to_make", fake_send)

    response = module.publish_post_to_make(post, user_id=user.id)

    assert response.status_code == 200
    assert calls[0][0]["post_type"] == "single"
    assert calls[0][1] == "https://make.test/single"


def test_scheduled_publish_caller_still_uses_extracted_service(app, module, monkeypatch):
    user = create_user(module)
    create_accounts(module, user, instagram=True)
    post = create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=utc_now() - timedelta(minutes=1),
        platforms="instagram",
    )
    sent = []

    monkeypatch.setattr(
        publishing.requests,
        "post",
        lambda url, json, timeout: sent.append((url, json, timeout)) or MockMakeResponse(),
    )

    module.check_scheduled_posts()
    module.db.session.expire_all()

    assert len(sent) == 1
    assert sent[0][1]["post_id"] == post.id
    assert module.db.session.get(Post, post.id).status == "sent_to_make"
