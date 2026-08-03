from contextlib import contextmanager

from flask import template_rendered, url_for

import app as smu_app
from conftest import create_accounts, create_post, create_user, login
from smu_core.models import ConnectedAccount


ALL_ENABLED_FORM = {
    "instagram_connected": "on",
    "facebook_connected": "on",
    "linkedin_connected": "on",
    "pinterest_connected": "on",
    "reddit_connected": "on",
    "x_connected": "on",
    "make_webhook_single": "  https://make.test/single  ",
    "make_webhook_carousel": "  https://make.test/carousel  ",
}


@contextmanager
def captured_templates(app):
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append((template.name, context))

    template_rendered.connect(record, app)
    try:
        yield recorded
    finally:
        template_rendered.disconnect(record, app)


def rules_for(app, path):
    return [rule for rule in app.url_map.iter_rules() if rule.rule == path]


def test_accounts_blueprint_is_registered_once(module):
    assert "accounts" in module.app.blueprints
    assert list(module.app.blueprints).count("accounts") == 1


def test_accounts_route_preserves_endpoint_and_methods(module):
    rules = rules_for(module.app, "/settings/accounts")

    assert len(rules) == 1
    assert rules[0].endpoint == "connected_accounts"
    assert {"GET", "POST"}.issubset(rules[0].methods)


def test_accounts_url_for_compatibility(module):
    with module.app.test_request_context():
        assert url_for("connected_accounts") == "/settings/accounts"


def test_accounts_get_requires_login(client):
    response = client.get("/settings/accounts")

    assert response.status_code == 302
    assert "/login" in response.location


def test_accounts_get_creates_current_user_row_and_renders_template(client, app, module):
    user = create_user(module)
    login(client, user)

    with captured_templates(app) as templates:
        response = client.get("/settings/accounts")
    account = module.ConnectedAccount.query.filter_by(user_id=user.id).first()

    assert response.status_code == 200
    assert templates[0][0] == "connected_accounts.html"
    assert templates[0][1]["accounts"] is account
    assert templates[0][1]["enabled_count"] == 0
    assert templates[0][1]["webhooks_ready"] == (False, False)
    assert account is not None
    assert account.created_at is not None
    assert account.updated_at is not None


def test_accounts_post_creates_row_stores_toggles_and_webhooks(client, module):
    user = create_user(module)
    login(client, user)

    response = client.post(
        "/settings/accounts",
        data=ALL_ENABLED_FORM,
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)
    account = module.ConnectedAccount.query.filter_by(user_id=user.id).first()

    assert response.status_code == 200
    assert "Connected accounts updated." in html
    assert account is not None
    assert account.instagram_connected is True
    assert account.facebook_connected is True
    assert account.linkedin_connected is True
    assert account.pinterest_connected is True
    assert account.reddit_connected is True
    assert account.x_connected is True
    assert account.make_webhook_single == "https://make.test/single"
    assert account.make_webhook_carousel == "https://make.test/carousel"
    assert account.created_at is not None
    assert account.updated_at is not None


def test_accounts_post_updates_existing_row_and_allows_clearing(client, module):
    user = create_user(module)
    account = create_accounts(
        module,
        user,
        single_webhook="https://make.test/old-single",
        carousel_webhook="https://make.test/old-carousel",
        instagram=True,
        facebook=True,
    )
    login(client, user)

    response = client.post(
        "/settings/accounts",
        data={
            "pinterest_connected": "on",
            "make_webhook_single": "",
            "make_webhook_carousel": "  https://make.test/new-carousel  ",
        },
    )
    accounts = module.ConnectedAccount.query.filter_by(user_id=user.id).all()

    assert response.status_code == 302
    assert len(accounts) == 1
    assert accounts[0].id == account.id
    assert accounts[0].instagram_connected is False
    assert accounts[0].facebook_connected is False
    assert accounts[0].linkedin_connected is False
    assert accounts[0].pinterest_connected is True
    assert accounts[0].reddit_connected is False
    assert accounts[0].x_connected is False
    assert accounts[0].make_webhook_single == ""
    assert accounts[0].make_webhook_carousel == "https://make.test/new-carousel"


