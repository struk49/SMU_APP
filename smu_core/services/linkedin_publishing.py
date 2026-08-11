"""Application-level LinkedIn publishing orchestration."""

from dataclasses import dataclass
from typing import Any, Callable, Optional

import requests

from smu_core.services.platforms import linkedin
from smu_core.services.time_utils import utc_now


REQUIRED_MEMBER_SCOPE = "w_member_social"
SUPPORTED_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/gif"}


class LinkedInPublishingError(RuntimeError):
    """Raised for user-safe LinkedIn publishing failures."""

    def __init__(
        self,
        message: str,
        *,
        stage: str = "linkedin_publish",
        status_code: Optional[int] = None,
        error_category: Optional[str] = None,
    ) -> None:
        self.stage = stage
        self.status_code = status_code
        self.error_category = error_category
        super().__init__(message)


@dataclass(frozen=True)
class LinkedInPublishingResult:
    post_urn: Optional[str]
    status_code: int


@dataclass(frozen=True)
class LinkedInMediaDownload:
    image_bytes: bytes
    content_type: str
    byte_length: int


@dataclass(frozen=True)
class LinkedInPreparedPublish:
    post: Any
    connected_account: Any
    mode: str
    commentary: str
    media: Optional[LinkedInMediaDownload] = None


def prepare_post_for_publish(
    post: Any,
    connected_account: Any,
    *,
    fetch_image_media_func: Callable[..., LinkedInMediaDownload] = None,
    now_provider: Callable[[], Any] = utc_now,
) -> LinkedInPreparedPublish:
    """Validate and prepare LinkedIn publishing before other channels are sent."""

    if fetch_image_media_func is None:
        fetch_image_media_func = fetch_image_media

    _validate_account(connected_account, now_provider=now_provider)
    mode, commentary = validate_post_eligibility(post)
    media = None

    if mode == "image":
        media = fetch_image_media_func(post.file_url)

    return LinkedInPreparedPublish(
        post=post,
        connected_account=connected_account,
        mode=mode,
        commentary=commentary,
        media=media,
    )


def publish_prepared_post(
    prepared: LinkedInPreparedPublish,
    *,
    create_text_post_func: Callable[..., Any] = linkedin.create_text_post,
    create_single_image_post_func: Callable[..., Any] = linkedin.create_single_image_post,
) -> LinkedInPublishingResult:
    """Publish a previously validated and prepared LinkedIn post."""

    account = prepared.connected_account

    try:
        if prepared.mode == "image":
            if prepared.media is None:
                raise LinkedInPublishingError(
                    "LinkedIn image posts require an image URL.",
                    stage="media_fetch",
                )
            result = create_single_image_post_func(
                account.linkedin_access_token,
                account.linkedin_member_urn,
                prepared.commentary,
                prepared.media.image_bytes,
                prepared.media.content_type,
            )
        else:
            result = create_text_post_func(
                account.linkedin_access_token,
                account.linkedin_member_urn,
                prepared.commentary,
            )
    except linkedin.LinkedInAPIError as exc:
        raise LinkedInPublishingError(
            _message_for_linkedin_api_error(exc),
            stage=getattr(exc, "stage", "linkedin_publish"),
            status_code=getattr(exc, "status_code", None),
            error_category=getattr(exc, "error_category", None),
        ) from exc

    return LinkedInPublishingResult(
        post_urn=getattr(result, "post_urn", None),
        status_code=getattr(result, "status_code", 0),
    )


def publish_post(
    post: Any,
    connected_account: Any,
    *,
    fetch_image_media_func: Callable[..., LinkedInMediaDownload] = None,
    create_text_post_func: Callable[..., Any] = linkedin.create_text_post,
    create_single_image_post_func: Callable[..., Any] = linkedin.create_single_image_post,
    now_provider: Callable[[], Any] = utc_now,
) -> LinkedInPublishingResult:
    """Publish one SMU post as a LinkedIn personal-profile post."""

    prepared = prepare_post_for_publish(
        post,
        connected_account,
        fetch_image_media_func=fetch_image_media_func,
        now_provider=now_provider,
    )
    return publish_prepared_post(
        prepared,
        create_text_post_func=create_text_post_func,
        create_single_image_post_func=create_single_image_post_func,
    )


def publish_text_only_post(
    post: Any,
    connected_account: Any,
    *,
    create_text_post_func: Callable[..., Any] = linkedin.create_text_post,
    now_provider: Callable[[], Any] = utc_now,
) -> LinkedInPublishingResult:
    """Publish one SMU post as a LinkedIn personal-profile text post."""

    _validate_account(connected_account, now_provider=now_provider)
    commentary = validate_text_only_eligibility(post)

    try:
        result = create_text_post_func(
            connected_account.linkedin_access_token,
            connected_account.linkedin_member_urn,
            commentary,
        )
    except linkedin.LinkedInAPIError as exc:
        raise LinkedInPublishingError(
            _message_for_linkedin_api_error(exc),
            stage=getattr(exc, "stage", "create_text_post"),
            status_code=getattr(exc, "status_code", None),
            error_category=getattr(exc, "error_category", None),
        ) from exc

    return LinkedInPublishingResult(
        post_urn=getattr(result, "post_urn", None),
        status_code=getattr(result, "status_code", 0),
    )


