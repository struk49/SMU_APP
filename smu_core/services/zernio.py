"""Small Zernio provider adapter for the direct-publishing POC."""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import urlparse
from uuid import uuid4

import requests


DEFAULT_BASE_URL = "https://zernio.com/api/v1"
SUPPORTED_POC_PLATFORMS = {"instagram", "facebook"}


class ZernioError(RuntimeError):
    """Raised for user-safe Zernio POC failures."""

    def __init__(
        self,
        message: str,
        *,
        stage: str = "zernio",
        status_code: Optional[int] = None,
        error_category: Optional[str] = None,
    ) -> None:
        self.stage = stage
        self.status_code = status_code
        self.error_category = error_category
        super().__init__(message)


@dataclass(frozen=True)
class ZernioPublishResult:
    provider_post_id: Optional[str]
    status: str
    platforms: List[str]
    published_url: Optional[str] = None
    error: Optional[str] = None


def create_profile(
    name: str,
    *,
    description: str = "",
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    request_func: Callable[..., Any] = requests.request,
    timeout: int = 30,
) -> Dict[str, Any]:
    response = _request(
        "POST",
        "/profiles",
        api_key=api_key,
        base_url=base_url,
        request_func=request_func,
        timeout=timeout,
        headers={"Idempotency-Key": str(uuid4())},
        json={"name": name, "description": description},
        stage="create_profile",
    )
    profile = response.get("profile")
    if not isinstance(profile, dict) or not profile.get("_id"):
        raise ZernioError(
            "Zernio did not return a profile identifier.",
            stage="create_profile",
            error_category="malformed_response",
        )
    return profile


def ensure_profile_for_user(
    user: Any,
    connected_account: Any,
    *,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    request_func: Callable[..., Any] = requests.request,
    timeout: int = 30,
) -> str:
    if connected_account.zernio_profile_id:
        return connected_account.zernio_profile_id

    profile = create_profile(
        f"SMU user {user.id}",
        description="SMU Zernio publishing POC profile",
        api_key=api_key,
        base_url=base_url,
        request_func=request_func,
        timeout=timeout,
    )
    connected_account.zernio_profile_id = str(profile["_id"])
    return connected_account.zernio_profile_id


def create_connection_url(
    *,
    profile_id: str,
    platform: str,
    redirect_url: str,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    request_func: Callable[..., Any] = requests.request,
    timeout: int = 30,
) -> str:
    platform = _normalise_platform(platform)
    if platform not in SUPPORTED_POC_PLATFORMS:
        raise ZernioError("This Zernio POC supports Instagram and Facebook only.")

    response = _request(
        "GET",
        f"/connect/{platform}",
        api_key=api_key,
        base_url=base_url,
        request_func=request_func,
        timeout=timeout,
        params={"profileId": profile_id, "redirect_url": redirect_url},
        stage="create_connection_url",
    )
    auth_url = response.get("authUrl") or response.get("url")
    if not auth_url:
        raise ZernioError(
            "Zernio did not return a connection URL.",
            stage="create_connection_url",
            error_category="malformed_response",
        )
    return str(auth_url)


def list_connected_accounts(
    *,
    profile_id: str,
    platform: Optional[str] = None,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    request_func: Callable[..., Any] = requests.request,
    timeout: int = 30,
) -> List[Dict[str, Any]]:
    params = {"profileId": profile_id, "status": "connected"}
    if platform:
        params["platform"] = _normalise_platform(platform)

    response = _request(
        "GET",
        "/accounts",
        api_key=api_key,
        base_url=base_url,
        request_func=request_func,
        timeout=timeout,
        params=params,
        stage="list_accounts",
    )
    accounts = response.get("accounts", [])
    if not isinstance(accounts, list):
        raise ZernioError(
            "Zernio account response was malformed.",
            stage="list_accounts",
            error_category="malformed_response",
        )
    return [account for account in accounts if isinstance(account, dict)]


def sync_connected_account_ids(
    connected_account: Any,
    *,
    platform: Optional[str],
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    request_func: Callable[..., Any] = requests.request,
    timeout: int = 30,
) -> List[Dict[str, Any]]:
    if not connected_account.zernio_profile_id:
        raise ZernioError("Create a Zernio connection profile first.")

    accounts = list_connected_accounts(
        profile_id=connected_account.zernio_profile_id,
        platform=platform,
        api_key=api_key,
        base_url=base_url,
        request_func=request_func,
        timeout=timeout,
    )

    for account in accounts:
        account_platform = _normalise_platform(account.get("platform"))
        account_id = str(account.get("_id") or account.get("accountId") or "")
        if not account_id:
            continue
        if account_platform == "instagram":
            connected_account.zernio_instagram_account_id = account_id
        elif account_platform == "facebook":
            connected_account.zernio_facebook_account_id = account_id

    return accounts


def publish_single_image(
    post: Any,
    connected_account: Any,
    *,
    platforms: Optional[Iterable[str]] = None,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    request_func: Callable[..., Any] = requests.request,
    timeout: int = 30,
) -> ZernioPublishResult:
    selected_platforms = _selected_platforms(post, platforms)
    zernio_platforms = _platform_targets(connected_account, selected_platforms)
    _validate_single_image_post(post, zernio_platforms)

    response = _request(
        "POST",
        "/posts",
        api_key=api_key,
        base_url=base_url,
        request_func=request_func,
        timeout=timeout,
        headers={"x-request-id": str(uuid4())},
        json={
            "title": f"SMU post {post.id}",
            "content": post.caption or "",
            "mediaItems": [
                {
                    "type": "image",
                    "url": post.file_url,
                    "title": f"SMU post {post.id} image",
                }
            ],
            "platforms": zernio_platforms,
            "publishNow": True,
            "metadata": {
                "smuPostId": str(post.id),
                "smuUserId": str(post.user_id),
            },
        },
        stage="publish_post",
    )
    return parse_publish_result(response)


