"""LinkedIn OAuth helpers for account connection.

The helpers are deliberately small and injectable so route tests can remain
offline and no LinkedIn request is made unless a route explicitly calls them.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlencode

import requests

from smu_core.services.time_utils import utc_now


LINKEDIN_AUTHORIZATION_ENDPOINT = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_ENDPOINT = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_USERINFO_ENDPOINT = "https://api.linkedin.com/v2/userinfo"
LINKEDIN_OAUTH_SCOPES = ("openid", "profile", "w_member_social")


class LinkedInOAuthError(RuntimeError):
    """Raised when LinkedIn OAuth or identity retrieval fails."""

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
        parts = [f"LinkedIn OAuth error during {self.stage}: {super().__str__()}"]
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        if self.error_category:
            parts.append(f"category={self.error_category}")
        return " ".join(parts)


@dataclass(frozen=True)
class LinkedInTokenData:
    access_token: str
    access_token_expires_at: Optional[Any]
    scopes: str
    refresh_token: Optional[str] = None
    refresh_token_expires_at: Optional[Any] = None


@dataclass(frozen=True)
class LinkedInMemberIdentity:
    member_id: str
    member_urn: str
    display_name: Optional[str] = None


def build_authorization_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    scopes: Any = LINKEDIN_OAUTH_SCOPES,
) -> str:
    if not client_id:
        raise ValueError("LinkedIn client ID is required.")
    if not redirect_uri:
        raise ValueError("LinkedIn redirect URI is required.")
    if not state:
        raise ValueError("LinkedIn OAuth state is required.")

    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": " ".join(scopes),
        }
    )
    return f"{LINKEDIN_AUTHORIZATION_ENDPOINT}?{query}"


def exchange_code_for_token(
    *,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    post_request: Callable[..., Any] = requests.post,
    timeout: int = 30,
) -> LinkedInTokenData:
    if not code:
        raise ValueError("LinkedIn authorization code is required.")
    if not client_id:
        raise ValueError("LinkedIn client ID is required.")
    if not client_secret:
        raise ValueError("LinkedIn client secret is required.")
    if not redirect_uri:
        raise ValueError("LinkedIn redirect URI is required.")

    response = post_request(
        LINKEDIN_TOKEN_ENDPOINT,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=timeout,
    )
    _raise_for_http_failure(response, "exchange_code_for_token")
    return parse_token_response(_safe_json(response))


def parse_token_response(body: Any) -> LinkedInTokenData:
    if not isinstance(body, dict):
        raise LinkedInOAuthError(
            "parse_token_response",
            "LinkedIn token response was malformed.",
            error_category="malformed_response",
        )

    access_token = body.get("access_token")
    if not access_token:
        raise LinkedInOAuthError(
            "parse_token_response",
            "LinkedIn token response did not include an access token.",
            error_category="malformed_response",
        )

    scopes = body.get("scope") or body.get("scopes") or " ".join(LINKEDIN_OAUTH_SCOPES)
    access_expires_at = _expiry_from_seconds(body.get("expires_in"))
    refresh_expires_at = _expiry_from_seconds(body.get("refresh_token_expires_in"))

    return LinkedInTokenData(
        access_token=access_token,
        access_token_expires_at=access_expires_at,
        scopes=str(scopes),
        refresh_token=body.get("refresh_token"),
        refresh_token_expires_at=refresh_expires_at,
    )


def fetch_member_identity(
    access_token: str,
    *,
    get_request: Callable[..., Any] = requests.get,
    timeout: int = 30,
) -> LinkedInMemberIdentity:
    if not access_token:
        raise ValueError("LinkedIn access token is required.")

    response = get_request(
        LINKEDIN_USERINFO_ENDPOINT,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=timeout,
    )
    _raise_for_http_failure(response, "fetch_member_identity")
    return parse_member_identity(_safe_json(response))


def parse_member_identity(body: Any) -> LinkedInMemberIdentity:
    if not isinstance(body, dict) or not body.get("sub"):
        raise LinkedInOAuthError(
            "parse_member_identity",
            "LinkedIn identity response did not include a subject.",
            error_category="malformed_response",
        )

    member_id = str(body["sub"])
    return LinkedInMemberIdentity(
        member_id=member_id,
        member_urn=f"urn:li:person:{member_id}",
        display_name=body.get("name"),
    )


def _expiry_from_seconds(value: Any) -> Optional[Any]:
    if value in (None, ""):
        return None
    try:
        seconds = int(value)
    except (TypeError, ValueError) as exc:
        raise LinkedInOAuthError(
            "parse_token_response",
            "LinkedIn token expiry was malformed.",
            error_category="malformed_response",
        ) from exc
    return utc_now() + timedelta(seconds=seconds)


def _raise_for_http_failure(response: Any, stage: str) -> None:
    status_code = getattr(response, "status_code", None)
    if status_code is not None and status_code >= 400:
        body = _safe_json(response)
        message = "LinkedIn OAuth request failed."
        category = None
        if isinstance(body, dict):
            message = str(body.get("error_description") or body.get("message") or body.get("error") or message)
            category = body.get("error") or body.get("code")
            if category is not None:
                category = str(category)
        raise LinkedInOAuthError(
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
            raise LinkedInOAuthError(
                stage,
                "LinkedIn OAuth request failed.",
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
