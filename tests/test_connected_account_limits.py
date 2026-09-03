from datetime import datetime, timedelta
from html import unescape

from conftest import create_accounts, create_post, create_user, login
from smu_core.services import usage, zernio
from smu_core.services.platforms import linkedin_oauth
from smu_core.services.time_utils import utc_now


def set_plan(module, user, plan):
    user_usage = module.UserUsage(
        user_id=user.id,
        plan=plan,
        ai_images_used=0,
        content_packs_used=0,
        usage_period_start=datetime(2026, 8, 1),
        usage_period_end=datetime(2026, 9, 1),
    )
    module.db.session.add(user_usage)
    module.db.session.commit()
    return user_usage


def test_real_connected_account_counting_ignores_profile_make_and_placeholders(app, module):
    user = create_user(module)
    accounts = create_accounts(
        module,
        user,
        single_webhook="https://make.test/single",
        carousel_webhook="https://make.test/carousel",
        instagram=False,
        facebook=False,
    )

    assert usage.get_connected_account_count(accounts) == 0

    accounts.zernio_profile_id = "prof_123"
    module.db.session.commit()
    assert usage.get_connected_account_count(accounts) == 0

    accounts.zernio_instagram_account_id = "acct_ig"
    module.db.session.commit()
    assert usage.get_connected_account_count(accounts) == 1

    accounts.zernio_facebook_account_id = "acct_fb"
    module.db.session.commit()
    assert usage.get_connected_account_count(accounts) == 2

    accounts.zernio_facebook_account_id = "acct_ig"
    module.db.session.commit()
    assert usage.get_connected_account_count(accounts) == 1

    accounts.linkedin_connected = True
    accounts.linkedin_access_token = "token"
    accounts.linkedin_member_urn = "urn:li:person:123"
    accounts.linkedin_access_token_expires_at = utc_now() + timedelta(hours=1)
    module.db.session.commit()
    assert usage.get_connected_account_count(accounts) == 2

    accounts.linkedin_access_token_expires_at = utc_now() - timedelta(minutes=1)
    module.db.session.commit()
    assert usage.get_connected_account_count(accounts) == 1


def test_starter_limit_blocks_new_zernio_connection_without_api_call(
    client,
    app,
    module,
    monkeypatch,
):
    user = create_user(module)
    set_plan(module, user, "starter")
    accounts = create_accounts(module, user, instagram=False, facebook=False)
    accounts.zernio_profile_id = "prof_123"
    accounts.zernio_instagram_account_id = "acct_ig"
    module.db.session.commit()
    login(client, user)
    calls = []
    monkeypatch.setattr(
        zernio,
        "ensure_profile_for_user",
        lambda *args, **kwargs: calls.append("profile"),
    )
    monkeypatch.setattr(
        zernio,
        "create_connection_url",
        lambda *args, **kwargs: calls.append("connect"),
    )

    response = client.get(
        "/accounts/zernio/connect/facebook",
        follow_redirects=True,
    )
    html = unescape(response.get_data(as_text=True))
    saved = module.db.session.get(module.ConnectedAccount, accounts.id)

    assert response.status_code == 200
    assert "You've reached your 1 connected social account limit." in html
    assert "Upgrade your plan to connect another account." in html
    assert calls == []
    assert usage.get_connected_account_count(saved) == 1
    assert saved.zernio_instagram_account_id == "acct_ig"
    assert saved.zernio_facebook_account_id is None


def test_starter_limit_blocks_new_linkedin_connection_without_oauth_call(
    client,
    app,
    module,
    monkeypatch,
):
    app.config.update(
        LINKEDIN_CLIENT_ID="client-id",
        LINKEDIN_CLIENT_SECRET="client-secret",
    )
    user = create_user(module)
    set_plan(module, user, "starter")
    accounts = create_accounts(module, user, instagram=False, facebook=False)
    accounts.zernio_instagram_account_id = "acct_ig"
    module.db.session.commit()
    login(client, user)
    calls = []
    monkeypatch.setattr(
        linkedin_oauth,
        "build_authorization_url",
        lambda **kwargs: calls.append(kwargs),
    )

    response = client.get("/accounts/linkedin/connect", follow_redirects=True)

    assert response.status_code == 200
    assert "You've reached your 1 connected social account limit." in unescape(
        response.get_data(as_text=True)
    )
    assert calls == []


