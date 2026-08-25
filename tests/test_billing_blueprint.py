from html import unescape
from datetime import datetime

from flask import url_for

from conftest import create_user, login
from smu_core.services import billing


def test_billing_routes_registered_with_compatible_endpoint_names(module):
    routes = {
        rule.endpoint: rule.rule
        for rule in module.app.url_map.iter_rules()
        if rule.rule.startswith("/billing")
    }

    assert routes["billing_account"] == "/billing"
    assert routes["billing_checkout"] == "/billing/checkout"
    assert routes["billing_portal"] == "/billing/portal"
    assert routes["billing_success"] == "/billing/success"
    assert routes["billing_cancel"] == "/billing/cancel"
    assert routes["billing_webhook"] == "/billing/webhook"

    with module.app.test_request_context():
        assert url_for("billing_account") == "/billing"
        assert url_for("billing_checkout") == "/billing/checkout"
        assert url_for("billing_portal") == "/billing/portal"
        assert url_for("billing_success") == "/billing/success"
        assert url_for("billing_cancel") == "/billing/cancel"
        assert url_for("billing_webhook") == "/billing/webhook"


def test_checkout_requires_login(client):
    response = client.post("/billing/checkout")

    assert response.status_code == 302
    assert "/login" in response.location


def test_billing_page_requires_login(client):
    response = client.get("/billing")

    assert response.status_code == 302
    assert "/login" in response.location


def test_billing_page_renders_unpaid_active_and_canceled_states(client, module):
    unpaid = create_user(module, email="billing-unpaid@example.com")
    login(client, unpaid)
    unpaid_response = client.get("/billing")
    client.get("/logout")

    active = create_user(module, email="billing-active@example.com")
    active.stripe_customer_id = "cus_active"
    active.subscription_status = "active"
    module.db.session.commit()
    login(client, active)
    active_response = client.get("/billing")
    client.get("/logout")

    canceled = create_user(module, email="billing-canceled@example.com")
    canceled.stripe_customer_id = "cus_canceled"
    canceled.subscription_status = "canceled"
    module.db.session.commit()
    login(client, canceled)
    canceled_response = client.get("/billing")

    unpaid_html = unpaid_response.get_data(as_text=True)
    active_html = active_response.get_data(as_text=True)
    canceled_html = canceled_response.get_data(as_text=True)

    assert unpaid_response.status_code == 200
    assert "No active subscription" in unpaid_html
    assert "Subscribe with Stripe" in unpaid_html
    assert "Manage Subscription" not in unpaid_html

    assert active_response.status_code == 200
    assert "Active" in active_html
    assert "Manage Subscription" in active_html
    assert "Go to Dashboard" in active_html

    assert canceled_response.status_code == 200
    assert "Canceled" in canceled_html
    assert "Subscribe Again" in canceled_html
    assert "Manage Subscription" in canceled_html

    for html in [unpaid_html, active_html, canceled_html]:
        assert "cus_" not in html
        assert "sk_test" not in html


def test_billing_page_shows_scheduled_cancellation_without_blocking_access(
    client,
    module,
):
    active = create_user(module, email="billing-renewing@example.com")
    active.stripe_customer_id = "cus_active"
    active.subscription_status = "active"
    active.subscription_current_period_end = datetime(2026, 9, 23, 18, 0)
    active.subscription_cancel_at_period_end = False
    module.db.session.commit()
    login(client, active)
    active_response = client.get("/billing")
    client.get("/logout")

    canceling = create_user(module, email="billing-canceling@example.com")
    canceling.stripe_customer_id = "cus_canceling"
    canceling.subscription_status = "active"
    canceling.subscription_current_period_end = datetime(2026, 9, 23, 18, 0)
    canceling.subscription_cancel_at_period_end = True
    module.db.session.commit()
    login(client, canceling)
    canceling_response = client.get("/billing")

    active_html = active_response.get_data(as_text=True)
    canceling_html = canceling_response.get_data(as_text=True)

    assert active_response.status_code == 200
    assert "Active" in active_html
    assert "Renews on 23 September 2026" in active_html
    assert "Cancels on" not in active_html

    assert canceling_response.status_code == 200
    assert "Active" in canceling_html
    assert "Cancels on 23 September 2026" in canceling_html
    assert "Your subscription is active until 23 September 2026." in canceling_html
    assert "Manage Subscription" in canceling_html
    assert "subscription_cancel_at_period_end" not in canceling_html
    assert "True" not in canceling_html
    assert "cus_canceling" not in canceling_html


