"""Application-level LinkedIn publishing orchestration."""

from dataclasses import dataclass
from typing import Any, Callable, Optional

from smu_core.services.platforms import linkedin
from smu_core.services.time_utils import utc_now


REQUIRED_MEMBER_SCOPE = "w_member_social"


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


def validate_text_only_eligibility(post: Any) -> str:
    """Validate that an SMU post can be sent as LinkedIn text-only content."""

    return _validate_text_post(post)


def _validate_account(connected_account: Any, *, now_provider: Callable[[], Any]) -> None:
    if connected_account is None or not connected_account.linkedin_connected:
        raise LinkedInPublishingError("LinkedIn is not connected.")

    if not connected_account.linkedin_access_token:
        raise LinkedInPublishingError("LinkedIn needs to be reconnected before publishing.")

    if not connected_account.linkedin_member_urn:
        raise LinkedInPublishingError("The selected LinkedIn publishing target is invalid. Reconnect LinkedIn.")

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
    commentary = (post.caption or "").strip()
    if not commentary:
        raise LinkedInPublishingError("LinkedIn text posts require a caption.")

    if getattr(post, "group_id", None):
        raise LinkedInPublishingError("LinkedIn carousel publishing is not available yet.")

    if getattr(post, "file_url", None):
        raise LinkedInPublishingError("LinkedIn image publishing is not available yet.")

    return commentary


def _message_for_linkedin_api_error(exc: linkedin.LinkedInAPIError) -> str:
    if exc.status_code == 401:
        return "LinkedIn needs to be reconnected before publishing."
    if exc.status_code == 403:
        return "LinkedIn did not grant the required publishing permission."
    if exc.status_code == 429:
        return "LinkedIn rate limit reached. Please try again later."
    return "LinkedIn could not create the post."