def test_accounts_route_isolates_users(client, app, module):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    owner_account = create_accounts(
        module,
        owner,
        single_webhook="https://make.test/owner-single",
        carousel_webhook="https://make.test/owner-carousel",
        instagram=True,
        facebook=False,
    )
    login(client, other)

    with captured_templates(app) as templates:
        get_response = client.get("/settings/accounts")
    post_response = client.post(
        "/settings/accounts",
        data={
            "x_connected": "on",
            "make_webhook_single": "https://make.test/other-single",
            "make_webhook_carousel": "https://make.test/other-carousel",
        },
    )
    saved_owner = module.ConnectedAccount.query.filter_by(user_id=owner.id).first()
    saved_other = module.ConnectedAccount.query.filter_by(user_id=other.id).first()

    assert get_response.status_code == 200
    assert templates[0][1]["accounts"] is saved_other
    assert "https://make.test/owner-single" not in get_response.get_data(as_text=True)
    assert post_response.status_code == 302
    assert saved_owner.id == owner_account.id
    assert saved_owner.make_webhook_single == "https://make.test/owner-single"
    assert saved_owner.instagram_connected is True
    assert saved_other.x_connected is True
    assert saved_other.make_webhook_single == "https://make.test/other-single"
    assert module.ConnectedAccount.query.count() == 2


def test_publishing_helpers_still_use_user_settings_and_fallback(app, module, monkeypatch):
    with app.app_context():
        user = create_user(module)
        create_accounts(
            module,
            user,
            single_webhook="https://make.test/user-single",
            carousel_webhook="https://make.test/user-carousel",
            instagram=True,
            facebook=False,
        )
        fallback_user = create_user(module, email="fallback@example.com")
        create_accounts(
            module,
            fallback_user,
            single_webhook="",
            carousel_webhook="",
            instagram=False,
            facebook=True,
        )
        monkeypatch.setattr(module, "MAKE_WEBHOOK_SINGLE", "https://make.test/global-single")
        monkeypatch.setattr(module, "MAKE_WEBHOOK_CAROUSEL", "https://make.test/global-carousel")

        assert module.get_enabled_platforms_for_user(
            ["instagram", "facebook", "pinterest"],
            user_id=user.id,
        ) == ["instagram"]
        assert module.get_user_make_webhook("single", user_id=user.id) == (
            "https://make.test/user-single"
        )
        assert module.get_user_make_webhook("carousel", user_id=user.id) == (
            "https://make.test/user-carousel"
        )
        assert module.get_user_make_webhook("single", user_id=fallback_user.id) == (
            "https://make.test/global-single"
        )
        assert module.get_user_make_webhook("carousel", user_id=fallback_user.id) == (
            "https://make.test/global-carousel"
        )


def test_scheduled_publish_resolves_settings_from_post_owner(app, module, monkeypatch):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    create_accounts(
        module,
        owner,
        single_webhook="https://make.test/owner-single",
        instagram=True,
        facebook=False,
    )
    create_accounts(
        module,
        other,
        single_webhook="https://make.test/other-single",
        instagram=True,
        facebook=True,
    )
    post = create_post(module, owner, platforms="instagram,facebook")
    captured = {}

    def fake_send_payload_to_make(payload, webhook_url=None):
        captured["payload"] = payload
        captured["webhook_url"] = webhook_url
        return object()

    monkeypatch.setattr(module, "send_payload_to_make", fake_send_payload_to_make)

    module.publish_post_to_make(post, user_id=post.user_id)

    assert captured["webhook_url"] == "https://make.test/owner-single"
    assert captured["payload"]["platforms"] == ["instagram"]


def test_connected_account_model_and_app_import_compatibility_remain(module):
    assert smu_app.ConnectedAccount is ConnectedAccount
    assert module.ConnectedAccount is ConnectedAccount


def test_existing_blueprints_and_unrelated_endpoints_remain_registered(module):
    endpoints = {rule.endpoint for rule in module.app.url_map.iter_rules()}

    assert {
        "landing_page",
        "privacy_policy",
        "terms_of_service",
        "maintenance",
        "help_centre",
        "contact",
        "register",
        "login",
        "logout",
        "beta_apply",
        "admin_beta",
        "submit_feedback",
        "brand_brief",
        "index",
        "calendar_view",
        "create_post",
        "post_studio",
        "send_to_make",
    }.issubset(endpoints)


def test_scheduler_reference_is_untouched(module):
    assert module.scheduler is smu_app.scheduler
