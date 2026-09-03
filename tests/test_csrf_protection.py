import re

from smu_core.services import billing

from conftest import create_user, login


def _csrf_token(html):
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def test_csrf_is_enabled_for_normal_configuration(module):
    from config import Config

    assert getattr(Config, "WTF_CSRF_ENABLED", True) is not False
    assert hasattr(module, "csrf")


def test_browser_post_without_csrf_token_is_rejected_friendly(client, app, module):
    app.config["WTF_CSRF_ENABLED"] = True

    response = client.post(
        "/contact",
        data={
            "name": "Andrew",
            "email": "andrew@example.com",
            "message": "Hello from a CSRF test.",
        },
        headers={"Referer": "/contact"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Your session expired. Please try again." in html
    assert "The CSRF token is missing" not in html
    assert module.ContactMessage.query.count() == 0


def test_browser_post_with_csrf_token_reaches_route(client, app, module):
    app.config["WTF_CSRF_ENABLED"] = True

    form_response = client.get("/contact")
    token = _csrf_token(form_response.get_data(as_text=True))

    response = client.post(
        "/contact",
        data={
            "csrf_token": token,
            "name": "Andrew",
            "email": "andrew@example.com",
            "message": "Hello from a CSRF test.",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Thanks. Your message has been received." in response.get_data(as_text=True)
    assert module.ContactMessage.query.count() == 1


def test_representative_forms_render_csrf_tokens(client, app, module):
    app.config["WTF_CSRF_ENABLED"] = True

    for path in ["/login", "/contact"]:
        response = client.get(path)
        html = response.get_data(as_text=True)

        assert response.status_code == 200
        assert 'name="csrf_token"' in html
        assert 'name="csrf-token"' in html

    user = create_user(module)
    login(client, user)

    for path in ["/create", "/settings/accounts", "/billing"]:
        response = client.get(path)
        html = response.get_data(as_text=True)

        assert response.status_code == 200
        assert 'name="csrf_token"' in html
        assert 'name="csrf-token"' in html


def test_calendar_post_fetches_send_csrf_header(client, app, module):
    app.config["WTF_CSRF_ENABLED"] = True
    user = create_user(module)
    login(client, user)

    response = client.get("/calendar")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'name="csrf-token"' in html
    assert "X-CSRFToken" in html
    assert "csrfHeaders" in html


def test_stripe_webhook_remains_exempt_from_csrf(client, app, monkeypatch):
    app.config.update(
        WTF_CSRF_ENABLED=True,
        STRIPE_WEBHOOK_SECRET="whsec_test",
    )
    calls = []

    def fake_construct_webhook_event(**kwargs):
        calls.append(("construct", kwargs["signature"]))
        return {"type": "unknown.event", "data": {"object": {}}}

    def fake_process_webhook_event(event, **kwargs):
        calls.append(("process", event["type"]))

    monkeypatch.setattr(billing, "construct_webhook_event", fake_construct_webhook_event)
    monkeypatch.setattr(billing, "process_webhook_event", fake_process_webhook_event)

    response = client.post(
        "/billing/webhook",
        data=b'{"id":"evt_123"}',
        headers={"Stripe-Signature": "valid-signature"},
    )

    assert response.status_code == 200
    assert calls == [
        ("construct", "valid-signature"),
        ("process", "unknown.event"),
    ]


def test_only_stripe_webhook_is_explicitly_csrf_exempt(module):
    exempt_views = set(getattr(module.csrf, "_exempt_views", set()))
    exempt_blueprints = set(getattr(module.csrf, "_exempt_blueprints", set()))

    assert exempt_blueprints == set()
    assert any(view.endswith(".billing_webhook") for view in exempt_views)
    assert [
        view for view in exempt_views if not view.endswith(".billing_webhook")
    ] == []


def test_oauth_get_callback_is_not_blocked_by_csrf(client, app, module):
    app.config["WTF_CSRF_ENABLED"] = True
    user = create_user(module)
    login(client, user)

    response = client.get("/accounts/linkedin/callback", follow_redirects=True)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "LinkedIn connection could not be verified. Please try again." in html
    assert "Your session expired. Please try again." not in html
