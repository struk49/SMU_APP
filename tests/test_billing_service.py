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


class FakePortalSession:
    calls = []

    @classmethod
    def create(cls, **kwargs):
        cls.calls.append(kwargs)
        return type("Session", (), {"url": "https://billing.stripe.test/session"})()


class FakeStripe:
    api_key = None

    class checkout:
        Session = FakeCheckoutSession

    class billing_portal:
        Session = FakePortalSession


class AttrObject:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def reset_fake_stripe():
    FakeStripe.api_key = None
    FakeCheckoutSession.calls = []
    FakePortalSession.calls = []


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


def test_customer_portal_session_call_shape():
    reset_fake_stripe()
    user = type("User", (), {
        "id": 42,
        "email": "owner@example.com",
        "stripe_customer_id": "cus_existing",
    })()

    session = billing.create_customer_portal_session(
        user,
        secret_key="sk_test_123",
        return_url="https://smu.test/billing",
        stripe_module=FakeStripe,
    )

    assert session.url == "https://billing.stripe.test/session"
    assert FakeStripe.api_key == "sk_test_123"
    assert FakePortalSession.calls == [
        {
            "customer": "cus_existing",
            "return_url": "https://smu.test/billing",
        }
    ]


def test_customer_portal_session_requires_secret_and_customer():
    user = type("User", (), {"stripe_customer_id": "cus_existing"})()
    missing_customer = type("User", (), {"stripe_customer_id": None})()

    with pytest.raises(billing.BillingConfigurationError):
        billing.create_customer_portal_session(
            user,
            secret_key="",
            return_url="https://smu.test/billing",
            stripe_module=FakeStripe,
        )

    with pytest.raises(billing.BillingCustomerPortalError):
        billing.create_customer_portal_session(
            missing_customer,
            secret_key="sk_test_123",
            return_url="https://smu.test/billing",
            stripe_module=FakeStripe,
        )


@pytest.mark.parametrize(
    ("raw_status", "label", "access_active"),
    [
        ("active", "Active", True),
        ("trialing", "Trial", True),
        ("past_due", "Payment issue", False),
        ("unpaid", "Payment required", False),
        ("canceled", "Canceled", False),
        ("incomplete", "Setup incomplete", False),
        ("incomplete_expired", "Setup expired", False),
        ("paused", "Paused", False),
        (None, "No active subscription", False),
        ("mystery", "Subscription unavailable", False),
    ],
)
def test_subscription_display_mapping(raw_status, label, access_active):
    user = type("User", (), {
        "subscription_status": raw_status,
        "subscription_current_period_end": datetime(2030, 1, 1),
        "subscription_cancel_at_period_end": False,
        "stripe_customer_id": "cus_123",
    })()

    display = billing.get_subscription_display(user)

    assert display["raw_status"] == raw_status
    assert display["status_label"] == label
    assert display["access_active"] is access_active
    assert display["has_customer"] is True
    if access_active:
        assert display["period_label"] == "Renews on 01 January 2030"
    else:
        assert display["period_label"] is None
    assert display["cancel_at_period_end"] is False
    assert display["cancellation_label"] is None


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


def test_subscription_updated_persists_scheduled_cancellation(app, module):
    user = create_user(module)
    user.stripe_customer_id = "cus_123"
    module.db.session.commit()

    updated = billing.process_subscription_updated(
        {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
            "current_period_end": 1790186400,
            "cancel_at_period_end": True,
        },
        user_model=module.User,
        db_session=module.db.session,
    )
    display = billing.get_subscription_display(updated)

    assert updated.stripe_subscription_id == "sub_123"
    assert updated.subscription_status == "active"
    assert updated.subscription_cancel_at_period_end is True
    assert updated.subscription_current_period_end.tzinfo is None
    assert billing.has_active_subscription(updated) is True
    assert display["status_label"] == "Active"
    assert display["access_active"] is True
    assert display["cancel_at_period_end"] is True
    assert display["cancellation_label"] == "Cancels on 23 September 2026"
    assert display["period_label"] is None


def test_subscription_updated_reads_period_from_subscription_items(app, module):
    user = create_user(module)
    user.stripe_customer_id = "cus_123"
    module.db.session.commit()

    updated = billing.process_subscription_updated(
        {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
            "cancel_at_period_end": True,
            "items": {
                "data": [
                    {"id": "si_123", "current_period_end": 1790186400},
                ],
            },
        },
        user_model=module.User,
        db_session=module.db.session,
    )
    display = billing.get_subscription_display(updated)

    assert updated.stripe_subscription_id == "sub_123"
    assert updated.subscription_status == "active"
    assert updated.subscription_cancel_at_period_end is True
    assert updated.subscription_current_period_end == datetime(2026, 9, 23, 18, 0)
    assert billing.has_active_subscription(updated) is True
    assert display["cancellation_label"] == "Cancels on 23 September 2026"


