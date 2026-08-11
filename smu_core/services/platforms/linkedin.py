"""LinkedIn Posts API adapter helpers.

This module is intentionally not wired into SMU publishing yet. It provides
small, testable helpers for the first LinkedIn platform-adapter slice.
"""

from dataclasses import dataclass
import re
from typing import Any, Callable, Dict, Optional

import requests


LINKEDIN_API_BASE_URL = "https://api.linkedin.com"
LINKEDIN_API_VERSION = "202606"
LINKEDIN_RESTLI_PROTOCOL_VERSION = "2.0.0"
LINKEDIN_POSTS_ENDPOINT = f"{LINKEDIN_API_BASE_URL}/rest/posts"
LINKEDIN_IMAGES_INITIALIZE_UPLOAD_ENDPOINT = (
    f"{LINKEDIN_API_BASE_URL}/rest/images?action=initializeUpload"
)

_AUTHOR_URN_RE = re.compile(r"^urn:li:(person|organization):[A-Za-z0-9_-]+$")
_IMAGE_URN_RE = re.compile(r"^urn:li:image:[A-Za-z0-9_-]+$")


class LinkedInAPIError(RuntimeError):
    """Raised when a LinkedIn API adapter request fails."""

    def __init__(
        self,
        stage: str,
        message: str,
        *,
        status_code: Optional[int] = None,
        error_category: Optional[str] = None,
    ) -> None:
        self.stage = stage
        self.status_code = status_code
        self.error_category = error_category
        super().__init__(message)

    def __str__(self) -> str:
        parts = [f"LinkedIn API error during {self.stage}: {super().__str__()}"]
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        if self.error_category:
            parts.append(f"category={self.error_category}")
        return " ".join(parts)


@dataclass(frozen=True)
class LinkedInPostResult:
    """Stable subset of a LinkedIn post creation response."""

    post_urn: Optional[str]
    status_code: int
    response_body: Optional[Any] = None


@dataclass(frozen=True)
class LinkedInImageUpload:
    """LinkedIn image upload initialization result."""

    upload_url: str
    image_urn: str


@dataclass(frozen=True)
class LinkedInImageBinaryUpload:
    """Stable subset of a LinkedIn binary image upload response."""

    status_code: int


def build_headers(
    access_token: str,
    api_version: str = LINKEDIN_API_VERSION,
) -> Dict[str, str]:
    """Build required LinkedIn REST API headers."""

    if not access_token:
        raise ValueError("LinkedIn access token is required.")
    if not api_version:
        raise ValueError("LinkedIn API version is required.")

    return {
        "Authorization": f"Bearer {access_token}",
        "Linkedin-Version": api_version,
        "X-Restli-Protocol-Version": LINKEDIN_RESTLI_PROTOCOL_VERSION,
        "Content-Type": "application/json",
    }


def validate_author_urn(author_urn: str) -> str:
    """Validate and return a supported LinkedIn author URN."""

    if not isinstance(author_urn, str) or not _AUTHOR_URN_RE.fullmatch(author_urn):
        raise ValueError("Unsupported LinkedIn author URN.")
    return author_urn


def _validate_image_urn(image_urn: str) -> str:
    if not isinstance(image_urn, str) or not _IMAGE_URN_RE.fullmatch(image_urn):
        raise ValueError("Unsupported LinkedIn image URN.")
    return image_urn


def _base_post_payload(
    author_urn: str,
    commentary: str,
    *,
    visibility: str = "PUBLIC",
) -> Dict[str, Any]:
    if commentary is None:
        raise ValueError("LinkedIn commentary is required.")

    return {
        "author": validate_author_urn(author_urn),
        "commentary": commentary,
        "visibility": visibility,
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }


def build_text_post_payload(
    author_urn: str,
    commentary: str,
    *,
    visibility: str = "PUBLIC",
) -> Dict[str, Any]:
    """Build a text-only LinkedIn Posts API payload."""

    return _base_post_payload(author_urn, commentary, visibility=visibility)


def build_initialize_image_upload_payload(owner_urn: str) -> Dict[str, Any]:
    """Build the LinkedIn Images API initializeUpload payload."""

    return {"initializeUploadRequest": {"owner": validate_author_urn(owner_urn)}}


def build_single_image_post_payload(
    author_urn: str,
    commentary: str,
    image_urn: str,
    *,
    visibility: str = "PUBLIC",
) -> Dict[str, Any]:
    """Build a single-image LinkedIn Posts API payload."""

    payload = _base_post_payload(author_urn, commentary, visibility=visibility)
    payload["content"] = {"media": {"id": _validate_image_urn(image_urn)}}
    return payload


def create_text_post(
    access_token: str,
    author_urn: str,
    commentary: str,
    *,
    visibility: str = "PUBLIC",
    api_version: str = LINKEDIN_API_VERSION,
    post_request: Callable[..., Any] = requests.post,
    timeout: int = 30,
) -> LinkedInPostResult:
    """Create a LinkedIn text post through an injectable HTTP POST callable."""

    response = post_request(
        LINKEDIN_POSTS_ENDPOINT,
        headers=build_headers(access_token, api_version=api_version),
        json=build_text_post_payload(author_urn, commentary, visibility=visibility),
        timeout=timeout,
    )
    _raise_for_http_failure(response, "create_text_post")

    return LinkedInPostResult(
        post_urn=_response_header(response, "x-restli-id"),
        status_code=getattr(response, "status_code", 0),
        response_body=_safe_json(response),
    )


