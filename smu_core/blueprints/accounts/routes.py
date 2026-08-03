from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from smu_core.extensions import db
from smu_core.models import ConnectedAccount


accounts_bp = Blueprint("accounts", __name__)


@login_required
def connected_accounts():
    accounts = ConnectedAccount.query.filter_by(
        user_id=current_user.id
    ).first()

    if not accounts:
        accounts = ConnectedAccount(
            user_id=current_user.id
        )
        db.session.add(accounts)
        db.session.commit()

    if request.method == "POST":
        accounts.instagram_connected = (
            request.form.get("instagram_connected") == "on"
        )
        accounts.facebook_connected = (
            request.form.get("facebook_connected") == "on"
        )
        accounts.linkedin_connected = (
            request.form.get("linkedin_connected") == "on"
        )
        accounts.pinterest_connected = (
            request.form.get("pinterest_connected") == "on"
        )
        accounts.reddit_connected = (
            request.form.get("reddit_connected") == "on"
        )
        accounts.x_connected = (
            request.form.get("x_connected") == "on"
        )

        accounts.make_webhook_single = request.form.get(
            "make_webhook_single",
            "",
        ).strip()

        accounts.make_webhook_carousel = request.form.get(
            "make_webhook_carousel",
            "",
        ).strip()

        db.session.commit()

        flash(
            "Connected accounts updated.",
            "success",
        )
        return redirect(
            url_for("connected_accounts")
        )

    enabled_count = sum([
        bool(accounts.instagram_connected),
        bool(accounts.facebook_connected),
        bool(accounts.linkedin_connected),
        bool(accounts.pinterest_connected),
        bool(accounts.reddit_connected),
        bool(accounts.x_connected),
    ])

    webhooks_ready = (
        bool(accounts.make_webhook_single),
        bool(accounts.make_webhook_carousel),
    )

    return render_template(
        "connected_accounts.html",
        accounts=accounts,
        enabled_count=enabled_count,
        webhooks_ready=webhooks_ready,
    )


@accounts_bp.record_once
def register_accounts_routes(state):
    state.app.add_url_rule(
        "/settings/accounts",
        "connected_accounts",
        connected_accounts,
        methods=["GET", "POST"],
    )