def get_post_status(
    provider_post_id: str,
    *,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    request_func: Callable[..., Any] = requests.request,
    timeout: int = 30,
) -> ZernioPublishResult:
    response = _request(
        "GET",
        f"/posts/{provider_post_id}",
        api_key=api_key,
        base_url=base_url,
        request_func=request_func,
        timeout=timeout,
        stage="get_post_status",
    )
    return parse_publish_result(response)


def parse_publish_result(body: Dict[str, Any]) -> ZernioPublishResult:
    post = body.get("post") if isinstance(body, dict) else None
    if not isinstance(post, dict):
        raise ZernioError(
            "Zernio post response was malformed.",
            stage="parse_post",
            error_category="malformed_response",
        )

    platform_entries = [
        entry for entry in post.get("platforms", []) if isinstance(entry, dict)
    ]
    platforms = [
        str(entry.get("platform"))
        for entry in platform_entries
        if entry.get("platform")
    ]
    published_url = _first_platform_url(platform_entries)
    error = post.get("error") or body.get("error")

    return ZernioPublishResult(
        provider_post_id=str(post.get("_id") or "") or None,
        status=str(post.get("status") or "publishing"),
        platforms=platforms,
        published_url=published_url,
        error=str(error) if error else None,
    )


def _request(
    method: str,
    path: str,
    *,
    api_key: str,
    base_url: str,
    request_func: Callable[..., Any],
    timeout: int,
    stage: str,
    **kwargs: Any,
) -> Dict[str, Any]:
    if not api_key:
        raise ZernioError("Zernio is not configured yet.", stage=stage)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    headers.update(kwargs.pop("headers", {}) or {})
    if "json" in kwargs:
        headers.setdefault("Content-Type", "application/json")

    try:
        response = request_func(
            method,
            f"{base_url.rstrip('/')}/{path.lstrip('/')}",
            headers=headers,
            timeout=timeout,
            **kwargs,
        )
    except requests.Timeout as exc:
        raise ZernioError(
            "Zernio timed out. Please try again.",
            stage=stage,
            error_category="timeout",
        ) from exc
    except requests.RequestException as exc:
        raise ZernioError(
            "Zernio could not be reached. Please try again.",
            stage=stage,
            error_category="network",
        ) from exc

    status_code = getattr(response, "status_code", None)
    body = _safe_json(response)
    if status_code is not None and status_code >= 400:
        message = _safe_error_message(body)
        raise ZernioError(
            message,
            stage=stage,
            status_code=status_code,
            error_category=_safe_error_code(body),
        )

    raise_for_status = getattr(response, "raise_for_status", None)
    if callable(raise_for_status):
        try:
            raise_for_status()
        except requests.RequestException as exc:
            raise ZernioError(
                "Zernio request failed. Please try again.",
                stage=stage,
                status_code=status_code,
            ) from exc

    if not isinstance(body, dict):
        raise ZernioError(
            "Zernio response was malformed.",
            stage=stage,
            status_code=status_code,
            error_category="malformed_response",
        )
    return body


def _selected_platforms(post: Any, platforms: Optional[Iterable[str]]) -> List[str]:
    raw_platforms = platforms
    if raw_platforms is None:
        raw_platforms = (getattr(post, "platforms", "") or "").split(",")
    selected = []
    for platform in raw_platforms:
        normalised = _normalise_platform(platform)
        if normalised in SUPPORTED_POC_PLATFORMS and normalised not in selected:
            selected.append(normalised)
    return selected


def _platform_targets(connected_account: Any, selected_platforms: List[str]) -> List[Dict[str, str]]:
    targets = []
    account_field_by_platform = {
        "instagram": "zernio_instagram_account_id",
        "facebook": "zernio_facebook_account_id",
    }
    for platform in selected_platforms:
        account_id = getattr(
            connected_account,
            account_field_by_platform[platform],
            None,
        )
        if account_id:
            targets.append({"platform": platform, "accountId": account_id})
    return targets


def _validate_single_image_post(post: Any, targets: List[Dict[str, str]]) -> None:
    if getattr(post, "group_id", None) or getattr(post, "post_type", None) == "carousel":
        raise ZernioError("The Zernio POC supports single-image posts only.")

    if (getattr(post, "file_type", "") or "").lower() != "image":
        raise ZernioError("The Zernio POC supports image posts only.")

    file_url = (getattr(post, "file_url", "") or "").strip()
    parsed_url = urlparse(file_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise ZernioError("Zernio publishing requires a public HTTPS image URL.")

    if not targets:
        raise ZernioError("Connect Instagram or Facebook with Zernio before publishing.")


def _normalise_platform(platform: Any) -> str:
    return str(platform or "").strip().lower()


def _safe_json(response: Any) -> Optional[Any]:
    json_method = getattr(response, "json", None)
    if not callable(json_method):
        return None
    try:
        return json_method()
    except ValueError:
        return None


def _safe_error_message(body: Optional[Any]) -> str:
    if isinstance(body, dict):
        message = body.get("error") or body.get("message")
        if message:
            return str(message)
    return "Zernio rejected the request. Please try again."


def _safe_error_code(body: Optional[Any]) -> Optional[str]:
    if isinstance(body, dict):
        code = body.get("code") or body.get("type")
        if code:
            return str(code)
    return None


def _first_platform_url(platform_entries: List[Dict[str, Any]]) -> Optional[str]:
    for entry in platform_entries:
        url = entry.get("platformPostUrl") or entry.get("url")
        if url:
            return str(url)
    return None
