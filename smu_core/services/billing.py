from datetime import datetime, timezone


try:
    import stripe as stripe_sdk
except ImportError:  # pragma: no cover - exercised when dependency is not installed.
    stripe_sdk = None


ACTIVE_SUBSCRIPTION_STATUSES = {"active", "trialing"}
INVOICE_PAID_ACTIVATABLE_STATUSES = {None, "past_due", "unpaid", "incomplete"}


class BillingConfigurationError(RuntimeError):
    """Raised when billing is used before Stripe is configured."""


class BillingWebhookError(RuntimeError):
    """Raised when a Stripe webhook cannot be verified or parsed."""


class BillingCustomerPortalError(RuntimeError):
    """Raised when a Stripe Customer Portal session cannot be created."""


def has_active_subscription(user):
    return getattr(user, "subscription_status", None) in ACTIVE_SUBSCRIPTION_STATUSES


def get_subscription_display(user):
    status = getattr(user, "subscription_status", None)
    status_label = {
        "active": "Active",
        "trialing": "Trial",
        "past_due": "Payment issue",
        "unpaid": "Payment required",
        "canceled": "Canceled",
        "incomplete": "Setup incomplete",
        "incomplete_expired": "Setup expired",
        "paused": "Paused",
        None: "No active subscription",
    }.get(status, "Subscription unavailable")

    access_active = has_active_subscription(user)
    period_end = getattr(user, "subscription_current_period_end", None)
    cancel_at_period_end = bool(
        getattr(user, "subscription_cancel_at_period_end", False)
    )
    period_label = None
    cancellation_label = None
    cancellation_date_label = None
    if period_end and status in ACTIVE_SUBSCRIPTION_STATUSES:
        formatted_period_end = period_end.strftime("%d %B %Y")
        if cancel_at_period_end:
            cancellation_date_label = formatted_period_end
            cancellation_label = f"Cancels on {formatted_period_end}"
        else:
            period_label = f"Renews on {formatted_period_end}"

    return {
        "raw_status": status,
        "status_label": status_label,
        "access_active": access_active,
        "access_label": "Active" if access_active else "Inactive",
        "cancel_at_period_end": cancel_at_period_end,
        "period_label": period_label,
        "cancellation_label": cancellation_label,
        "cancellation_date_label": cancellation_date_label,
        "has_customer": bool(getattr(user, "stripe_customer_id", None)),
    }


def create_checkout_session(
    user,
    *,
    secret_key,
    price_id,
    success_url,
    cancel_url,
    stripe_module=None,
):
    if not secret_key:
        raise BillingConfigurationError("Stripe secret key is not configured.")
    if not price_id:
        raise BillingConfigurationError("Stripe price ID is not configured.")

    stripe = _stripe_module(stripe_module)
    stripe.api_key = secret_key

    metadata = {"smu_user_id": str(user.id)}
    params = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": str(user.id),
        "metadata": metadata,
        "subscription_data": {"metadata": metadata},
    }

    if getattr(user, "stripe_customer_id", None):
        params["customer"] = user.stripe_customer_id
    else:
        params["customer_email"] = user.email

    return stripe.checkout.Session.create(**params)


def create_customer_portal_session(
    user,
    *,
    secret_key,
    return_url,
    stripe_module=None,
):
    if not secret_key:
        raise BillingConfigurationError("Stripe secret key is not configured.")
    if not getattr(user, "stripe_customer_id", None):
        raise BillingCustomerPortalError("Stripe customer ID is not configured.")

    stripe = _stripe_module(stripe_module)
    stripe.api_key = secret_key

    return stripe.billing_portal.Session.create(
        customer=user.stripe_customer_id,
        return_url=return_url,
    )