def upload_image_binary(
    access_token: str,
    upload_url: str,
    image_bytes: bytes,
    content_type: str,
    *,
    put_request: Callable[..., Any] = requests.put,
    timeout: int = 30,
) -> LinkedInImageBinaryUpload:
    """Upload image bytes to a LinkedIn Images API upload URL."""

    if not access_token:
        raise ValueError("LinkedIn access token is required.")
    if not upload_url:
        raise ValueError("LinkedIn image upload URL is required.")
    if not image_bytes:
        raise ValueError("LinkedIn image bytes are required.")
    if not content_type:
        raise ValueError("LinkedIn image content type is required.")

    response = put_request(
        upload_url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": content_type,
        },
        data=image_bytes,
        timeout=timeout,
    )
    _raise_for_http_failure(response, "upload_image_binary")

    return LinkedInImageBinaryUpload(
        status_code=getattr(response, "status_code", 0),
    )


def create_single_image_post(
    access_token: str,
    author_urn: str,
    commentary: str,
    image_bytes: bytes,
    content_type: str,
    *,
    visibility: str = "PUBLIC",
    api_version: str = LINKEDIN_API_VERSION,
    initialize_post_request: Callable[..., Any] = requests.post,
    upload_put_request: Callable[..., Any] = requests.put,
    post_request: Callable[..., Any] = requests.post,
    timeout: int = 30,
) -> LinkedInPostResult:
    """Create a LinkedIn single-image post through injectable HTTP callables."""

    upload = initialize_image_upload(
        access_token,
        author_urn,
        api_version=api_version,
        post_request=initialize_post_request,
        timeout=timeout,
    )
    upload_image_binary(
        access_token,
        upload.upload_url,
        image_bytes,
        content_type,
        put_request=upload_put_request,
        timeout=timeout,
    )

    response = post_request(
        LINKEDIN_POSTS_ENDPOINT,
        headers=build_headers(access_token, api_version=api_version),
        json=build_single_image_post_payload(
            author_urn,
            commentary,
            upload.image_urn,
            visibility=visibility,
        ),
        timeout=timeout,
    )
    _raise_for_http_failure(response, "create_single_image_post")

    return LinkedInPostResult(
        post_urn=_response_header(response, "x-restli-id"),
        status_code=getattr(response, "status_code", 0),
        response_body=_safe_json(response),
    )


def initialize_image_upload(
    access_token: str,
    owner_urn: str,
    *,
    api_version: str = LINKEDIN_API_VERSION,
    post_request: Callable[..., Any] = requests.post,
    timeout: int = 30,
) -> LinkedInImageUpload:
    """Initialize a LinkedIn image upload and return the upload URL and image URN."""

    response = post_request(
        LINKEDIN_IMAGES_INITIALIZE_UPLOAD_ENDPOINT,
        headers=build_headers(access_token, api_version=api_version),
        json=build_initialize_image_upload_payload(owner_urn),
        timeout=timeout,
    )
    _raise_for_http_failure(response, "initialize_image_upload")

    body = _safe_json(response)
    value = body.get("value") if isinstance(body, dict) else None
    upload_url = value.get("uploadUrl") if isinstance(value, dict) else None
    image_urn = value.get("image") if isinstance(value, dict) else None

    if not upload_url or not image_urn:
        raise LinkedInAPIError(
            "initialize_image_upload",
            "LinkedIn image initialize response missing uploadUrl or image URN.",
            status_code=getattr(response, "status_code", None),
            error_category="malformed_response",
        )

    try:
        _validate_image_urn(image_urn)
    except ValueError as exc:
        raise LinkedInAPIError(
            "initialize_image_upload",
            "LinkedIn image initialize response contained an invalid image URN.",
            status_code=getattr(response, "status_code", None),
            error_category="malformed_response",
        ) from exc
    return LinkedInImageUpload(upload_url=upload_url, image_urn=image_urn)


def _raise_for_http_failure(response: Any, stage: str) -> None:
    status_code = getattr(response, "status_code", None)
    if status_code is not None and status_code >= 400:
        body = _safe_json(response)
        message = "LinkedIn API request failed."
        category = None
        if isinstance(body, dict):
            message = str(body.get("message") or body.get("error") or message)
            category = body.get("code") or body.get("errorCode") or body.get("serviceErrorCode")
            if category is not None:
                category = str(category)
        raise LinkedInAPIError(
            stage,
            message,
            status_code=status_code,
            error_category=category,
        )

    raise_for_status = getattr(response, "raise_for_status", None)
    if callable(raise_for_status):
        try:
            raise_for_status()
        except Exception as exc:
            raise LinkedInAPIError(
                stage,
                "LinkedIn API request failed.",
                status_code=status_code,
            ) from exc


def _safe_json(response: Any) -> Optional[Any]:
    json_method = getattr(response, "json", None)
    if not callable(json_method):
        return None
    try:
        return json_method()
    except ValueError:
        return None


def _response_header(response: Any, name: str) -> Optional[str]:
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    value = headers.get(name) or headers.get(name.lower()) or headers.get(name.upper())
    if value is not None:
        return value
    lowered_name = name.lower()
    for header_name, header_value in headers.items():
        if str(header_name).lower() == lowered_name:
            return header_value
    return None
