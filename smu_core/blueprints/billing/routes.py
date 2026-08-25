from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from smu_core.extensions import db
from smu_core.models import User
from smu_core.services import billing as billing_service


billing_bp = Blueprint("billing", __name__)


def _log_event(event_name, **fields):
    log_event = current_app.extensions.get("smu_log_event")
    if log_event:
        log_event(event_name, **fields)


@login_required
def billing_account():
    user = current_user._get_current_object()
    subscription = billing_service.get_subscription_display(user)
    return render_template(
        "billing.html",
        subscription=subscription,
        price_display=current_app.config.get(
            "SMU_MONTHLY_PRICE_DISPLAY",
            "Monthly subscription",
        ),
    )


@login_required
def billing_checkout():
    user = current_user._get_current_object()

    try:
        checkout_session = billing_service.create_checkout_session(
            user,
            secret_key=current_app.config.get("STRIPE_SECRET_KEY", ""),
            price_id=current_app.config.get("STRIPE_PRICE_ID", ""),
            success_url=url_for("billing_success", _external=True),
            cancel_url=url_for("billing_cancel", _external=True),
        )
    except billing_service.BillingConfigurationError as exc:
        current_app.logger.warning(
            "Stripe billing configuration missing: %s",
            exc.__class__.__name__,
        )
        flash("Subscription checkout is not configured yet.", "warning")
        return redirect(url_for("index"))

    _log_event("stripe_checkout_session_created", user_id=user.id)
    return redirect(checkout_session.url)


@login_required
def billing_portal():
    user = current_user._get_current_object()
    subscription = billing_service.get_subscription_display(user)

    if not user.stripe_customer_id:
        _log_event(
            "stripe_customer_portal_unavailable",
            user_id=user.id,
            stripe_customer_configured=False,
            subscription_display_state=subscription["status_label"],
        )
        flash(
            "No billing account is available yet. Choose a plan to get started.",
            "warning",
        )
        return redirect(url_for("billing_account"))

    try:
        portal_session = billing_service.create_customer_portal_session(
            user,
            secret_key=current_app.config.get("STRIPE_SECRET_KEY", ""),
            return_url=url_for("billing_account", _external=True),
        )
    except billing_service.BillingConfigurationError as exc:
        current_app.logger.warning(
            "Stripe portal configuration missing: %s",
            exc.__class__.__name__,
        )
        _log_event(
            "stripe_customer_portal_unavailable",
            user_id=user.id,
            stripe_customer_configured=True,
            subscription_display_state=subscription["status_label"],
        )
        flash("We couldn't open billing management right now. Please try again.", "warning")
        return redirect(url_for("billing_account"))
    except Exception as exc:
        current_app.logger.warning(
            "Stripe portal session creation failed: %s",
            exc.__class__.__name__,
        )
        _log_event(
            "stripe_customer_portal_unavailable",
            user_id=user.id,
            stripe_customer_configured=True,
            subscription_display_state=subscription["status_label"],
        )
        flash("We couldn't open billing management right now. Please try again.", "warning")
        return redirect(url_for("billing_account"))

    _log_event(
        "stripe_customer_portal_session_created",
        user_id=user.id,
        stripe_customer_configured=True,
        subscription_display_state=subscription["status_label"],
    )
    return redirect(portal_session.url)


@login_required
def billing_success():
    user = current_user._get_current_object()
    return render_template(
        "billing_success.html",
        subscription=billing_service.get_subscription_display(user),
    )


@login_required
def billing_cancel():
    return render_template("billing_cancel.html")


def billing_webhook():
    payload = request.get_data()
    signature = request.headers.get("Stripe-Signature", "")

    try:
        event = billing_service.construct_webhook_event(
            payload=payload,
            signature=signature,
            webhook_secret=current_app.config.get("STRIPE_WEBHOOK_SECRET", ""),
        )
        billing_service.process_webhook_event(
            event,
            user_model=User,
            db_session=db.session,
        )
    except billing_service.BillingConfigurationError:
        current_app.logger.warning("Stripe webhook configuration missing.")
        return "", 400
    except billing_service.BillingWebhookError as exc:
        current_app.logger.warning(
            "Stripe webhook rejected: %s",
            exc.__class__.__name__,
        )
        return "", 400
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(
            "Stripe webhook processing failed: %s",
            exc.__class__.__name__,
        )
        return "", 500

    event_type = getattr(event, "type", None)
    if isinstance(event, dict):
        event_type = event.get("type")
    _log_event("stripe_webhook_processed", event_type=event_type)
    return "", 200


@billing_bp.record_once
def register_billing_routes(state):
    app = state.app
    routes = [
        ("/billing", "billing_account", billing_account, ["GET"]),
        ("/billing/checkout", "billing_checkout", billing_checkout, ["POST"]),
        ("/billing/portal", "billing_portal", billing_portal, ["POST"]),
        ("/billing/success", "billing_success", billing_success, ["GET"]),
        ("/billing/cancel", "billing_cancel", billing_cancel, ["GET"]),
        ("/billing/webhook", "billing_webhook", billing_webhook, ["POST"]),
    ]

    for rule, endpoint, view_func, methods in routes:
        app.add_url_rule(rule, endpoint, view_func, methods=methods)