def construct_webhook_event(
    *,
    payload,
    signature,
    webhook_secret,
    stripe_module=None,
):
    if not webhook_secret:
        raise BillingConfigurationError("Stripe webhook secret is not configured.")
    if not signature:
        raise BillingWebhookError("Missing Stripe signature.")

    stripe = _stripe_module(stripe_module)

    try:
        return stripe.Webhook.construct_event(payload, signature, webhook_secret)
    except ValueError as exc:
        raise BillingWebhookError("Malformed Stripe webhook payload.") from exc
    except Exception as exc:
        if exc.__class__.__name__ == "SignatureVerificationError":
            raise BillingWebhookError("Invalid Stripe webhook signature.") from exc
        raise


def process_webhook_event(
    event,
    *,
    user_model,
    db_session,
    stripe_module=None,
    secret_key=None,
):
    event_type = _get(event, "type")
    obj = _get(_get(event, "data", {}), "object", {})

    if event_type == "checkout.session.completed":
        return process_checkout_completed(
            obj,
            user_model=user_model,
            db_session=db_session,
            stripe_module=stripe_module,
            secret_key=secret_key,
        )
    if event_type == "invoice.paid":
        return process_invoice_paid(
            obj,
            user_model=user_model,
            db_session=db_session,
            stripe_module=stripe_module,
            secret_key=secret_key,
        )
    if event_type == "invoice.payment_failed":
        return process_invoice_payment_failed(obj, user_model=user_model, db_session=db_session)
    if event_type == "customer.subscription.updated":
        return process_subscription_updated(obj, user_model=user_model, db_session=db_session)
    if event_type == "customer.subscription.deleted":
        return process_subscription_deleted(obj, user_model=user_model, db_session=db_session)

    return None


def process_checkout_completed(
    session,
    *,
    user_model,
    db_session,
    stripe_module=None,
    secret_key=None,
):
    user = _user_from_checkout_session(session, user_model=user_model, db_session=db_session)
    if not user:
        return None

    customer_id = _get(session, "customer")
    subscription = _get(session, "subscription")
    subscription_id = _object_id(subscription)
    subscription = _resolve_subscription_object(
        subscription,
        stripe_module=stripe_module,
        secret_key=secret_key,
    )

    if customer_id:
        user.stripe_customer_id = customer_id
    if subscription_id:
        user.stripe_subscription_id = subscription_id

    _apply_subscription_object(user, subscription)
    db_session.commit()
    return user


def process_invoice_paid(
    invoice,
    *,
    user_model,
    db_session,
    stripe_module=None,
    secret_key=None,
):
    user = _user_from_customer_or_subscription(
        invoice,
        user_model=user_model,
        db_session=db_session,
    )
    if not user:
        return None

    _apply_customer_and_subscription_ids(user, invoice)
    subscription = _get(invoice, "subscription")
    if not isinstance(subscription, str) or _subscription_metadata_incomplete(user):
        subscription = _resolve_subscription_object(
            subscription,
            stripe_module=stripe_module,
            secret_key=secret_key,
        )
    _apply_subscription_object(user, subscription)
    if user.subscription_status in INVOICE_PAID_ACTIVATABLE_STATUSES:
        user.subscription_status = "active"

    db_session.commit()
    return user


def process_invoice_payment_failed(invoice, *, user_model, db_session):
    user = _user_from_customer_or_subscription(
        invoice,
        user_model=user_model,
        db_session=db_session,
    )
    if not user:
        return None

    _apply_customer_and_subscription_ids(user, invoice)
    subscription = _get(invoice, "subscription")
    _apply_subscription_object(user, subscription)
    if not user.subscription_status or user.subscription_status in ACTIVE_SUBSCRIPTION_STATUSES:
        user.subscription_status = "past_due"

    db_session.commit()
    return user


def process_subscription_updated(subscription, *, user_model, db_session):
    user = _user_from_customer_or_subscription(
        subscription,
        user_model=user_model,
        db_session=db_session,
    )
    if not user:
        return None

    _apply_customer_and_subscription_ids(user, subscription)
    _apply_subscription_object(user, subscription)
    db_session.commit()
    return user


