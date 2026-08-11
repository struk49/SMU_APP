from datetime import timedelta

import pytest

from conftest import create_accounts, create_post, create_user, login
from smu_core.services import linkedin_publishing
from smu_core.services.platforms import linkedin
from smu_core.services.time_utils import utc_now


class FakeLinkedInResult:
    post_urn = "urn:li:share:123"
    status_code = 201


class FakeMakeResponse:
    status_code = 200


class FakeImageResponse:
    def __init__(self, content_type="image/jpeg", content=b"image-bytes", status_code=200):
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP failure")


def make_linkedin_account(module, user, **overrides):
    account = create_accounts(
        module,
        user,
        single_webhook="https://make.test/single",
        carousel_webhook="https://make.test/carousel",
        instagram=True,
        facebook=True,
    )
    account.linkedin_connected = overrides.pop("linkedin_connected", True)
    account.linkedin_access_token = overrides.pop("linkedin_access_token", "token-secret")
    account.linkedin_access_token_expires_at = overrides.pop(
        "linkedin_access_token_expires_at",
        utc_now() + timedelta(hours=1),
    )
    account.linkedin_scopes = overrides.pop("linkedin_scopes", "openid profile w_member_social")
    account.linkedin_member_id = overrides.pop("linkedin_member_id", "member123")
    account.linkedin_member_urn = overrides.pop(
        "linkedin_member_urn",
        "urn:li:person:member123",
    )
    for key, value in overrides.items():
        setattr(account, key, value)
    module.db.session.commit()
    return account


def make_text_only_post(module, user, *, platforms="linkedin", status="draft"):
    post = create_post(module, user, platforms=platforms, status=status)
    post.file_url = ""
    post.file_type = "text"
    module.db.session.commit()
    return post


def make_image_post(module, user, *, platforms="linkedin", status="draft"):
    post = create_post(module, user, platforms=platforms, status=status)
    post.file_url = "https://cdn.example.test/image.jpg"
    post.file_type = "image"
    module.db.session.commit()
    return post


def test_connected_valid_account_publishes_text_with_exact_caption_and_identity(app, module):
    user = create_user(module)
    account = make_linkedin_account(module, user)
    post = make_text_only_post(module, user)
    calls = []

    def fake_create_text_post(access_token, author_urn, commentary):
        calls.append((access_token, author_urn, commentary))
        return FakeLinkedInResult()

    result = linkedin_publishing.publish_text_only_post(
        post,
        account,
        create_text_post_func=fake_create_text_post,
    )

    assert result == linkedin_publishing.LinkedInPublishingResult(
        post_urn="urn:li:share:123",
        status_code=201,
    )
    assert calls == [("token-secret", "urn:li:person:member123", "Caption")]


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("linkedin_connected", False, "LinkedIn is not connected."),
        ("linkedin_access_token", "", "LinkedIn needs to be reconnected before publishing."),
        ("linkedin_member_urn", "", "publishing target is invalid"),
        (
            "linkedin_access_token_expires_at",
            utc_now() - timedelta(minutes=1),
            "LinkedIn needs to be reconnected",
        ),
        (
            "linkedin_scopes",
            "openid profile",
            "required publishing permission",
        ),
    ],
)
def test_invalid_linkedin_account_fails_before_adapter(app, module, field, value, message):
    user = create_user(module)
    account = make_linkedin_account(module, user, **{field: value})
    post = make_text_only_post(module, user)
    calls = []

    with pytest.raises(linkedin_publishing.LinkedInPublishingError) as exc_info:
        linkedin_publishing.publish_text_only_post(
            post,
            account,
            create_text_post_func=lambda *args, **kwargs: calls.append(args),
        )

    assert message in str(exc_info.value)
    assert calls == []


def test_missing_scope_is_allowed_when_linkedin_scope_data_is_absent(app, module):
    user = create_user(module)
    account = make_linkedin_account(module, user, linkedin_scopes="")
    post = make_text_only_post(module, user)
    calls = []

    linkedin_publishing.publish_text_only_post(
        post,
        account,
        create_text_post_func=lambda *args, **kwargs: calls.append(args) or FakeLinkedInResult(),
    )

    assert len(calls) == 1


