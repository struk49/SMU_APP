from flask import url_for

from conftest import create_user, login
from smu_core.services import billing


def test_billing_routes_registered_with_compatible_endpoint_names(module):
    routes = {
        rule.endpoint: rule.rule
        for rule in module.app.url_map.iter_rules()
        if rule.rule.startswith("/billing")
    }

    assert routes["billing_checkout"] == "/billing/checkout"
    assert routes["billing_success"] == "/billing/success"
    assert routes["billing_cancel"] == "/billing/cancel"
    assert routes["billing_webhook"] == "/billing/webhook"

    with module.app.test_request_context():
        assert url_for("billing_checkout") == "/billing/checkout"
        assert url_for("billing_success") == "/billing/success"
        assert url_for("billing_cancel") == "/billing/cancel"
        assert url_for("billing_webhook") == "/billing/webhook"


def test_checkout_requires_login(client):
    response = client.post("/billing/checkout")

    assert response.status_code == 302
    assert "/login" in response.location


def test_missing_stripe_config_is_handled_safely(client, app, module):
    app.config.update(
        STRIPE_SECRET_KEY="",
        STRIPE_PRICE_ID="",
    )
    user = create_user(module)
    login(client, user)

    response = client.post("/billing/checkout", follow_redirects=True)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Subscription checkout is not configured yet." in html
    assert "STRIPE_SECRET_KEY" not in html
    assert "sk_test" not in html


def test_authenticated_checkout_redirects_to_stripe(client, app, module, monkeypatch):
    app.config.update(
        STRIPE_SECRET_KEY="sk_test_123",
        STRIPE_PRICE_ID="price_123",
        SERVER_NAME="smu.test",
    )
    user = create_user(module)
    login(client, user)
    calls = []

    def fake_create_checkout_session(user_arg, **kwargs):
        calls.append((user_arg, kwargs))
        return type("Session", (), {"url": "https://checkout.stripe.test/session"})()

    monkeypatch.setattr(
        billing,
        "create_checkout_session",
        fake_create_checkout_session,
    )

    response = client.post("/billing/checkout")

    assert response.status_code == 302
    assert response.location == "https://checkout.stripe.test/session"
    assert calls[0][0].id == user.id
    assert calls[0][1]["secret_key"] == "sk_test_123"
    assert calls[0][1]["price_id"] == "price_123"
    assert calls[0][1]["success_url"] == "http://smu.test/billing/success"
    assert calls[0][1]["cancel_url"] == "http://smu.test/billing/cancel"


def test_success_and_cancel_do_not_mark_subscription_active(client, module):
    user = create_user(module)
    login(client, user)

    success = client.get("/billing/success")
    cancel = client.get("/billing/cancel")
    module.db.session.refresh(user)

    assert success.status_code == 200
    assert cancel.status_code == 200
    assert user.subscription_status is None


def test_webhook_does_not_require_login_and_processes_verified_event(
    client,
    app,
    module,
    monkeypatch,
):
    app.config.update(STRIPE_WEBHOOK_SECRET="whsec_test")
    user = create_user(module)
    calls = []

    def fake_construct_webhook_event(**kwargs):
        assert kwargs["payload"] == b'{"id":"evt_123"}'
        assert kwargs["signature"] == "valid-signature"
        assert kwargs["webhook_secret"] == "whsec_test"
        return {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "client_reference_id": str(user.id),
                    "metadata": {"smu_user_id": str(user.id)},
                    "customer": "cus_123",
                    "subscription": "sub_123",
                }
            },
        }

    monkeypatch.setattr(
        billing,
        "construct_webhook_event",
        fake_construct_webhook_event,
    )
    original_process = billing.process_webhook_event

    def spy_process_webhook_event(event, **kwargs):
        calls.append(event["type"])
        return original_process(event, **kwargs)

    monkeypatch.setattr(
        billing,
        "process_webhook_event",
        spy_process_webhook_event,
    )

    response = client.post(
        "/billing/webhook",
        data=b'{"id":"evt_123"}',
        headers={"Stripe-Signature": "valid-signature"},
    )
    module.db.session.refresh(user)

    assert response.status_code == 200
    assert calls == ["checkout.session.completed"]
    assert user.stripe_customer_id == "cus_123"
    assert user.stripe_subscription_id == "sub_123"


def test_webhook_rejects_invalid_signature(client, app, monkeypatch):
    app.config.update(STRIPE_WEBHOOK_SECRET="whsec_test")

    def fake_construct_webhook_event(**kwargs):
        raise billing.BillingWebhookError("Invalid Stripe webhook signature.")

    monkeypatch.setattr(
        billing,
        "construct_webhook_event",
        fake_construct_webhook_event,
    )

    response = client.post(
        "/billing/webhook",
        data=b"{}",
        headers={"Stripe-Signature": "bad"},
    )

    assert response.status_code == 400


def test_webhook_rejects_malformed_payload(client, app, monkeypatch):
    app.config.update(STRIPE_WEBHOOK_SECRET="whsec_test")

    def fake_construct_webhook_event(**kwargs):
        raise billing.BillingWebhookError("Malformed Stripe webhook payload.")

    monkeypatch.setattr(
        billing,
        "construct_webhook_event",
        fake_construct_webhook_event,
    )

    response = client.post(
        "/billing/webhook",
        data=b"not-json",
        headers={"Stripe-Signature": "valid-signature"},
    )

    assert response.status_code == 400


def test_unknown_webhook_event_returns_success(client, app, monkeypatch):
    app.config.update(STRIPE_WEBHOOK_SECRET="whsec_test")

    monkeypatch.setattr(
        billing,
        "construct_webhook_event",
        lambda **kwargs: {
            "type": "customer.created",
            "data": {"object": {"id": "cus_123"}},
        },
    )

    response = client.post(
        "/billing/webhook",
        data=b"{}",
        headers={"Stripe-Signature": "valid-signature"},
    )

    assert response.status_code == 200
