import re

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from smu_core.extensions import db
from smu_core.models import BetaApplication, Feedback


beta_bp = Blueprint("beta", __name__)


def is_valid_email(email):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email or ""))


def field_too_long(value, max_length):
    return len(value or "") > max_length


def _log_event(event_name, **fields):
    log_event = current_app.extensions.get("smu_log_event")
    if log_event:
        log_event(event_name, **fields)


def is_current_user_admin():
    admin_emails = current_app.config.get("SMU_ADMIN_EMAILS", set())
    return (
        current_user.is_authenticated
        and current_user.email.lower() in admin_emails
    )


def beta_apply():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        primary_platform = request.form.get("primary_platform", "").strip()
        posting_frequency = request.form.get("posting_frequency", "").strip()
        challenge = request.form.get("challenge", "").strip()
        consent = request.form.get("consent") == "on"

        errors = []
        if not name:
            errors.append("Name is required.")
        if not is_valid_email(email):
            errors.append("A valid email is required.")
        if not primary_platform:
            errors.append("Primary platform is required.")
        if not posting_frequency:
            errors.append("Posting frequency is required.")
        if not challenge:
            errors.append("Tell us your biggest content challenge.")
        if not consent:
            errors.append("Consent is required for beta-related emails.")
        if field_too_long(name, 120) or field_too_long(email, 150):
            errors.append("Name or email is too long.")
        if field_too_long(primary_platform, 50) or field_too_long(posting_frequency, 80):
            errors.append("Platform or posting frequency is too long.")
        if field_too_long(challenge, 1000):
            errors.append("Challenge must be 1000 characters or fewer.")
        if BetaApplication.query.filter_by(email=email).first():
            errors.append("A beta application already exists for that email.")

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template("beta_apply.html"), 400

        application = BetaApplication(
            name=name,
            email=email,
            primary_platform=primary_platform,
            posting_frequency=posting_frequency,
            challenge=challenge,
            consent=consent,
        )
        db.session.add(application)
        db.session.commit()
        _log_event(
            "beta_application_submission",
            beta_application_id=application.id,
            primary_platform=primary_platform,
        )

        flash("Thanks. Your private beta application has been received.", "success")
        return redirect(url_for("beta_apply"))

    return render_template("beta_apply.html")


@login_required
def admin_beta():
    if not is_current_user_admin():
        abort(404)

    applications = BetaApplication.query.order_by(
        BetaApplication.created_at.desc()
    ).all()
    feedback_items = Feedback.query.order_by(
        Feedback.created_at.desc()
    ).all()

    return render_template(
        "admin_beta.html",
        applications=applications,
        feedback_items=feedback_items,
    )


@beta_bp.record_once
def register_beta_routes(state):
    app = state.app
    routes = [
        ("/beta/apply", "beta_apply", beta_apply, ["GET", "POST"]),
        ("/admin/beta", "admin_beta", admin_beta, ["GET"]),
    ]

    for rule, endpoint, view_func, methods in routes:
        app.add_url_rule(rule, endpoint, view_func, methods=methods)