def test_empty_caption_and_media_posts_fail_before_adapter(app, module):
    user = create_user(module)
    account = make_linkedin_account(module, user)
    blank_post = make_text_only_post(module, user)
    blank_post.caption = "  "
    media_post = create_post(module, user, platforms="linkedin")
    module.db.session.commit()
    calls = []

    with pytest.raises(linkedin_publishing.LinkedInPublishingError) as blank_error:
        linkedin_publishing.publish_text_only_post(
            blank_post,
            account,
            create_text_post_func=lambda *args, **kwargs: calls.append(args),
        )
    with pytest.raises(linkedin_publishing.LinkedInPublishingError) as media_error:
        linkedin_publishing.publish_text_only_post(
            media_post,
            account,
            create_text_post_func=lambda *args, **kwargs: calls.append(args),
        )

    assert "require a caption" in str(blank_error.value)
    assert "image publishing is not available yet" in str(media_error.value)
    assert calls == []


def test_generic_linkedin_image_publish_downloads_media_and_calls_image_adapter(app, module):
    user = create_user(module)
    account = make_linkedin_account(module, user)
    post = make_image_post(module, user)
    get_calls = []
    image_calls = []
    text_calls = []

    result = linkedin_publishing.publish_post(
        post,
        account,
        fetch_image_media_func=lambda url: (
            get_calls.append(url)
            or linkedin_publishing.LinkedInMediaDownload(
                image_bytes=b"png-bytes",
                content_type="image/png",
                byte_length=len(b"png-bytes"),
            )
        ),
        create_text_post_func=lambda *args, **kwargs: text_calls.append(args),
        create_single_image_post_func=(
            lambda *args, **kwargs: image_calls.append(args) or FakeLinkedInResult()
        ),
    )

    assert result.status_code == 201
    assert get_calls == ["https://cdn.example.test/image.jpg"]
    assert text_calls == []
    assert image_calls == [
        (
            "token-secret",
            "urn:li:person:member123",
            "Caption",
            b"png-bytes",
            "image/png",
        )
    ]


@pytest.mark.parametrize("content_type", ["image/jpeg", "image/png", "image/gif"])
def test_linkedin_image_media_accepts_supported_content_types(content_type):
    media = linkedin_publishing.fetch_image_media(
        "https://cdn.example.test/image",
        get_request=lambda *args, **kwargs: FakeImageResponse(content_type=content_type),
    )

    assert media.content_type == content_type
    assert media.image_bytes == b"image-bytes"
    assert media.byte_length == len(b"image-bytes")


def test_linkedin_image_media_rejects_unsupported_content_type():
    with pytest.raises(linkedin_publishing.LinkedInPublishingError) as exc_info:
        linkedin_publishing.fetch_image_media(
            "https://cdn.example.test/image",
            get_request=lambda *args, **kwargs: FakeImageResponse(
                content_type="image/webp",
            ),
        )

    assert "JPEG, PNG and GIF" in str(exc_info.value)


def test_linkedin_image_post_without_url_fails_before_adapter(app, module):
    user = create_user(module)
    account = make_linkedin_account(module, user)
    post = make_image_post(module, user)
    post.file_url = ""
    module.db.session.commit()
    calls = []

    with pytest.raises(linkedin_publishing.LinkedInPublishingError) as exc_info:
        linkedin_publishing.publish_post(
            post,
            account,
            create_single_image_post_func=lambda *args, **kwargs: calls.append(args),
        )

    assert "image URL" in str(exc_info.value)
    assert calls == []


def test_linkedin_carousel_and_video_remain_unsupported(app, module):
    user = create_user(module)
    account = make_linkedin_account(module, user)
    carousel_post = make_image_post(module, user)
    carousel_post.group_id = "carousel-1"
    video_post = make_image_post(module, user)
    video_post.file_type = "video"
    module.db.session.commit()

    with pytest.raises(linkedin_publishing.LinkedInPublishingError) as carousel_error:
        linkedin_publishing.publish_post(carousel_post, account)
    with pytest.raises(linkedin_publishing.LinkedInPublishingError) as video_error:
        linkedin_publishing.publish_post(video_post, account)

    assert "carousel publishing is not available yet" in str(carousel_error.value)
    assert "single-image" in str(video_error.value)


def test_adapter_error_is_translated_without_token(app, module):
    user = create_user(module)
    account = make_linkedin_account(module, user)
    post = make_text_only_post(module, user)

    def fail_adapter(*args, **kwargs):
        raise linkedin.LinkedInAPIError(
            "create_text_post",
            "Nope",
            status_code=401,
            error_category="invalid_token",
        )

    with pytest.raises(linkedin_publishing.LinkedInPublishingError) as exc_info:
        linkedin_publishing.publish_text_only_post(
            post,
            account,
            create_text_post_func=fail_adapter,
        )

    assert "LinkedIn needs to be reconnected" in str(exc_info.value)
    assert "token-secret" not in str(exc_info.value)
    assert exc_info.value.status_code == 401
    assert exc_info.value.error_category == "invalid_token"


