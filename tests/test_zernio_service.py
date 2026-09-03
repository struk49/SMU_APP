import pytest

from smu_core.services import zernio


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP failure")


class FakePost:
    id = 42
    user_id = 7
    caption = "Caption"
    file_url = "https://res.cloudinary.com/demo/image/upload/post.jpg"
    file_type = "image"
    platforms = "instagram,facebook"
    group_id = None
    post_type = "single"


class FakeAccount:
    zernio_profile_id = "prof_123"
    zernio_instagram_account_id = "acct_ig"
    zernio_facebook_account_id = "acct_fb"


def test_create_profile_uses_official_endpoint_and_bearer_auth():
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return FakeResponse(201, {"profile": {"_id": "prof_123"}})

    profile = zernio.create_profile(
        "SMU user 1",
        description="POC",
        api_key="sk_test",
        base_url="https://zernio.test/api/v1",
        request_func=fake_request,
    )

    assert profile["_id"] == "prof_123"
    assert calls[0][0] == "POST"
    assert calls[0][1] == "https://zernio.test/api/v1/profiles"
    assert calls[0][2]["headers"]["Authorization"] == "Bearer sk_test"
    assert "Idempotency-Key" in calls[0][2]["headers"]
    assert calls[0][2]["json"] == {"name": "SMU user 1", "description": "POC"}


def test_connection_url_generation_uses_profile_and_redirect():
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return FakeResponse(200, {"authUrl": "https://connect.zernio.test/start"})

    connect_url = zernio.create_connection_url(
        profile_id="prof_123",
        platform="instagram",
        redirect_url="https://smu.test/accounts/zernio/callback",
        api_key="sk_test",
        base_url="https://zernio.test/api/v1",
        request_func=fake_request,
    )

    assert connect_url == "https://connect.zernio.test/start"
    assert calls[0][0] == "GET"
    assert calls[0][1] == "https://zernio.test/api/v1/connect/instagram"
    assert calls[0][2]["params"] == {
        "profileId": "prof_123",
        "redirect_url": "https://smu.test/accounts/zernio/callback",
    }


def test_successful_single_image_publish_uses_existing_cloudinary_url():
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return FakeResponse(
            201,
            {
                "post": {
                    "_id": "zp_123",
                    "status": "publishing",
                    "platforms": [
                        {"platform": "instagram", "platformPostUrl": None},
                        {"platform": "facebook", "platformPostUrl": None},
                    ],
                }
            },
        )

    result = zernio.publish_single_image(
        FakePost(),
        FakeAccount(),
        api_key="sk_test",
        base_url="https://zernio.test/api/v1",
        request_func=fake_request,
    )

    payload = calls[0][2]["json"]
    assert result.provider_post_id == "zp_123"
    assert result.status == "publishing"
    assert calls[0][0] == "POST"
    assert calls[0][1] == "https://zernio.test/api/v1/posts"
    assert payload["publishNow"] is True
    assert payload["content"] == "Caption"
    assert payload["mediaItems"] == [
        {
            "type": "image",
            "url": "https://res.cloudinary.com/demo/image/upload/post.jpg",
            "title": "SMU post 42 image",
        }
    ]
    assert payload["platforms"] == [
        {"platform": "instagram", "accountId": "acct_ig"},
        {"platform": "facebook", "accountId": "acct_fb"},
    ]
    assert "x-request-id" in calls[0][2]["headers"]


def test_publish_rejects_missing_api_key_before_network():
    calls = []

    with pytest.raises(zernio.ZernioError, match="not configured"):
        zernio.publish_single_image(
            FakePost(),
            FakeAccount(),
            api_key="",
            request_func=lambda *args, **kwargs: calls.append(args),
        )

    assert calls == []


def test_publish_rejects_non_public_media_url():
    class LocalPost(FakePost):
        file_url = "http://localhost/image.jpg"

    with pytest.raises(zernio.ZernioError, match="public HTTPS image URL"):
        zernio.publish_single_image(
            LocalPost(),
            FakeAccount(),
            api_key="sk_test",
        )


def test_publish_rejects_carousel_posts():
    class CarouselPost(FakePost):
        group_id = "group-1"
        post_type = "carousel"

    with pytest.raises(zernio.ZernioError, match="single-image"):
        zernio.publish_single_image(
            CarouselPost(),
            FakeAccount(),
            api_key="sk_test",
        )


def test_api_rejection_is_safe_and_does_not_expose_api_key():
    def fake_request(method, url, **kwargs):
        return FakeResponse(429, {"error": "Rate limited", "code": "rate_limit"})

    with pytest.raises(zernio.ZernioError) as exc_info:
        zernio.publish_single_image(
            FakePost(),
            FakeAccount(),
            api_key="sk_secret",
            base_url="https://zernio.test/api/v1",
            request_func=fake_request,
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.error_category == "rate_limit"
    assert "sk_secret" not in str(exc_info.value)