def process_subscription_deleted(subscription, *, user_model, db_session):
    user = _user_from_customer_or_subscription(
        subscription,
        user_model=user_model,
        db_session=db_session,
    )
    if not user:
        return None

    _apply_customer_and_subscription_ids(user, subscription)
    _apply_subscription_object(user, subscription)
    user.subscription_cancel_at_period_end = False
    user.subscription_status = "canceled"
    db_session.commit()
    return user


def _stripe_module(stripe_module=None):
    stripe = stripe_module or stripe_sdk
    if stripe is None:
        raise BillingConfigurationError("Stripe Python SDK is not installed.")
    return stripe


def _get(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _object_id(value):
    if not value:
        return None
    if isinstance(value, str):
        return value
    return _get(value, "id")


def _metadata_value(obj, key):
    metadata = _get(obj, "metadata", {}) or {}
    return _get(metadata, key)


def _user_from_checkout_session(session, *, user_model, db_session):
    user_id = _metadata_value(session, "smu_user_id") or _get(
        session,
        "client_reference_id",
    )
    user = _user_by_id(user_id, user_model=user_model, db_session=db_session)
    if user:
        return user

    return _user_from_customer_or_subscription(
        session,
        user_model=user_model,
        db_session=db_session,
    )


def _user_by_id(user_id, *, user_model, db_session):
    if user_id is None:
        return None
    try:
        return db_session.get(user_model, int(user_id))
    except (TypeError, ValueError):
        return None


def _user_from_customer_or_subscription(obj, *, user_model, db_session):
    customer_id = _object_id(_get(obj, "customer"))
    subscription_id = _object_id(_get(obj, "subscription")) or _object_id(obj)

    query = user_model.query
    if customer_id:
        user = query.filter_by(stripe_customer_id=customer_id).first()
        if user:
            return user

    if subscription_id:
        user = query.filter_by(stripe_subscription_id=subscription_id).first()
        if user:
            return user

    return None


def _apply_customer_and_subscription_ids(user, obj):
    customer_id = _object_id(_get(obj, "customer"))
    subscription_id = _object_id(_get(obj, "subscription")) or _object_id(obj)

    if customer_id:
        user.stripe_customer_id = customer_id
    if subscription_id:
        user.stripe_subscription_id = subscription_id


def _resolve_subscription_object(subscription, *, stripe_module=None, secret_key=None):
    if not isinstance(subscription, str):
        return subscription

    stripe = _stripe_module(stripe_module)
    if secret_key:
        stripe.api_key = secret_key
    return stripe.Subscription.retrieve(subscription)


def _subscription_metadata_incomplete(user):
    return (
        user.subscription_status in INVOICE_PAID_ACTIVATABLE_STATUSES
        and user.subscription_current_period_end is None
    )


def _apply_subscription_object(user, subscription):
    if not subscription or isinstance(subscription, str):
        return

    status = _get(subscription, "status")
    period_end = _get_subscription_period_end(subscription)
    cancel_at_period_end = _get(subscription, "cancel_at_period_end")
    cancel_at = _get(subscription, "cancel_at")

    if status:
        user.subscription_status = status
    if period_end:
        user.subscription_current_period_end = _datetime_from_unix(period_end)
    if cancel_at_period_end is not None or cancel_at is not None:
        user.subscription_cancel_at_period_end = (
            _subscription_has_scheduled_cancellation(subscription)
        )


def _subscription_has_scheduled_cancellation(subscription):
    if _get(subscription, "cancel_at_period_end") is True:
        return True

    cancel_at = _get(subscription, "cancel_at")
    if cancel_at is None:
        return False

    return _datetime_from_unix(cancel_at) is not None


def _get_subscription_period_end(subscription):
    period_end = _get(subscription, "current_period_end")
    if period_end:
        return period_end

    items = _get(subscription, "items", {}) or {}
    item_data = _get(items, "data", []) or []
    for item in item_data:
        item_period_end = _get(item, "current_period_end")
        if item_period_end:
            return item_period_end

    return None


def _datetime_from_unix(value):
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None

    return datetime.fromtimestamp(timestamp, timezone.utc).replace(tzinfo=None)
