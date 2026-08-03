from flask import Blueprint, current_app, flash, jsonify, redirect, request, url_for
from flask_login import current_user, login_required

from smu_core.extensions import db
from smu_core.models import Feedback


feedback_bp = Blueprint("feedback", __name__)


def _log_event(event_name, **fields):
    log_event = current_app.extensions.get("smu_log_event")
    if log_event:
        log_event(event_name, **fields)


@login_required
def submit_feedback():
    data = request.get_json(silent=True) or {}
    message = (
        data.get("message")
        or request.form.get("message", "")
    ).strip()
    page_url = (
        data.get("page_url")
        or request.form.get("page_url", "")
    ).strip()

    if not message:
        if request.is_json:
            return jsonify({"error": "Feedback message is required."}), 400

        flash("Please enter feedback before sending.", "danger")
        return redirect(request.referrer or url_for("index"))

    feedback = Feedback(
        user_id=current_user.id,
        message=message,
        page_url=page_url[:500],
    )

    db.session.add(feedback)
    db.session.commit()
    _log_event(
        "feedback_submission",
        feedback_id=feedback.id,
        user_id=current_user.id,
    )

    if request.is_json:
        return jsonify({"success": True})

    flash("Thanks for the feedback.", "success")
    return redirect(request.referrer or url_for("index"))


@feedback_bp.record_once
def register_feedback_routes(state):
    state.app.add_url_rule(
        "/feedback",
        "submit_feedback",
        submit_feedback,
        methods=["POST"],
    )