def test_billing_page_canceled_subscription_is_not_scheduled_active(client, module):
    user = create_user(module, email="billing-ended@example.com")
    user.stripe_customer_id = "cus_ended"
    user.subscription_status = "canceled"
    user.subscription_current_period_end = datetime(2026, 9, 23, 18, 0)
    user.subscription_cancel_at_period_end = False
    module.db.session.commit()
    login(client, user)

    response = client.get("/billing")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Canceled" in html
    assert "Cancels on" not in html
    assert "Your subscription is active until" not in html
    assert "Subscribe Again" in html


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


def test_portal_requires_login(client):
    response = client.post("/billing/portal")

    assert response.status_code == 302
    assert "/login" in response.location


def test_portal_redirects_to_stripe_for_active_user(client, app, module, monkeypatch):
    app.config.update(STRIPE_SECRET_KEY="sk_test_123", SERVER_NAME="smu.test")
    user = create_user(module)
    user.stripe_customer_id = "cus_owner"
    user.subscription_status = "active"
    module.db.session.commit()
    login(client, user)
    calls = []

    def fake_create_customer_portal_session(user_arg, **kwargs):
        calls.append((user_arg, kwargs))
        return type("Session", (), {"url": "https://billing.stripe.test/session"})()

    monkeypatch.setattr(
        billing,
        "create_customer_portal_session",
        fake_create_customer_portal_session,
    )

    response = client.post("/billing/portal")

    assert response.status_code == 302
    assert response.location == "https://billing.stripe.test/session"
    assert calls[0][0].id == user.id
    assert calls[0][0].stripe_customer_id == "cus_owner"
    assert calls[0][1]["secret_key"] == "sk_test_123"
    assert calls[0][1]["return_url"] == "http://smu.test/billing"


def test_portal_allows_past_due_user_with_customer(client, app, module, monkeypatch):
    app.config.update(STRIPE_SECRET_KEY="sk_test_123", SERVER_NAME="smu.test")
    user = create_user(module)
    user.stripe_customer_id = "cus_past_due"
    user.subscription_status = "past_due"
    module.db.session.commit()
    login(client, user)

    monkeypatch.setattr(
        billing,
        "create_customer_portal_session",
        lambda user_arg, **kwargs: type(
            "Session",
            (),
            {"url": "https://billing.stripe.test/past-due"},
        )(),
    )

    response = client.post("/billing/portal")

    assert response.status_code == 302
    assert response.location == "https://billing.stripe.test/past-due"


def test_portal_missing_customer_fails_safely(client, module):
    user = create_user(module)
    login(client, user)

    response = client.post("/billing/portal", follow_redirects=True)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "No billing account is available yet. Choose a plan to get started." in html
    assert "Manage Subscription" not in html


def test_portal_service_failure_uses_friendly_message(client, app, module, monkeypatch):
    app.config.update(STRIPE_SECRET_KEY="sk_test_123")
    user = create_user(module)
    user.stripe_customer_id = "cus_owner"
    module.db.session.commit()
    login(client, user)

    def fake_create_customer_portal_session(*args, **kwargs):
        raise RuntimeError("raw stripe failure")

    monkeypatch.setattr(
        billing,
        "create_customer_portal_session",
        fake_create_customer_portal_session,
    )

    response = client.post("/billing/portal", follow_redirects=True)
    html = response.get_data(as_text=True)
    text = unescape(html)

    assert response.status_code == 200
    assert "We couldn't open billing management right now. Please try again." in text
    assert "raw stripe failure" not in text
    assert "sk_test" not in text
    assert "cus_owner" not in text


