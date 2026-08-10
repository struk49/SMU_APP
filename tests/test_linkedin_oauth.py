from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from flask import url_for

from conftest import create_accounts, create_user, login
from smu_core.services.platforms import linkedin_oauth
from smu_core.services.time_utils import utc_now


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP failure")


def test_linkedin_routes_registered_with_compatible_endpoint_names(module):
    routes = {
        rule.endpoint: rule.rule
        for rule in module.app.url_map.iter_rules()
        if "linkedin" in rule.endpoint
    }

    assert routes["linkedin_connect"] == "/accounts/linkedin/connect"
    assert routes["linkedin_callback"] == "/accounts/linkedin/callback"
    assert routes["linkedin_disconnect"] == "/accounts/linkedin/disconnect"

    with module.app.test_request_context():
        assert url_for("linkedin_connect") == "/accounts/linkedin/connect"
        assert url_for("linkedin_callback") == "/accounts/linkedin/callback"
        assert url_for("linkedin_disconnect") == "/accounts/linkedin/disconnect"


def test_logged_out_connect_redirects_to_login(client):
    response = client.get("/accounts/linkedin/connect")

    assert response.status_code == 302
    assert "/login" in response.location


def test_configured_connect_redirects_to_linkedin_and_stores_state(client, app, module):
    app.config.update(
        LINKEDIN_CLIENT_ID="client-id",
        LINKEDIN_CLIENT_SECRET="client-secret",
        LINKEDIN_REDIRECT_URI="https://smu.test/accounts/linkedin/callback",
    )
    user = create_user(module)
    login(client, user)

    response = client.get("/accounts/linkedin/connect")

    assert response.status_code == 302
    parsed = urlparse(response.location)
    query = parse_qs(parsed.query)
    assert response.location.startswith(linkedin_oauth.LINKEDIN_AUTHORIZATION_ENDPOINT)
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == ["https://smu.test/accounts/linkedin/callback"]
    assert "openid profile w_member_social" in query["scope"]
    assert "client-secret" not in response.location

    with client.session_transaction() as session:
        assert session["linkedin_oauth_state"]
        assert session["linkedin_oauth_state"] == query["state"][0]


def test_missing_linkedin_config_shows_safe_message(client, app, module):
    app.config.update(
        LINKEDIN_CLIENT_ID="",
        LINKEDIN_CLIENT_SECRET="",
        LINKEDIN_REDIRECT_URI="",
    )
    user = create_user(module)
    login(client, user)

    response = client.get("/accounts/linkedin/connect", follow_redirects=True)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "LinkedIn connection is not configured." in html


def test_callback_rejects_missing_or_wrong_state_before_token_exchange(
    client,
    module,
    monkeypatch,
):
    user = create_user(module)
    login(client, user)
    calls = []

    def fake_exchange(**kwargs):
        calls.append(kwargs)
        raise AssertionError("Token exchange should not be called")

    monkeypatch.setattr(linkedin_oauth, "exchange_code_for_token", fake_exchange)

    missing = client.get(
        "/accounts/linkedin/callback?code=abc&state=wrong",
        follow_redirects=True,
    )
    with client.session_transaction() as session:
        session["linkedin_oauth_state"] = "expected"
    wrong = client.get(
        "/accounts/linkedin/callback?code=abc&state=wrong",
        follow_redirects=True,
    )

    assert "LinkedIn connection could not be verified." in missing.get_data(as_text=True)
    assert "LinkedIn connection could not be verified." in wrong.get_data(as_text=True)
    assert calls == []


