from datetime import datetime, timedelta

import pytest

from conftest import create_user
from smu_core.services import billing
from smu_core.services.time_utils import utc_now


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


class FakeSubscription:
    calls = []
    responses = {}
    error = None

    @classmethod
    def retrieve(cls, subscription_id):
        cls.calls.append(subscription_id)
        if cls.error:
            raise cls.error
        return cls.responses[subscription_id]


class FakeStripe:
    api_key = None

    class checkout:
        Session = FakeCheckoutSession

    class billing_portal:
        Session = FakePortalSession

    Subscription = FakeSubscription


class AttrObject:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def reset_fake_stripe():
    FakeStripe.api_key = None
    FakeCheckoutSession.calls = []
    FakePortalSession.calls = []
    FakeSubscription.calls = []
    FakeSubscription.responses = {}
    FakeSubscription.error = None


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
            "metadata": {"smu_user_id": "42", "smu_plan": "pro"},
            "subscription_data": {"metadata": {"smu_user_id": "42", "smu_plan": "pro"}},
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


def test_checkout_session_includes_requested_internal_plan_metadata():
    reset_fake_stripe()
    user = type("User", (), {
        "id": 7,
        "email": "owner@example.com",
        "stripe_customer_id": None,
    })()

    billing.create_checkout_session(
        user,
        secret_key="sk_test_123",
        price_id="price_starter",
        plan="starter",
        success_url="https://smu.test/success",
        cancel_url="https://smu.test/cancel",
        stripe_module=FakeStripe,
    )

    call = FakeCheckoutSession.calls[0]
    assert call["line_items"] == [{"price": "price_starter", "quantity": 1}]
    assert call["metadata"]["smu_plan"] == "starter"
    assert call["subscription_data"]["metadata"]["smu_plan"] == "starter"


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


def test_plan_price_mapping_prefers_explicit_prices_and_preserves_legacy_pro():
    config = {
        "STRIPE_PRICE_STARTER": "price_starter",
        "STRIPE_PRICE_PRO": "price_pro",
        "STRIPE_PRICE_BUSINESS": "price_business",
        "STRIPE_PRICE_ID": "price_legacy",
    }

    assert billing.price_id_for_plan("starter", config) == "price_starter"
    assert billing.price_id_for_plan("pro", config) == "price_pro"
    assert billing.price_id_for_plan("business", config) == "price_business"
    assert billing.plan_for_price_id("price_starter", config) == "starter"
    assert billing.plan_for_price_id("price_pro", config) == "pro"
    assert billing.plan_for_price_id("price_business", config) == "business"
    assert billing.plan_for_price_id("price_legacy", config) == "pro"


def test_legacy_stripe_price_id_remains_pro_checkout_fallback():
    config = {
        "STRIPE_PRICE_STARTER": "",
        "STRIPE_PRICE_PRO": "",
        "STRIPE_PRICE_BUSINESS": "",
        "STRIPE_PRICE_ID": "price_legacy",
    }

    assert billing.price_id_for_plan("pro", config) == "price_legacy"
    with pytest.raises(billing.BillingConfigurationError):
        billing.price_id_for_plan("starter", config)
    with pytest.raises(billing.BillingConfigurationError):
        billing.price_id_for_plan("unknown", config)


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


def test_checkout_completed_retrieves_subscription_id_for_initial_period(app, module):
    reset_fake_stripe()
    user = create_user(module)
    FakeSubscription.responses["sub_live_123"] = {
        "id": "sub_live_123",
        "customer": "cus_live_123",
        "status": "active",
        "cancel_at_period_end": False,
        "cancel_at": None,
        "items": {
            "data": [
                {
                    "current_period_end": 1790294400,
                },
            ],
        },
    }

    updated = billing.process_checkout_completed(
        {
            "customer": "cus_live_123",
            "subscription": "sub_live_123",
            "metadata": {
                "smu_user_id": str(user.id),
            },
        },
        user_model=module.User,
        db_session=module.db.session,
        stripe_module=FakeStripe,
        secret_key="sk_live_test",
    )

    assert FakeStripe.api_key == "sk_live_test"
    assert FakeSubscription.calls == ["sub_live_123"]
    assert updated.stripe_customer_id == "cus_live_123"
    assert updated.stripe_subscription_id == "sub_live_123"
    assert updated.subscription_status == "active"
    assert updated.subscription_current_period_end == datetime(2026, 9, 25)
    assert updated.subscription_cancel_at_period_end is False
    assert billing.has_active_subscription(updated) is True


