from datetime import datetime

import pytest

from conftest import create_user
from smu_core.services import billing


class FakeCheckoutSession:
    calls = []

    @classmethod
    def create(cls, **kwargs):
        cls.calls.append(kwargs)
        return type("Session", (), {"url": "https://checkout.stripe.test/session"})()


class FakeStripe:
    api_key = None

    class checkout:
        Session = FakeCheckoutSession


def reset_fake_stripe():
    FakeStripe.api_key = None
    FakeCheckoutSession.calls = []


def test_active_subscription_statuses():
    user = type("User", (), {"subscription_status": "active"})()
    trialing = type("User", (), {"subscription_status": "trialing"})()
    past_due = type("User", (), {"subscription_status": "past_due"})()
    canceled = type("User", (), {"subscription_status": "canceled"})()
    missing = type("User", (), {"subscription_status": None})()

    assert billing.has_active_subscription(user) is True
    assert billing.has_active_subscription(trialing) is True
    assert billing.has_active_subscription(past_due) is False
    assert billing.has_active_subscription(canceled) is False
    assert billing.has_active_subscription(missing) is False


def test_checkout_session_call_shape_for_new_customer(module):
    reset_fake_stripe()
    user = type("User", (), {
        "id": 42,
        "email": "owner@example.com",
        "stripe_customer_id": None,
    })()

    session = billing.create_checkout_session(
        user,
        secret_key="sk_test_123",
        price_id="price_123",
        success_url="https://smu.test/billing/success",
        cancel_url="https://smu.test/billing/cancel",
        stripe_module=FakeStripe,
    )

    assert session.url == "https://checkout.stripe.test/session"
    assert FakeStripe.api_key == "sk_test_123"
    assert FakeCheckoutSession.calls == [
        {
            "mode": "subscription",
            "line_items": [{"price": "price_123", "quantity": 1}],
            "success_url": "https://smu.test/billing/success",
            "cancel_url": "https://smu.test/billing/cancel",
            "client_reference_id": "42",
            "metadata": {"smu_user_id": "42"},
            "subscription_data": {"metadata": {"smu_user_id": "42"}},
            "customer_email": "owner@example.com",
        }
    ]


def test_checkout_session_reuses_existing_customer():
    reset_fake_stripe()
    user = type("User", (), {
        "id": 7,
        "email": "owner@example.com",
        "stripe_customer_id": "cus_existing",
    })()

    billing.create_checkout_session(
        user,
        secret_key="sk_test_123",
        price_id="price_123",
        success_url="https://smu.test/success",
        cancel_url="https://smu.test/cancel",
        stripe_module=FakeStripe,
    )

    call = FakeCheckoutSession.calls[0]
    assert call["customer"] == "cus_existing"
    assert "customer_email" not in call


def test_checkout_session_requires_configured_keys():
    user = type("User", (), {"id": 1, "email": "owner@example.com"})()

    with pytest.raises(billing.BillingConfigurationError):
        billing.create_checkout_session(
            user,
            secret_key="",
            price_id="price_123",
            success_url="https://smu.test/success",
            cancel_url="https://smu.test/cancel",
            stripe_module=FakeStripe,
        )

    with pytest.raises(billing.BillingConfigurationError):
        billing.create_checkout_session(
            user,
            secret_key="sk_test_123",
            price_id="",
            success_url="https://smu.test/success",
            cancel_url="https://smu.test/cancel",
            stripe_module=FakeStripe,
        )


def test_checkout_completed_stores_customer_and_subscription(app, module):
    user = create_user(module)
    event_session = {
        "client_reference_id": str(user.id),
        "metadata": {"smu_user_id": str(user.id)},
        "customer": "cus_123",
        "subscription": {
            "id": "sub_123",
            "status": "active",
            "current_period_end": 1893456000,
        },
    }

    updated = billing.process_checkout_completed(
        event_session,
        user_model=module.User,
        db_session=module.db.session,
    )

    assert updated.id == user.id
    assert updated.stripe_customer_id == "cus_123"
    assert updated.stripe_subscription_id == "sub_123"
    assert updated.subscription_status == "active"
    assert isinstance(updated.subscription_current_period_end, datetime)
    assert updated.subscription_current_period_end.tzinfo is None


def test_invoice_paid_updates_existing_subscription(app, module):
    user = create_user(module)
    user.stripe_customer_id = "cus_123"
    user.stripe_subscription_id = "sub_123"
    user.subscription_status = "past_due"
    module.db.session.commit()

    updated = billing.process_invoice_paid(
        {
            "customer": "cus_123",
            "subscription": "sub_123",
        },
        user_model=module.User,
        db_session=module.db.session,
    )

    assert updated.id == user.id
    assert updated.subscription_status == "active"


def test_invoice_paid_does_not_reactivate_canceled_subscription(app, module):
    user = create_user(module)
    user.stripe_customer_id = "cus_123"
    user.stripe_subscription_id = "sub_123"
    user.subscription_status = "canceled"
    module.db.session.commit()

    updated = billing.process_invoice_paid(
        {
            "customer": "cus_123",
            "subscription": "sub_123",
        },
        user_model=module.User,
        db_session=module.db.session,
    )

    assert updated.subscription_status == "canceled"


def test_invoice_payment_failed_updates_non_active_state(app, module):
    user = create_user(module)
    user.stripe_customer_id = "cus_123"
    user.stripe_subscription_id = "sub_123"
    user.subscription_status = "active"
    module.db.session.commit()

    updated = billing.process_invoice_payment_failed(
        {
            "customer": "cus_123",
            "subscription": "sub_123",
        },
        user_model=module.User,
        db_session=module.db.session,
    )

    assert updated.subscription_status == "past_due"


def test_subscription_updated_sets_status_and_period(app, module):
    user = create_user(module)
    user.stripe_customer_id = "cus_123"
    module.db.session.commit()

    updated = billing.process_subscription_updated(
        {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "trialing",
            "current_period_end": 1893456000,
        },
        user_model=module.User,
        db_session=module.db.session,
    )

    assert updated.stripe_subscription_id == "sub_123"
    assert updated.subscription_status == "trialing"
    assert updated.subscription_current_period_end.tzinfo is None


def test_subscription_deleted_marks_canceled(app, module):
    user = create_user(module)
    user.stripe_customer_id = "cus_123"
    user.stripe_subscription_id = "sub_123"
    user.subscription_status = "active"
    module.db.session.commit()

    updated = billing.process_subscription_deleted(
        {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
        },
        user_model=module.User,
        db_session=module.db.session,
    )

    assert updated.stripe_customer_id == "cus_123"
    assert updated.stripe_subscription_id == "sub_123"
    assert updated.subscription_status == "canceled"


def test_duplicate_event_processing_is_idempotent(app, module):
    user = create_user(module)
    event_session = {
        "client_reference_id": str(user.id),
        "metadata": {"smu_user_id": str(user.id)},
        "customer": "cus_123",
        "subscription": "sub_123",
    }

    billing.process_checkout_completed(
        event_session,
        user_model=module.User,
        db_session=module.db.session,
    )
    billing.process_checkout_completed(
        event_session,
        user_model=module.User,
        db_session=module.db.session,
    )

    users = module.User.query.filter_by(stripe_customer_id="cus_123").all()
    assert users == [user]