def test_subscription_updated_detects_cancel_at_scheduled_cancellation(app, module):
    user = create_user(module)
    user.stripe_customer_id = "cus_123"
    module.db.session.commit()

    updated = billing.process_subscription_updated(
        {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
            "cancel_at_period_end": False,
            "cancel_at": 1790163936,
            "canceled_at": 1787572214,
            "items": {
                "data": [
                    {
                        "current_period_end": 1790163936,
                    },
                ],
            },
        },
        user_model=module.User,
        db_session=module.db.session,
    )
    display = billing.get_subscription_display(updated)

    assert updated.subscription_status == "active"
    assert updated.subscription_cancel_at_period_end is True
    assert updated.subscription_current_period_end == datetime(2026, 9, 23, 11, 45, 36)
    assert billing.has_active_subscription(updated) is True
    assert display["status_label"] == "Active"
    assert display["access_active"] is True
    assert display["cancellation_label"] == "Cancels on 23 September 2026"
    assert display["period_label"] is None


def test_subscription_updated_clears_cancel_at_scheduled_cancellation(app, module):
    user = create_user(module)
    user.stripe_customer_id = "cus_123"
    user.subscription_cancel_at_period_end = True
    module.db.session.commit()

    updated = billing.process_subscription_updated(
        {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
            "cancel_at_period_end": False,
            "cancel_at": None,
            "items": {
                "data": [
                    {
                        "current_period_end": 1790163936,
                    },
                ],
            },
        },
        user_model=module.User,
        db_session=module.db.session,
    )
    display = billing.get_subscription_display(updated)

    assert updated.subscription_status == "active"
    assert updated.subscription_cancel_at_period_end is False
    assert updated.subscription_current_period_end == datetime(2026, 9, 23, 11, 45, 36)
    assert billing.has_active_subscription(updated) is True
    assert display["status_label"] == "Active"
    assert display["period_label"] == "Renews on 23 September 2026"
    assert display["cancellation_label"] is None


def test_subscription_updated_reads_item_period_from_stripe_objects(app, module):
    user = create_user(module)
    user.stripe_customer_id = "cus_123"
    module.db.session.commit()

    subscription = AttrObject(
        id="sub_123",
        customer="cus_123",
        status="active",
        cancel_at_period_end=False,
        items=AttrObject(
            data=[
                AttrObject(id="si_123", current_period_end=1790186400),
            ],
        ),
    )

    updated = billing.process_subscription_updated(
        subscription,
        user_model=module.User,
        db_session=module.db.session,
    )

    assert updated.subscription_status == "active"
    assert updated.subscription_cancel_at_period_end is False
    assert updated.subscription_current_period_end == datetime(2026, 9, 23, 18, 0)


def test_subscription_updated_handles_missing_subscription_items(app, module):
    user = create_user(module)
    user.stripe_customer_id = "cus_123"
    module.db.session.commit()

    updated = billing.process_subscription_updated(
        {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
        },
        user_model=module.User,
        db_session=module.db.session,
    )

    assert updated.subscription_status == "active"
    assert updated.subscription_current_period_end is None


def test_subscription_updated_handles_empty_subscription_items(app, module):
    user = create_user(module)
    user.stripe_customer_id = "cus_123"
    module.db.session.commit()

    updated = billing.process_subscription_updated(
        {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
            "items": {"data": []},
        },
        user_model=module.User,
        db_session=module.db.session,
    )

    assert updated.subscription_status == "active"
    assert updated.subscription_current_period_end is None


def test_subscription_updated_preserves_cancellation_when_field_missing(app, module):
    user = create_user(module)
    user.stripe_customer_id = "cus_123"
    user.subscription_cancel_at_period_end = True
    module.db.session.commit()

    updated = billing.process_subscription_updated(
        {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
            "current_period_end": 1790186400,
        },
        user_model=module.User,
        db_session=module.db.session,
    )

    assert updated.subscription_status == "active"
    assert updated.subscription_cancel_at_period_end is True
    assert billing.has_active_subscription(updated) is True


def test_subscription_updated_clears_scheduled_cancellation(app, module):
    user = create_user(module)
    user.stripe_customer_id = "cus_123"
    user.subscription_cancel_at_period_end = True
    module.db.session.commit()

    updated = billing.process_subscription_updated(
        {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
            "current_period_end": 1790186400,
            "cancel_at_period_end": False,
        },
        user_model=module.User,
        db_session=module.db.session,
    )

    assert updated.subscription_status == "active"
    assert updated.subscription_cancel_at_period_end is False
    assert billing.has_active_subscription(updated) is True


def test_subscription_deleted_marks_canceled(app, module):
    user = create_user(module)
    user.stripe_customer_id = "cus_123"
    user.stripe_subscription_id = "sub_123"
    user.subscription_status = "active"
    user.subscription_cancel_at_period_end = True
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
    assert updated.subscription_cancel_at_period_end is False


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