def test_checkout_completed_uses_expanded_subscription_without_retrieve(app, module):
    reset_fake_stripe()
    user = create_user(module)

    updated = billing.process_checkout_completed(
        {
            "customer": "cus_live_123",
            "subscription": {
                "id": "sub_live_123",
                "customer": "cus_live_123",
                "status": "active",
                "cancel_at_period_end": False,
                "items": {
                    "data": [
                        {
                            "current_period_end": 1790294400,
                        },
                    ],
                },
            },
            "metadata": {
                "smu_user_id": str(user.id),
            },
        },
        user_model=module.User,
        db_session=module.db.session,
        stripe_module=FakeStripe,
        secret_key="sk_live_test",
    )

    assert FakeSubscription.calls == []
    assert updated.stripe_subscription_id == "sub_live_123"
    assert updated.subscription_status == "active"
    assert updated.subscription_current_period_end == datetime(2026, 9, 25)


def test_checkout_completed_retrieve_failure_is_not_silently_accepted(app, module):
    reset_fake_stripe()
    user = create_user(module)
    FakeSubscription.error = RuntimeError("stripe unavailable")

    with pytest.raises(RuntimeError, match="stripe unavailable"):
        billing.process_checkout_completed(
            {
                "customer": "cus_live_123",
                "subscription": "sub_live_123",
                "metadata": {
                    "smu_user_id": str(user.id),
                },
            },
            user_model=module.User,
            db_session=module.db.session,
            stripe_module=FakeStripe,
            secret_key="sk_live_test",
        )

    assert FakeSubscription.calls == ["sub_live_123"]


def test_invoice_paid_updates_existing_subscription(app, module):
    user = create_user(module)
    user.stripe_customer_id = "cus_123"
    user.stripe_subscription_id = "sub_123"
    user.subscription_status = "past_due"
    module.db.session.commit()
    reset_fake_stripe()
    FakeSubscription.responses["sub_123"] = {
        "id": "sub_123",
        "customer": "cus_123",
        "status": "active",
        "cancel_at_period_end": False,
        "items": {
            "data": [
                {
                    "current_period_end": 1790294400,
                },
            ],
        },
    }

    updated = billing.process_invoice_paid(
        {
            "customer": "cus_123",
            "subscription": "sub_123",
        },
        user_model=module.User,
        db_session=module.db.session,
        stripe_module=FakeStripe,
        secret_key="sk_live_test",
    )

    assert updated.id == user.id
    assert updated.subscription_status == "active"
    assert updated.subscription_current_period_end == datetime(2026, 9, 25)


def test_invoice_paid_retrieves_subscription_when_period_metadata_missing(app, module):
    reset_fake_stripe()
    user = create_user(module)
    user.stripe_customer_id = "cus_live_123"
    user.stripe_subscription_id = "sub_live_123"
    user.subscription_status = "past_due"
    module.db.session.commit()
    FakeSubscription.responses["sub_live_123"] = {
        "id": "sub_live_123",
        "customer": "cus_live_123",
        "status": "active",
        "cancel_at_period_end": False,
        "items": {
            "data": [
                {
                    "current_period_end": 1790294400,
                },
            ],
        },
    }

    updated = billing.process_invoice_paid(
        {
            "customer": "cus_live_123",
            "subscription": "sub_live_123",
        },
        user_model=module.User,
        db_session=module.db.session,
        stripe_module=FakeStripe,
        secret_key="sk_live_test",
    )

    assert FakeSubscription.calls == ["sub_live_123"]
    assert updated.subscription_status == "active"
    assert updated.subscription_current_period_end == datetime(2026, 9, 25)