def test_linkedin_only_publish_does_not_require_make_and_marks_published(app, module):
    user = create_user(module)
    make_linkedin_account(module, user)
    post = make_text_only_post(module, user, platforms="linkedin")
    linkedin_calls = []
    make_calls = []

    from smu_core.services import publishing

    result = publishing.publish_post_to_make(
        post,
        user.id,
        send_payload_func=lambda *args, **kwargs: make_calls.append(args),
        publish_linkedin_text_post_func=lambda *args, **kwargs: linkedin_calls.append(args) or FakeLinkedInResult(),
    )

    assert result is not None
    assert len(linkedin_calls) == 1
    assert make_calls == []
    assert post.status == "published"
    assert post.sent_at is not None


def test_instagram_only_uses_make_and_not_linkedin(app, module):
    from smu_core.services import publishing

    user = create_user(module)
    make_linkedin_account(module, user)
    post = create_post(module, user, platforms="instagram")
    make_calls = []
    linkedin_calls = []

    publishing.publish_post_to_make(
        post,
        user.id,
        send_payload_func=lambda payload, webhook_url: make_calls.append((payload, webhook_url)) or FakeMakeResponse(),
        publish_linkedin_text_post_func=lambda *args, **kwargs: linkedin_calls.append(args),
    )

    assert make_calls[0][0]["platforms"] == ["instagram"]
    assert make_calls[0][1] == "https://make.test/single"
    assert linkedin_calls == []
    assert post.status == "sent_to_make"


def test_mixed_text_platforms_send_make_platforms_in_order_and_linkedin_once(app, module):
    from smu_core.services import publishing

    user = create_user(module)
    make_linkedin_account(module, user)
    post = make_text_only_post(
        module,
        user,
        platforms="facebook,linkedin",
    )
    make_calls = []
    linkedin_calls = []

    publishing.publish_post_to_make(
        post,
        user.id,
        send_payload_func=lambda payload, webhook_url: make_calls.append((payload, webhook_url)) or FakeMakeResponse(),
        publish_linkedin_text_post_func=lambda *args, **kwargs: linkedin_calls.append(args) or FakeLinkedInResult(),
    )

    assert make_calls[0][0]["platforms"] == ["facebook"]
    assert len(linkedin_calls) == 1
    assert post.status == "sent_to_make"
    assert post.sent_at is not None


def test_mixed_media_post_with_linkedin_fails_before_make_or_linkedin(app, module):
    from smu_core.services import publishing

    user = create_user(module)
    make_linkedin_account(module, user)
    post = create_post(module, user, platforms="instagram,linkedin")
    make_calls = []
    linkedin_calls = []

    publishing.publish_post_to_make(
        post,
        user.id,
        send_payload_func=lambda payload, webhook_url: (
            make_calls.append((payload, webhook_url)) or FakeMakeResponse()
        ),
        prepare_linkedin_post_func=lambda *args, **kwargs: "prepared-linkedin-image",
        publish_prepared_linkedin_post_func=lambda *args, **kwargs: (
            linkedin_calls.append(args) or FakeLinkedInResult()
        ),
    )

    assert make_calls[0][0]["platforms"] == ["instagram"]
    assert make_calls[0][1] == "https://make.test/single"
    assert linkedin_calls == [("prepared-linkedin-image",)]
    assert post.status == "sent_to_make"


def test_mixed_instagram_facebook_linkedin_image_preserves_make_order_and_linkedin_once(
    app,
    module,
):
    from smu_core.services import publishing

    user = create_user(module)
    make_linkedin_account(module, user)
    post = make_image_post(module, user, platforms="instagram,facebook,linkedin")
    make_calls = []
    linkedin_calls = []

    publishing.publish_post_to_make(
        post,
        user.id,
        send_payload_func=lambda payload, webhook_url: (
            make_calls.append((payload, webhook_url)) or FakeMakeResponse()
        ),
        prepare_linkedin_post_func=lambda *args, **kwargs: "prepared-linkedin-image",
        publish_prepared_linkedin_post_func=lambda *args, **kwargs: (
            linkedin_calls.append(args) or FakeLinkedInResult()
        ),
    )

    assert make_calls[0][0]["platforms"] == ["instagram", "facebook"]
    assert linkedin_calls == [("prepared-linkedin-image",)]
    assert post.status == "sent_to_make"


