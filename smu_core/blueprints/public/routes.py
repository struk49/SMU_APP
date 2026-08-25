import re

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from smu_core.extensions import db
from smu_core.models import ContactMessage
from smu_core.services.access import has_product_access
from smu_core.services import billing as billing_service


public_bp = Blueprint("public", __name__)


def is_valid_email(email):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email or ""))


def field_too_long(value, max_length):
    return len(value or "") > max_length


def _log_event(event_name, **fields):
    log_event = current_app.extensions.get("smu_log_event")
    if log_event:
        log_event(event_name, **fields)


def landing_page():
    return render_template("landing.html")


def pricing():
    user = current_user._get_current_object() if current_user.is_authenticated else None
    return render_template(
        "pricing.html",
        has_access=has_product_access(user),
        subscription=(
            billing_service.get_subscription_display(user)
            if user
            else None
        ),
        price_display=current_app.config.get(
            "SMU_MONTHLY_PRICE_DISPLAY",
            "Monthly subscription",
        ),
    )


def about_page():
    return render_template("about.html")


def privacy_policy():
    return render_template("privacy.html")


def terms_of_service():
    return render_template("terms.html")


def maintenance():
    return render_template("maintenance.html"), 503


@login_required
def help_centre():
    return render_template("help.html")


def contact():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        message = request.form.get("message", "").strip()

        errors = []
        if not name:
            errors.append("Name is required.")
        if not is_valid_email(email):
            errors.append("A valid email is required.")
        if not message:
            errors.append("Message is required.")
        if field_too_long(name, 120) or field_too_long(email, 150):
            errors.append("Name or email is too long.")
        if field_too_long(message, 2000):
            errors.append("Message must be 2000 characters or fewer.")

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template("contact.html"), 400

        contact_message = ContactMessage(
            name=name,
            email=email,
            message=message,
        )
        db.session.add(contact_message)
        db.session.commit()
        _log_event("contact_submission", contact_message_id=contact_message.id)

        flash("Thanks. Your message has been received.", "success")
        return redirect(url_for("contact"))

    return render_template("contact.html")


@public_bp.record_once
def register_public_routes(state):
    app = state.app
    routes = [
        ("/landing", "landing_page", landing_page, None),
        ("/pricing", "pricing", pricing, None),
        ("/about", "about_page", about_page, None),
        ("/privacy", "privacy_policy", privacy_policy, None),
        ("/terms", "terms_of_service", terms_of_service, None),
        ("/maintenance", "maintenance", maintenance, None),
        ("/help", "help_centre", help_centre, None),
        ("/contact", "contact", contact, ["GET", "POST"]),
    ]

    for rule, endpoint, view_func, methods in routes:
        app.add_url_rule(rule, endpoint, view_func, methods=methods)
