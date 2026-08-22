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


def has_active_subscription(user):
    return getattr(user, "subscription_status", None) in ACTIVE_SUBSCRIPTION_STATUSES


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


def process_webhook_event(event, *, user_model, db_session):
    event_type = _get(event, "type")
    obj = _get(_get(event, "data", {}), "object", {})

    if event_type == "checkout.session.completed":
        return process_checkout_completed(obj, user_model=user_model, db_session=db_session)
    if event_type == "invoice.paid":
        return process_invoice_paid(obj, user_model=user_model, db_session=db_session)
    if event_type == "invoice.payment_failed":
        return process_invoice_payment_failed(obj, user_model=user_model, db_session=db_session)
    if event_type == "customer.subscription.updated":
        return process_subscription_updated(obj, user_model=user_model, db_session=db_session)
    if event_type == "customer.subscription.deleted":
        return process_subscription_deleted(obj, user_model=user_model, db_session=db_session)

    return None


def process_checkout_completed(session, *, user_model, db_session):
    user = _user_from_checkout_session(session, user_model=user_model, db_session=db_session)
    if not user:
        return None

    customer_id = _get(session, "customer")
    subscription = _get(session, "subscription")
    subscription_id = _object_id(subscription)

    if customer_id:
        user.stripe_customer_id = customer_id
    if subscription_id:
        user.stripe_subscription_id = subscription_id

    _apply_subscription_object(user, subscription)
    db_session.commit()
    return user


def process_invoice_paid(invoice, *, user_model, db_session):
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


def _apply_subscription_object(user, subscription):
    if not subscription or isinstance(subscription, str):
        return

    status = _get(subscription, "status")
    period_end = _get(subscription, "current_period_end")

    if status:
        user.subscription_status = status
    if period_end:
        user.subscription_current_period_end = _datetime_from_unix(period_end)


def _datetime_from_unix(value):
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None

    return datetime.fromtimestamp(timestamp, timezone.utc).replace(tzinfo=None)
