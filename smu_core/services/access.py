from functools import wraps

from flask import current_app, flash, redirect, url_for
from flask_login import current_user

from smu_core.services.billing import has_active_subscription


SUBSCRIPTION_REQUIRED_MESSAGE = (
    "An active SMU subscription is required to use this feature."
)


def registration_mode():
    mode = (current_app.config.get("REGISTRATION_MODE") or "subscription").lower()
    if mode not in {"subscription", "beta", "open"}:
        return "subscription"
    return mode


def is_admin_user(user):
    email = (getattr(user, "email", "") or "").strip().lower()
    admin_emails = current_app.config.get("SMU_ADMIN_EMAILS") or set()
    return bool(email and email in admin_emails)


def has_product_access(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False

    if registration_mode() == "open":
        return True

    if is_admin_user(user):
        return True

    return has_active_subscription(user)


def subscription_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        user = (
            current_user._get_current_object()
            if current_user.is_authenticated
            else None
        )
        if has_product_access(user):
            return view_func(*args, **kwargs)

        flash(SUBSCRIPTION_REQUIRED_MESSAGE, "warning")
        return redirect(url_for("pricing"))

    return wrapped