def fetch_image_media(
    file_url: str,
    *,
    get_request: Callable[..., Any] = requests.get,
    timeout: int = 30,
) -> LinkedInMediaDownload:
    """Fetch image bytes from a public URL and validate LinkedIn-supported media."""

    if not file_url:
        raise LinkedInPublishingError(
            "LinkedIn image posts require an image URL.",
            stage="media_fetch",
        )

    try:
        response = get_request(file_url, timeout=timeout)
    except requests.RequestException as exc:
        raise LinkedInPublishingError(
            "LinkedIn could not download the image.",
            stage="media_fetch",
        ) from exc

    status_code = getattr(response, "status_code", None)
    if status_code is not None and status_code >= 400:
        raise LinkedInPublishingError(
            "LinkedIn could not download the image.",
            stage="media_fetch",
            status_code=status_code,
        )

    raise_for_status = getattr(response, "raise_for_status", None)
    if callable(raise_for_status):
        try:
            raise_for_status()
        except requests.RequestException as exc:
            raise LinkedInPublishingError(
                "LinkedIn could not download the image.",
                stage="media_fetch",
                status_code=status_code,
            ) from exc

    headers = getattr(response, "headers", {}) or {}
    content_type = (headers.get("Content-Type") or headers.get("content-type") or "")
    content_type = content_type.split(";", 1)[0].strip().lower()
    if content_type not in SUPPORTED_IMAGE_CONTENT_TYPES:
        raise LinkedInPublishingError(
            "LinkedIn supports JPEG, PNG and GIF images only.",
            stage="media_fetch",
            error_category="unsupported_content_type",
        )

    image_bytes = getattr(response, "content", b"") or b""
    if not image_bytes:
        raise LinkedInPublishingError(
            "LinkedIn image download returned no data.",
            stage="media_fetch",
            error_category="empty_media",
        )

    return LinkedInMediaDownload(
        image_bytes=image_bytes,
        content_type=content_type,
        byte_length=len(image_bytes),
    )


def validate_post_eligibility(post: Any):
    """Validate that an SMU post can be sent to LinkedIn in this slice."""

    commentary = _validate_caption(post)

    if getattr(post, "group_id", None):
        raise LinkedInPublishingError("LinkedIn carousel publishing is not available yet.")

    file_url = (getattr(post, "file_url", None) or "").strip()
    file_type = (getattr(post, "file_type", None) or "").strip().lower()

    if file_type == "image":
        if not file_url:
            raise LinkedInPublishingError("LinkedIn image posts require an image URL.")
        return "image", commentary

    if file_url:
        raise LinkedInPublishingError(
            "LinkedIn supports only text and single-image posts in this slice."
        )

    if file_type and file_type != "text":
        raise LinkedInPublishingError("LinkedIn video publishing is not available yet.")

    return "text", commentary


def validate_text_only_eligibility(post: Any) -> str:
    """Validate that an SMU post can be sent as LinkedIn text-only content."""

    return _validate_text_post(post)


def _validate_account(connected_account: Any, *, now_provider: Callable[[], Any]) -> None:
    if connected_account is None or not connected_account.linkedin_connected:
        raise LinkedInPublishingError("LinkedIn is not connected.")

    if not connected_account.linkedin_access_token:
        raise LinkedInPublishingError("LinkedIn needs to be reconnected before publishing.")

    if not connected_account.linkedin_member_urn:
        raise LinkedInPublishingError(
            "The selected LinkedIn publishing target is invalid. Reconnect LinkedIn."
        )

    scopes = {
        scope.strip()
        for scope in (connected_account.linkedin_scopes or "").replace(",", " ").split()
        if scope.strip()
    }
    if connected_account.linkedin_scopes and REQUIRED_MEMBER_SCOPE not in scopes:
        raise LinkedInPublishingError("LinkedIn did not grant the required publishing permission.")

    expires_at = connected_account.linkedin_access_token_expires_at
    if expires_at is not None and expires_at <= now_provider():
        raise LinkedInPublishingError("LinkedIn needs to be reconnected before publishing.")


def _validate_text_post(post: Any) -> str:
    commentary = _validate_caption(post)

    if getattr(post, "group_id", None):
        raise LinkedInPublishingError("LinkedIn carousel publishing is not available yet.")

    if getattr(post, "file_url", None):
        raise LinkedInPublishingError("LinkedIn image publishing is not available yet.")

    return commentary


def _validate_caption(post: Any) -> str:
    commentary = (post.caption or "").strip()
    if not commentary:
        raise LinkedInPublishingError("LinkedIn text posts require a caption.")
    return commentary


def _message_for_linkedin_api_error(exc: linkedin.LinkedInAPIError) -> str:
    if exc.status_code == 401:
        return "LinkedIn needs to be reconnected before publishing."
    if exc.status_code == 403:
        return "LinkedIn did not grant the required publishing permission."
    if exc.status_code == 429:
        return "LinkedIn rate limit reached. Please try again later."
    return "LinkedIn could not create the post."