def test_callback_handles_denied_authorization(client, module):
    user = create_user(module)
    login(client, user)
    with client.session_transaction() as session:
        session["linkedin_oauth_state"] = "expected"

    response = client.get(
        "/accounts/linkedin/callback?error=access_denied&state=expected",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "LinkedIn connection was cancelled or denied." in response.get_data(as_text=True)


def test_callback_requires_authorization_code(client, module):
    user = create_user(module)
    login(client, user)
    with client.session_transaction() as session:
        session["linkedin_oauth_state"] = "expected"

    response = client.get(
        "/accounts/linkedin/callback?state=expected",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "LinkedIn did not return an authorization code." in response.get_data(as_text=True)


def test_valid_callback_stores_token_identity_and_leaves_other_user_untouched(
    client,
    app,
    module,
    monkeypatch,
):
    app.config.update(
        LINKEDIN_CLIENT_ID="client-id",
        LINKEDIN_CLIENT_SECRET="client-secret",
        LINKEDIN_REDIRECT_URI="https://smu.test/accounts/linkedin/callback",
    )
    user = create_user(module)
    other = create_user(module, email="other@example.com")
    other_accounts = create_accounts(module, other, instagram=True, facebook=True)
    login(client, user)
    with client.session_transaction() as session:
        session["linkedin_oauth_state"] = "expected"

    expires_at = utc_now() + timedelta(hours=1)
    refresh_expires_at = utc_now() + timedelta(days=30)

    def fake_exchange(**kwargs):
        assert kwargs["code"] == "auth-code"
        assert kwargs["client_secret"] == "client-secret"
        return linkedin_oauth.LinkedInTokenData(
            access_token="access-token",
            access_token_expires_at=expires_at,
            scopes="openid profile w_member_social",
            refresh_token="refresh-token",
            refresh_token_expires_at=refresh_expires_at,
        )

    def fake_identity(access_token):
        assert access_token == "access-token"
        return linkedin_oauth.LinkedInMemberIdentity(
            member_id="member123",
            member_urn="urn:li:person:member123",
            display_name="LinkedIn User",
        )

    monkeypatch.setattr(linkedin_oauth, "exchange_code_for_token", fake_exchange)
    monkeypatch.setattr(linkedin_oauth, "fetch_member_identity", fake_identity)

    response = client.get(
        "/accounts/linkedin/callback?code=auth-code&state=expected",
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)
    account = module.ConnectedAccount.query.filter_by(user_id=user.id).first()
    saved_other = module.db.session.get(module.ConnectedAccount, other_accounts.id)

    assert response.status_code == 200
    assert "LinkedIn connected successfully." in html
    assert "access-token" not in html
    assert account.linkedin_connected is True
    assert account.linkedin_access_token == "access-token"
    assert account.linkedin_access_token_expires_at == expires_at
    assert account.linkedin_scopes == "openid profile w_member_social"
    assert account.linkedin_member_id == "member123"
    assert account.linkedin_member_urn == "urn:li:person:member123"
    assert account.linkedin_display_name == "LinkedIn User"
    assert account.linkedin_refresh_token == "refresh-token"
    assert account.linkedin_refresh_token_expires_at == refresh_expires_at
    assert saved_other.instagram_connected is True
    assert saved_other.facebook_connected is True
    assert saved_other.linkedin_connected is False


def test_valid_callback_allows_absent_refresh_token(client, app, module, monkeypatch):
    app.config.update(
        LINKEDIN_CLIENT_ID="client-id",
        LINKEDIN_CLIENT_SECRET="client-secret",
        LINKEDIN_REDIRECT_URI="https://smu.test/accounts/linkedin/callback",
    )
    user = create_user(module)
    login(client, user)
    with client.session_transaction() as session:
        session["linkedin_oauth_state"] = "expected"

    monkeypatch.setattr(
        linkedin_oauth,
        "exchange_code_for_token",
        lambda **kwargs: linkedin_oauth.LinkedInTokenData(
            access_token="access-token",
            access_token_expires_at=None,
            scopes="openid profile w_member_social",
        ),
    )
    monkeypatch.setattr(
        linkedin_oauth,
        "fetch_member_identity",
        lambda access_token: linkedin_oauth.LinkedInMemberIdentity(
            member_id="member123",
            member_urn="urn:li:person:member123",
        ),
    )

    response = client.get(
        "/accounts/linkedin/callback?code=auth-code&state=expected",
    )
    account = module.ConnectedAccount.query.filter_by(user_id=user.id).first()

    assert response.status_code == 302
    assert account.linkedin_connected is True
    assert account.linkedin_refresh_token is None
    assert account.linkedin_refresh_token_expires_at is None


def test_callback_rolls_back_and_does_not_mark_connected_on_error(
    client,
    app,
    module,
    monkeypatch,
):
    app.config.update(
        LINKEDIN_CLIENT_ID="client-id",
        LINKEDIN_CLIENT_SECRET="client-secret",
        LINKEDIN_REDIRECT_URI="https://smu.test/accounts/linkedin/callback",
    )
    user = create_user(module)
    login(client, user)
    with client.session_transaction() as session:
        session["linkedin_oauth_state"] = "expected"

    monkeypatch.setattr(
        linkedin_oauth,
        "exchange_code_for_token",
        lambda **kwargs: (_ for _ in ()).throw(
            linkedin_oauth.LinkedInOAuthError("exchange", "Nope")
        ),
    )

    response = client.get(
        "/accounts/linkedin/callback?code=auth-code&state=expected",
        follow_redirects=True,
    )
    account = module.ConnectedAccount.query.filter_by(user_id=user.id).first()

    assert "LinkedIn could not be connected." in response.get_data(as_text=True)
    assert account is not None
    assert account.linkedin_connected is False
    assert account.linkedin_access_token is None
    assert account.linkedin_access_token_expires_at is None
    assert account.linkedin_refresh_token is None
    assert account.linkedin_refresh_token_expires_at is None
    assert account.linkedin_scopes is None
    assert account.linkedin_member_id is None
    assert account.linkedin_member_urn is None
    assert account.linkedin_display_name is None


def test_disconnect_requires_login(client):
    response = client.post("/accounts/linkedin/disconnect")

    assert response.status_code == 302
    assert "/login" in response.location


def test_disconnect_clears_only_current_user_linkedin_sensitive_fields(
    client,
    module,
):
    user = create_user(module)
    other = create_user(module, email="other@example.com")
    account = create_accounts(
        module,
        user,
        single_webhook="https://make.test/single",
        carousel_webhook="https://make.test/carousel",
        instagram=True,
        facebook=True,
    )
    account.linkedin_connected = True
    account.linkedin_access_token = "access-token"
    account.linkedin_access_token_expires_at = utc_now() + timedelta(hours=1)
    account.linkedin_scopes = "openid profile w_member_social"
    account.linkedin_member_id = "member123"
    account.linkedin_member_urn = "urn:li:person:member123"
    account.linkedin_display_name = "LinkedIn User"
    account.linkedin_refresh_token = "refresh-token"
    account.linkedin_refresh_token_expires_at = utc_now() + timedelta(days=30)

    other_account = create_accounts(module, other, instagram=False, facebook=False)
    other_account.linkedin_connected = True
    other_account.linkedin_access_token = "other-token"
    module.db.session.commit()

    login(client, user)

    response = client.post("/accounts/linkedin/disconnect", follow_redirects=True)
    saved = module.db.session.get(module.ConnectedAccount, account.id)
    saved_other = module.db.session.get(module.ConnectedAccount, other_account.id)

    assert response.status_code == 200
    assert "LinkedIn disconnected." in response.get_data(as_text=True)
    assert saved.linkedin_connected is False
    assert saved.linkedin_access_token is None
    assert saved.linkedin_access_token_expires_at is None
    assert saved.linkedin_scopes is None
    assert saved.linkedin_member_id is None
    assert saved.linkedin_member_urn is None
    assert saved.linkedin_display_name is None
    assert saved.linkedin_refresh_token is None
    assert saved.linkedin_refresh_token_expires_at is None
    assert saved.instagram_connected is True
    assert saved.facebook_connected is True
    assert saved.make_webhook_single == "https://make.test/single"
    assert saved.make_webhook_carousel == "https://make.test/carousel"
    assert saved_other.linkedin_connected is True
    assert saved_other.linkedin_access_token == "other-token"


def test_connected_account_model_has_nullable_linkedin_storage_columns(module):
    columns = module.ConnectedAccount.__table__.columns

    assert columns.linkedin_access_token.nullable is True
    assert columns.linkedin_access_token_expires_at.nullable is True
    assert columns.linkedin_scopes.nullable is True
    assert columns.linkedin_member_id.nullable is True
    assert columns.linkedin_member_urn.nullable is True
    assert columns.linkedin_display_name.nullable is True
    assert columns.linkedin_refresh_token.nullable is True
    assert columns.linkedin_refresh_token_expires_at.nullable is True


def test_authorization_url_helper_uses_exact_parameters():
    url = linkedin_oauth.build_authorization_url(
        client_id="client-id",
        redirect_uri="https://smu.test/callback",
        state="state-value",
    )
    query = parse_qs(urlparse(url).query)

    assert url.startswith(linkedin_oauth.LINKEDIN_AUTHORIZATION_ENDPOINT)
    assert query == {
        "response_type": ["code"],
        "client_id": ["client-id"],
        "redirect_uri": ["https://smu.test/callback"],
        "state": ["state-value"],
        "scope": ["openid profile w_member_social"],
    }


def test_exchange_code_for_token_uses_exact_token_endpoint_and_body():
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(
            200,
            {
                "access_token": "access-token",
                "expires_in": 3600,
                "scope": "openid profile w_member_social",
            },
        )

    token_data = linkedin_oauth.exchange_code_for_token(
        code="auth-code",
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="https://smu.test/callback",
        post_request=fake_post,
        timeout=9,
    )

    assert token_data.access_token == "access-token"
    assert token_data.refresh_token is None
    assert calls == [
        (
            linkedin_oauth.LINKEDIN_TOKEN_ENDPOINT,
            {
                "data": {
                    "grant_type": "authorization_code",
                    "code": "auth-code",
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    "redirect_uri": "https://smu.test/callback",
                },
                "headers": {"Content-Type": "application/x-www-form-urlencoded"},
                "timeout": 9,
            },
        )
    ]


def test_token_errors_and_malformed_responses_are_safe():
    with pytest.raises(linkedin_oauth.LinkedInOAuthError) as failed:
        linkedin_oauth.exchange_code_for_token(
            code="auth-code",
            client_id="client-id",
            client_secret="client-secret",
            redirect_uri="https://smu.test/callback",
            post_request=lambda *args, **kwargs: FakeResponse(
                400,
                {
                    "error": "invalid_grant",
                    "error_description": "Bad code",
                },
            ),
        )

    assert failed.value.status_code == 400
    assert failed.value.error_category == "invalid_grant"
    assert "client-secret" not in str(failed.value)

    with pytest.raises(linkedin_oauth.LinkedInOAuthError):
        linkedin_oauth.parse_token_response({})


def test_member_identity_fetch_and_parse_are_offline_and_exact():
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(
            200,
            {
                "sub": "member123",
                "name": "LinkedIn User",
            },
        )

    identity = linkedin_oauth.fetch_member_identity(
        "access-token",
        get_request=fake_get,
        timeout=8,
    )

    assert identity == linkedin_oauth.LinkedInMemberIdentity(
        member_id="member123",
        member_urn="urn:li:person:member123",
        display_name="LinkedIn User",
    )
    assert calls == [
        (
            linkedin_oauth.LINKEDIN_USERINFO_ENDPOINT,
            {
                "headers": {"Authorization": "Bearer access-token"},
                "timeout": 8,
            },
        )
    ]


def test_member_identity_requires_subject():
    with pytest.raises(linkedin_oauth.LinkedInOAuthError):
        linkedin_oauth.parse_member_identity({"name": "No Subject"})