def test_linkedin_only_single_image_does_not_require_make_and_marks_published(
    app,
    module,
):
    from smu_core.services import publishing

    user = create_user(module)
    make_linkedin_account(module, user)
    post = make_image_post(module, user, platforms="linkedin")
    make_calls = []
    linkedin_calls = []

    publishing.publish_post_to_make(
        post,
        user.id,
        send_payload_func=lambda *args, **kwargs: make_calls.append(args),
        prepare_linkedin_post_func=lambda *args, **kwargs: "prepared-linkedin-image",
        publish_prepared_linkedin_post_func=lambda *args, **kwargs: (
            linkedin_calls.append(args) or FakeLinkedInResult()
        ),
    )

    assert make_calls == []
    assert linkedin_calls == [("prepared-linkedin-image",)]
    assert post.status == "published"
    assert post.sent_at is not None


def test_invalid_linkedin_image_media_in_mixed_post_blocks_make_and_linkedin(app, module):
    from smu_core.services import publishing

    user = create_user(module)
    make_linkedin_account(module, user)
    post = make_image_post(module, user, platforms="instagram,linkedin")
    make_calls = []
    linkedin_calls = []

    with pytest.raises(linkedin_publishing.LinkedInPublishingError) as exc_info:
        publishing.publish_post_to_make(
            post,
            user.id,
            send_payload_func=lambda *args, **kwargs: make_calls.append(args),
            prepare_linkedin_post_func=lambda *args, **kwargs: (
                (_ for _ in ()).throw(
                    linkedin_publishing.LinkedInPublishingError(
                        "LinkedIn supports JPEG, PNG and GIF images only.",
                        stage="media_fetch",
                    )
                )
            ),
            publish_prepared_linkedin_post_func=lambda *args, **kwargs: (
                linkedin_calls.append(args) or FakeLinkedInResult()
            ),
        )

    assert "JPEG, PNG and GIF" in str(exc_info.value)
    assert make_calls == []
    assert linkedin_calls == []


def test_mixed_instagram_text_post_fails_before_make_or_linkedin(app, module):
    from smu_core.services import publishing

    user = create_user(module)
    make_linkedin_account(module, user)
    post = make_text_only_post(module, user, platforms="instagram,linkedin")
    make_calls = []
    linkedin_calls = []

    with pytest.raises(Exception) as exc_info:
        publishing.publish_post_to_make(
            post,
            user.id,
            send_payload_func=lambda *args, **kwargs: make_calls.append(args),
            publish_linkedin_text_post_func=lambda *args, **kwargs: linkedin_calls.append(args),
        )

    assert "Instagram single-image posts require an image URL" in str(exc_info.value)
    assert make_calls == []
    assert linkedin_calls == []
    assert post.status == "draft"


def test_disconnected_linkedin_mixed_with_instagram_fails_before_make(app, module):
    from smu_core.services import publishing

    user = create_user(module)
    make_linkedin_account(module, user, linkedin_connected=False)
    post = make_text_only_post(module, user, platforms="instagram,linkedin")
    make_calls = []

    with pytest.raises(linkedin_publishing.LinkedInPublishingError):
        publishing.publish_post_to_make(
            post,
            user.id,
            send_payload_func=lambda *args, **kwargs: make_calls.append(args),
        )

    assert make_calls == []
    assert post.status == "draft"


def test_manual_route_owner_linkedin_only_publish_calls_adapter_once(
    client,
    app,
    module,
    monkeypatch,
):
    user = create_user(module)
    make_linkedin_account(module, user)
    post = make_text_only_post(module, user, platforms="linkedin")
    login(client, user)
    calls = []

    monkeypatch.setattr(
        module,
        "publish_prepared_linkedin_post",
        lambda *args, **kwargs: calls.append(args) or FakeLinkedInResult(),
    )

    response = client.post(f"/send/{post.id}", follow_redirects=True)
    updated = module.db.session.get(module.Post, post.id)

    assert response.status_code == 200
    assert "Post published successfully." in response.get_data(as_text=True)
    assert len(calls) == 1
    assert updated.status == "published"


def test_create_route_can_create_linkedin_text_only_draft(client, app, module):
    user = create_user(module)
    login(client, user)

    response = client.post(
        "/create",
        data={
            "caption": "A LinkedIn text post",
            "platforms": ["linkedin"],
        },
        follow_redirects=True,
    )
    post = module.Post.query.filter_by(user_id=user.id).first()

    assert response.status_code == 200
    assert "LinkedIn text post created successfully." in response.get_data(as_text=True)
    assert post.file_url == ""
    assert post.file_type == "text"
    assert post.caption == "A LinkedIn text post"
    assert post.platforms == "linkedin"
    assert post.status == "draft"