def test_linkedin_reconnect_uses_existing_slot(client, app, module, monkeypatch):
    app.config.update(
        LINKEDIN_CLIENT_ID="client-id",
        LINKEDIN_CLIENT_SECRET="client-secret",
        LINKEDIN_REDIRECT_URI="https://smu.test/accounts/linkedin/callback",
    )
    user = create_user(module)
    set_plan(module, user, "starter")
    accounts = create_accounts(module, user, instagram=False, facebook=False)
    accounts.linkedin_connected = True
    accounts.linkedin_access_token = "token"
    accounts.linkedin_member_urn = "urn:li:person:123"
    accounts.linkedin_access_token_expires_at = utc_now() + timedelta(hours=1)
    module.db.session.commit()
    login(client, user)
    calls = []

    def fake_authorization_url(**kwargs):
        calls.append(kwargs)
        return "https://linkedin.test/oauth"

    monkeypatch.setattr(linkedin_oauth, "build_authorization_url", fake_authorization_url)

    response = client.get("/accounts/linkedin/connect")

    assert response.status_code == 302
    assert response.location == "https://linkedin.test/oauth"
    assert len(calls) == 1
    assert usage.get_connected_account_count(accounts) == 1


def test_linkedin_callback_does_not_displace_existing_account_at_limit(
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
    set_plan(module, user, "starter")
    accounts = create_accounts(module, user, instagram=False, facebook=False)
    accounts.zernio_instagram_account_id = "acct_ig"
    module.db.session.commit()
    login(client, user)
    with client.session_transaction() as session:
        session["linkedin_oauth_state"] = "expected"

    monkeypatch.setattr(
        linkedin_oauth,
        "exchange_code_for_token",
        lambda **kwargs: linkedin_oauth.LinkedInTokenData(
            access_token="new-token",
            access_token_expires_at=utc_now() + timedelta(hours=1),
            scopes="openid profile w_member_social",
            refresh_token=None,
            refresh_token_expires_at=None,
        ),
    )
    monkeypatch.setattr(
        linkedin_oauth,
        "fetch_member_identity",
        lambda access_token: linkedin_oauth.LinkedInMemberIdentity(
            member_id="member123",
            member_urn="urn:li:person:member123",
            display_name="LinkedIn User",
        ),
    )

    response = client.get(
        "/accounts/linkedin/callback?code=auth-code&state=expected",
        follow_redirects=True,
    )
    saved = module.db.session.get(module.ConnectedAccount, accounts.id)

    assert response.status_code == 200
    assert "You've reached your 1 connected social account limit." in unescape(
        response.get_data(as_text=True)
    )
    assert saved.zernio_instagram_account_id == "acct_ig"
    assert saved.linkedin_connected is False
    assert saved.linkedin_access_token is None
    assert usage.get_connected_account_count(saved) == 1


def test_pro_and_business_connection_gating_boundaries(
    client,
    app,
    module,
    monkeypatch,
):
    app.config["ZERNIO_API_KEY"] = "sk_test"
    calls = []

    def fake_ensure_profile(current_user, accounts, **kwargs):
        calls.append(("profile", current_user.id))
        accounts.zernio_profile_id = accounts.zernio_profile_id or f"prof_{current_user.id}"
        return accounts.zernio_profile_id

    def fake_connection_url(**kwargs):
        calls.append(("connect", kwargs["platform"]))
        return "https://connect.zernio.test/start"

    monkeypatch.setattr(zernio, "ensure_profile_for_user", fake_ensure_profile)
    monkeypatch.setattr(zernio, "create_connection_url", fake_connection_url)

    pro = create_user(module, email="pro@example.com")
    set_plan(module, pro, "pro")
    pro_accounts = create_accounts(module, pro, instagram=False, facebook=False)
    pro_accounts.zernio_profile_id = "prof_pro"
    pro_accounts.zernio_instagram_account_id = "acct_ig"
    module.db.session.commit()
    login(client, pro)

    allowed = client.get("/accounts/zernio/connect/facebook")
    pro_accounts.zernio_facebook_account_id = "acct_fb"
    module.db.session.commit()
    pro_status = usage.connected_account_limit_status(
        pro,
        pro_accounts,
        usage_model=module.UserUsage,
        db_session=module.db.session,
    )
    reconnect = client.get("/accounts/zernio/connect/facebook")

    assert allowed.status_code == 302
    assert allowed.location == "https://connect.zernio.test/start"
    assert pro_status["count"] == 2
    assert pro_status["limit"] == 2
    assert pro_status["can_connect"] is False
    assert usage.can_connect_social_account(
        pro,
        pro_accounts,
        platform="facebook",
        usage_model=module.UserUsage,
        db_session=module.db.session,
    ) is True
    assert reconnect.status_code == 302
    assert reconnect.location == "https://connect.zernio.test/start"

    client.get("/logout")
    business = create_user(module, email="business@example.com")
    set_plan(module, business, "business")
    business_accounts = create_accounts(module, business, instagram=False, facebook=False)
    business_accounts.zernio_profile_id = "prof_business"
    business_accounts.zernio_instagram_account_id = "acct_ig"
    business_accounts.zernio_facebook_account_id = "acct_fb"
    business_accounts.linkedin_connected = True
    business_accounts.linkedin_access_token = "token"
    business_accounts.linkedin_member_urn = "urn:li:person:business"
    business_accounts.linkedin_access_token_expires_at = utc_now() + timedelta(hours=1)
    module.db.session.commit()
    login(client, business)

    business_allowed = client.get("/accounts/zernio/connect/instagram")
    business_status = usage.connected_account_limit_status(
        business,
        business_accounts,
        usage_model=module.UserUsage,
        db_session=module.db.session,
    )

    assert business_allowed.status_code == 302
    assert business_allowed.location == "https://connect.zernio.test/start"
    assert business_status["count"] == 3
    assert business_status["limit"] == 3
    assert business_status["can_connect"] is False
    assert ("connect", "instagram") in calls


def test_admin_bypasses_connected_account_limit(client, app, module, monkeypatch):
    app.config.update(
        SMU_ADMIN_EMAILS={"admin@example.com"},
        ZERNIO_API_KEY="sk_test",
    )
    admin = create_user(module, email="admin@example.com")
    accounts = create_accounts(module, admin, instagram=False, facebook=False)
    accounts.zernio_profile_id = "prof_admin"
    accounts.zernio_instagram_account_id = "acct_ig"
    accounts.zernio_facebook_account_id = "acct_fb"
    accounts.linkedin_connected = True
    accounts.linkedin_access_token = "token"
    accounts.linkedin_member_urn = "urn:li:person:123"
    accounts.linkedin_access_token_expires_at = utc_now() + timedelta(hours=1)
    module.db.session.commit()
    login(client, admin)
    monkeypatch.setattr(
        zernio,
        "ensure_profile_for_user",
        lambda current_user, accounts, **kwargs: accounts.zernio_profile_id,
    )
    monkeypatch.setattr(
        zernio,
        "create_connection_url",
        lambda **kwargs: "https://connect.zernio.test/start",
    )

    response = client.get("/accounts/zernio/connect/facebook")

    assert response.status_code == 302
    assert response.location == "https://connect.zernio.test/start"


def test_downgrade_preserves_existing_accounts_but_blocks_new_connections(
    client,
    app,
    module,
    monkeypatch,
):
    user = create_user(module)
    set_plan(module, user, "starter")
    accounts = create_accounts(module, user, instagram=False, facebook=False)
    accounts.zernio_profile_id = "prof_123"
    accounts.zernio_instagram_account_id = "acct_ig"
    accounts.zernio_facebook_account_id = "acct_fb"
    accounts.linkedin_connected = True
    accounts.linkedin_access_token = "token"
    accounts.linkedin_member_urn = "urn:li:person:downgrade"
    accounts.linkedin_access_token_expires_at = utc_now() + timedelta(hours=1)
    module.db.session.commit()
    login(client, user)
    calls = []
    monkeypatch.setattr(
        zernio,
        "ensure_profile_for_user",
        lambda *args, **kwargs: calls.append("profile"),
    )
    monkeypatch.setattr(
        zernio,
        "create_connection_url",
        lambda **kwargs: calls.append(kwargs),
    )

    response = client.get("/settings/accounts")
    html = unescape(response.get_data(as_text=True))
    saved = module.db.session.get(module.ConnectedAccount, accounts.id)
    status = usage.connected_account_limit_status(
        user,
        saved,
        usage_model=module.UserUsage,
        db_session=module.db.session,
    )

    assert response.status_code == 200
    assert "3 of 1" in html
    assert "Your Starter plan includes 1." in html
    assert "Existing connections remain available" in html
    assert saved.zernio_instagram_account_id == "acct_ig"
    assert saved.zernio_facebook_account_id == "acct_fb"
    assert saved.linkedin_connected is True
    assert usage.get_connected_account_count(saved) == 3
    assert status["over_limit"] is True
    assert status["can_connect"] is False
    assert module.UserUsage.query.filter_by(user_id=user.id).one().plan == "starter"
    assert calls == []


def test_callback_preserves_existing_account_and_rejects_excess_new_account(
    client,
    app,
    module,
    monkeypatch,
):
    app.config["ZERNIO_API_KEY"] = "sk_test"
    user = create_user(module)
    set_plan(module, user, "starter")
    accounts = create_accounts(module, user, instagram=False, facebook=False)
    accounts.zernio_profile_id = "prof_123"
    accounts.zernio_instagram_account_id = "acct_ig"
    module.db.session.commit()
    login(client, user)

    def fake_sync(saved_accounts, **kwargs):
        saved_accounts.zernio_facebook_account_id = "acct_fb"
        return [{"_id": "acct_fb", "platform": "facebook"}]

    monkeypatch.setattr(zernio, "sync_connected_account_ids", fake_sync)

    response = client.get(
        "/accounts/zernio/callback?connected=facebook",
        follow_redirects=True,
    )
    saved = module.db.session.get(module.ConnectedAccount, accounts.id)

    assert response.status_code == 200
    assert "your Starter plan includes 1" in response.get_data(as_text=True)
    assert saved.zernio_instagram_account_id == "acct_ig"
    assert saved.zernio_facebook_account_id is None


def test_over_limit_accounts_do_not_block_existing_zernio_publishing(
    client,
    app,
    module,
    monkeypatch,
):
    app.config["ZERNIO_API_KEY"] = "sk_test"
    user = create_user(module)
    set_plan(module, user, "starter")
    accounts = create_accounts(module, user, instagram=False, facebook=False)
    accounts.zernio_profile_id = "prof_123"
    accounts.zernio_instagram_account_id = "acct_ig"
    accounts.zernio_facebook_account_id = "acct_fb"
    post = create_post(
        module,
        user,
        platforms="instagram",
        file_url="https://res.cloudinary.com/demo/image/upload/post.jpg",
    )
    module.db.session.commit()
    calls = []
    login(client, user)

    def fake_publish(published_post, connected_account, **kwargs):
        calls.append((published_post.id, connected_account.id))
        return zernio.ZernioPublishResult(
            provider_post_id="zp_123",
            status="publishing",
            platforms=["instagram"],
        )

    monkeypatch.setattr(zernio, "publish_single_image", fake_publish)

    response = client.post(f"/post/{post.id}/zernio-publish", follow_redirects=True)

    assert response.status_code == 200
    assert calls == [(post.id, accounts.id)]