def test_portal_uses_current_users_customer_only(client, app, module, monkeypatch):
    app.config.update(STRIPE_SECRET_KEY="sk_test_123", SERVER_NAME="smu.test")
    owner = create_user(module, email="portal-owner@example.com")
    other = create_user(module, email="portal-other@example.com")
    owner.stripe_customer_id = "cus_owner"
    other.stripe_customer_id = "cus_other"
    module.db.session.commit()
    login(client, owner)
    calls = []

    def fake_create_customer_portal_session(user_arg, **kwargs):
        calls.append(user_arg.stripe_customer_id)
        return type("Session", (), {"url": "https://billing.stripe.test/session"})()

    monkeypatch.setattr(
        billing,
        "create_customer_portal_session",
        fake_create_customer_portal_session,
    )

    response = client.post("/billing/portal", data={"customer": "cus_other"})

    assert response.status_code == 302
    assert calls == ["cus_owner"]


def test_pricing_page_shows_billing_aware_ctas(client, app, module):
    app.config["REGISTRATION_MODE"] = "subscription"
    anonymous_html = client.get("/pricing").get_data(as_text=True)

    unpaid = create_user(module, email="pricing-unpaid@example.com")
    login(client, unpaid)
    unpaid_html = client.get("/pricing").get_data(as_text=True)
    client.get("/logout")

    active = create_user(module, email="pricing-active@example.com")
    active.stripe_customer_id = "cus_active"
    active.subscription_status = "active"
    module.db.session.commit()
    login(client, active)
    active_html = client.get("/pricing").get_data(as_text=True)
    client.get("/logout")

    canceled = create_user(module, email="pricing-canceled@example.com")
    canceled.stripe_customer_id = "cus_canceled"
    canceled.subscription_status = "canceled"
    module.db.session.commit()
    login(client, canceled)
    canceled_html = client.get("/pricing").get_data(as_text=True)

    assert "Create Account" in anonymous_html
    assert "Subscribe with Stripe" in unpaid_html
    assert 'action="/billing/checkout"' in unpaid_html
    assert "Current Plan" in active_html
    assert "Manage Billing" in active_html
    assert "Subscribe Again" in canceled_html
    assert "Manage Billing" in canceled_html


def test_success_and_cancel_do_not_mark_subscription_active(client, module):
    user = create_user(module)
    login(client, user)

    success = client.get("/billing/success")
    cancel = client.get("/billing/cancel")
    module.db.session.refresh(user)

    assert success.status_code == 200
    assert cancel.status_code == 200
    assert user.subscription_status is None


def test_success_page_reflects_persisted_subscription_state(client, module):
    active = create_user(module, email="success-active@example.com")
    active.subscription_status = "active"
    module.db.session.commit()
    login(client, active)
    active_response = client.get("/billing/success")
    client.get("/logout")

    inactive = create_user(module, email="success-inactive@example.com")
    login(client, inactive)
    inactive_response = client.get("/billing/success")

    assert "Your subscription is confirmed" in active_response.get_data(as_text=True)
    assert "Go to Dashboard" in active_response.get_data(as_text=True)
    inactive_html = inactive_response.get_data(as_text=True)
    assert "We're confirming your subscription." in inactive_html
    assert "Refresh Status" in inactive_html
    assert inactive.subscription_status is None


def test_cancel_page_describes_canceled_checkout_not_subscription(client, module):
    user = create_user(module)
    login(client, user)

    response = client.get("/billing/cancel")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Checkout canceled" in html
    assert "No payment was completed" in html
    assert "Your subscription has been canceled" not in html


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
                    "subscription": {
                        "id": "sub_123",
                        "customer": "cus_123",
                        "status": "active",
                        "current_period_end": 1893456000,
                    },
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
