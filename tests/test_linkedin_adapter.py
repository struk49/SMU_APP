import pytest

from smu_core.services.platforms import linkedin


PERSON_URN = "urn:li:person:abc123"
ORG_URN = "urn:li:organization:456"
IMAGE_URN = "urn:li:image:789"


class FakeResponse:
    def __init__(self, status_code=201, body=None, headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP failure")


def test_build_headers_contains_required_linkedin_values():
    headers = linkedin.build_headers("secret-token", api_version="202606")

    assert headers == {
        "Authorization": "Bearer secret-token",
        "Linkedin-Version": "202606",
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }


def test_build_headers_requires_token_and_version():
    with pytest.raises(ValueError):
        linkedin.build_headers("")

    with pytest.raises(ValueError):
        linkedin.build_headers("token", api_version="")


def test_linkedin_error_text_never_includes_access_token():
    token = "very-secret-token"
    response = FakeResponse(403, {"message": "Forbidden", "code": "ACCESS_DENIED"})

    with pytest.raises(linkedin.LinkedInAPIError) as exc_info:
        linkedin.create_text_post(
            token,
            PERSON_URN,
            "Hello LinkedIn",
            post_request=lambda *args, **kwargs: response,
        )

    assert token not in str(exc_info.value)
    assert token not in repr(exc_info.value)
    assert exc_info.value.stage == "create_text_post"
    assert exc_info.value.status_code == 403
    assert exc_info.value.error_category == "ACCESS_DENIED"


def test_author_validation_accepts_person_and_organization_urns():
    assert linkedin.validate_author_urn(PERSON_URN) == PERSON_URN
    assert linkedin.validate_author_urn(ORG_URN) == ORG_URN


@pytest.mark.parametrize(
    "author_urn",
    [
        "",
        "abc123",
        "urn:li:image:123",
        "urn:li:person:",
        "urn:li:company:123",
        None,
    ],
)
def test_author_validation_rejects_malformed_urns(author_urn):
    with pytest.raises(ValueError):
        linkedin.validate_author_urn(author_urn)


def test_text_payload_matches_posts_api_shape_and_preserves_text():
    text = "Line one\nLine two with punctuation."

    assert linkedin.build_text_post_payload(PERSON_URN, text) == {
        "author": PERSON_URN,
        "commentary": text,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }


def test_create_text_post_uses_endpoint_headers_payload_and_returns_stable_result():
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(
            201,
            {"id": "ignored-body-id"},
            {"x-restli-id": "urn:li:share:123"},
        )

    result = linkedin.create_text_post(
        "token",
        PERSON_URN,
        "Hello LinkedIn",
        post_request=fake_post,
        timeout=12,
    )

    assert result == linkedin.LinkedInPostResult(
        post_urn="urn:li:share:123",
        status_code=201,
        response_body={"id": "ignored-body-id"},
    )
    assert calls == [
        (
            linkedin.LINKEDIN_POSTS_ENDPOINT,
            {
                "headers": linkedin.build_headers("token"),
                "json": linkedin.build_text_post_payload(PERSON_URN, "Hello LinkedIn"),
                "timeout": 12,
            },
        )
    ]


def test_create_text_post_raises_for_non_2xx_response():
    response = FakeResponse(422, {"message": "Invalid post", "code": "VALIDATION"})

    with pytest.raises(linkedin.LinkedInAPIError) as exc_info:
        linkedin.create_text_post(
            "token",
            PERSON_URN,
            "Hello LinkedIn",
            post_request=lambda *args, **kwargs: response,
        )

    assert exc_info.value.stage == "create_text_post"
    assert exc_info.value.status_code == 422
    assert exc_info.value.error_category == "VALIDATION"


def test_initialize_image_upload_payload_matches_images_api_shape():
    assert linkedin.build_initialize_image_upload_payload(PERSON_URN) == {
        "initializeUploadRequest": {"owner": PERSON_URN}
    }


def test_initialize_image_upload_uses_endpoint_body_and_parses_result():
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(
            200,
            {
                "value": {
                    "uploadUrl": "https://upload.linkedin.com/image",
                    "image": IMAGE_URN,
                }
            },
        )

    result = linkedin.initialize_image_upload(
        "token",
        PERSON_URN,
        post_request=fake_post,
        timeout=15,
    )

    assert result == linkedin.LinkedInImageUpload(
        upload_url="https://upload.linkedin.com/image",
        image_urn=IMAGE_URN,
    )
    assert calls == [
        (
            linkedin.LINKEDIN_IMAGES_INITIALIZE_UPLOAD_ENDPOINT,
            {
                "headers": linkedin.build_headers("token"),
                "json": linkedin.build_initialize_image_upload_payload(PERSON_URN),
                "timeout": 15,
            },
        )
    ]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"value": {}},
        {"value": {"uploadUrl": "https://upload.linkedin.com/image"}},
        {"value": {"image": IMAGE_URN}},
        {"value": {"uploadUrl": "https://upload.linkedin.com/image", "image": "bad"}},
    ],
)
def test_initialize_image_upload_rejects_malformed_response(body):
    response = FakeResponse(200, body)

    with pytest.raises(linkedin.LinkedInAPIError):
        linkedin.initialize_image_upload(
            "token",
            PERSON_URN,
            post_request=lambda *args, **kwargs: response,
        )


def test_single_image_payload_maps_media_id_exactly():
    payload = linkedin.build_single_image_post_payload(
        PERSON_URN,
        "Image caption",
        IMAGE_URN,
    )

    assert payload == {
        "author": PERSON_URN,
        "commentary": "Image caption",
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
        "content": {"media": {"id": IMAGE_URN}},
    }


def test_adapter_does_not_log_binary_or_token(caplog):
    token = "secret-token"

    with pytest.raises(linkedin.LinkedInAPIError):
        linkedin.initialize_image_upload(
            token,
            PERSON_URN,
            post_request=lambda *args, **kwargs: FakeResponse(500, {"message": "Nope"}),
        )

    assert token not in caplog.text
    assert "binary" not in caplog.text.lower()