def test_invoice_paid_does_not_retrieve_when_period_metadata_exists(app, module):
    reset_fake_stripe()
    user = create_user(module)
    user.stripe_customer_id = "cus_live_123"
    user.stripe_subscription_id = "sub_live_123"
    user.subscription_status = "active"
    user.subscription_current_period_end = datetime(2026, 9, 25)
    module.db.session.commit()

    updated = billing.process_invoice_paid(
        {
            "customer": "cus_live_123",
            "subscription": "sub_live_123",
        },
        user_model=module.User,
        db_session=module.db.session,
        stripe_module=FakeStripe,
        secret_key="sk_live_test",
    )

    assert FakeSubscription.calls == []
    assert updated.subscription_status == "active"
    assert updated.subscription_current_period_end == datetime(2026, 9, 25)


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


def test_subscription_updated_syncs_usage_plan_from_price_without_resetting_counters(
    app,
    module,
):
    now = utc_now()
    usage_period_start = now - timedelta(days=1)
    usage_period_end = now + timedelta(days=1)
    user = create_user(module)
    user.stripe_customer_id = "cus_123"
    usage = module.UserUsage(
        user_id=user.id,
        plan="starter",
        ai_images_used=12,
        content_packs_used=4,
        usage_period_start=usage_period_start,
        usage_period_end=usage_period_end,
    )
    module.db.session.add(usage)
    module.db.session.commit()
    config = {
        "STRIPE_PRICE_STARTER": "price_starter",
        "STRIPE_PRICE_PRO": "price_pro",
        "STRIPE_PRICE_BUSINESS": "price_business",
        "STRIPE_PRICE_ID": "",
    }

    updated = billing.process_subscription_updated(
        {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
            "current_period_end": 1893456000,
            "items": {
                "data": [
                    {"price": {"id": "price_business"}},
                ],
            },
        },
        user_model=module.User,
        db_session=module.db.session,
        usage_model=module.UserUsage,
        config=config,
    )

    assert updated.subscription_status == "active"
    assert usage.plan == "business"
    assert usage.ai_images_used == 12
    assert usage.content_packs_used == 4
    assert usage.usage_period_start == usage_period_start
    assert usage.usage_period_end == usage_period_end


def test_subscription_updated_ignores_unknown_price_without_changing_usage_plan(
    app,
    module,
):
    user = create_user(module)
    user.stripe_customer_id = "cus_123"
    usage = module.UserUsage(
        user_id=user.id,
        plan="pro",
        ai_images_used=8,
        content_packs_used=2,
        usage_period_start=datetime(2026, 8, 1),
        usage_period_end=datetime(2026, 9, 1),
    )
    module.db.session.add(usage)
    module.db.session.commit()

    billing.process_subscription_updated(
        {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
            "items": {
                "data": [
                    {"price": {"id": "price_unknown"}},
                ],
            },
        },
        user_model=module.User,
        db_session=module.db.session,
        usage_model=module.UserUsage,
        config={
            "STRIPE_PRICE_STARTER": "price_starter",
            "STRIPE_PRICE_PRO": "price_pro",
            "STRIPE_PRICE_BUSINESS": "price_business",
            "STRIPE_PRICE_ID": "",
        },
    )

    assert usage.plan == "pro"
    assert usage.ai_images_used == 8
    assert usage.content_packs_used == 2


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
    reset_fake_stripe()
    user = create_user(module)
    FakeSubscription.responses["sub_123"] = {
        "id": "sub_123",
        "customer": "cus_123",
        "status": "active",
        "cancel_at_period_end": False,
        "current_period_end": 1893456000,
    }
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
        stripe_module=FakeStripe,
        secret_key="sk_test_123",
    )
    billing.process_checkout_completed(
        event_session,
        user_model=module.User,
        db_session=module.db.session,
        stripe_module=FakeStripe,
        secret_key="sk_test_123",
    )

    users = module.User.query.filter_by(stripe_customer_id="cus_123").all()
    assert users == [user]