def test_create_route_rejects_blank_linkedin_text_only_caption(client, app, module):
    user = create_user(module)
    login(client, user)

    response = client.post(
        "/create",
        data={
            "caption": " ",
            "platforms": ["linkedin"],
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "LinkedIn text posts require a caption." in response.get_data(as_text=True)
    assert module.Post.query.filter_by(user_id=user.id).count() == 0


def test_manual_route_blocks_already_published_post(client, app, module, monkeypatch):
    user = create_user(module)
    post = make_text_only_post(module, user, platforms="linkedin", status="published")
    login(client, user)
    calls = []

    monkeypatch.setattr(
        module,
        "publish_prepared_linkedin_post",
        lambda *args, **kwargs: calls.append(args),
    )

    response = client.post(f"/send/{post.id}", follow_redirects=True)

    assert response.status_code == 200
    assert "This post has already been published." in response.get_data(as_text=True)
    assert calls == []


def test_scheduled_linkedin_only_post_invokes_linkedin_once(app, module, monkeypatch):
    user = create_user(module)
    make_linkedin_account(module, user)
    post = make_text_only_post(module, user, platforms="linkedin", status="scheduled")
    post.scheduled_time = utc_now() - timedelta(minutes=1)
    module.db.session.commit()
    calls = []

    monkeypatch.setattr(
        module,
        "publish_prepared_linkedin_post",
        lambda *args, **kwargs: calls.append(args) or FakeLinkedInResult(),
    )

    module.check_scheduled_posts()
    module.check_scheduled_posts()
    updated = module.db.session.get(module.Post, post.id)

    assert len(calls) == 1
    assert updated.status == "published"
    assert updated.sent_at is not None


def test_scheduled_mixed_post_rejects_unsatisfied_instagram_text_before_channels(
    app,
    module,
    monkeypatch,
):
    user = create_user(module)
    make_linkedin_account(module, user)
    post = make_text_only_post(
        module,
        user,
        platforms="instagram,linkedin",
        status="scheduled",
    )
    post.scheduled_time = utc_now() - timedelta(minutes=1)
    module.db.session.commit()
    make_calls = []
    linkedin_calls = []

    monkeypatch.setattr(
        module,
        "send_payload_to_make",
        lambda payload, webhook_url=None: make_calls.append((payload, webhook_url)) or FakeMakeResponse(),
    )
    monkeypatch.setattr(
        module,
        "publish_prepared_linkedin_post",
        lambda *args, **kwargs: linkedin_calls.append(args) or FakeLinkedInResult(),
    )

    module.check_scheduled_posts()
    updated = module.db.session.get(module.Post, post.id)

    assert make_calls == []
    assert linkedin_calls == []
    assert updated.status == "schedule_failed"


def test_scheduled_linkedin_failure_uses_existing_schedule_failed_policy(
    app,
    module,
    monkeypatch,
):
    user = create_user(module)
    make_linkedin_account(module, user, linkedin_connected=False)
    post = make_text_only_post(module, user, platforms="linkedin", status="scheduled")
    post.scheduled_time = utc_now() - timedelta(minutes=1)
    module.db.session.commit()

    module.check_scheduled_posts()
    updated = module.db.session.get(module.Post, post.id)

    assert updated.status == "schedule_failed"


def test_scheduled_linkedin_only_image_post_invokes_linkedin_once(
    app,
    module,
    monkeypatch,
):
    user = create_user(module)
    make_linkedin_account(module, user)
    post = make_image_post(module, user, platforms="linkedin", status="scheduled")
    post.scheduled_time = utc_now() - timedelta(minutes=1)
    module.db.session.commit()
    calls = []

    monkeypatch.setattr(
        module,
        "prepare_linkedin_post",
        lambda *args, **kwargs: "prepared-linkedin-image",
    )
    monkeypatch.setattr(
        module,
        "publish_prepared_linkedin_post",
        lambda *args, **kwargs: calls.append(args) or FakeLinkedInResult(),
    )

    module.check_scheduled_posts()
    module.check_scheduled_posts()
    updated = module.db.session.get(module.Post, post.id)

    assert calls == [("prepared-linkedin-image",)]
    assert updated.status == "published"
    assert updated.sent_at is not None
