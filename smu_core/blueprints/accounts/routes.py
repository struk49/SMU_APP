import hmac
import secrets

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required

from smu_core.extensions import db
from smu_core.models import ConnectedAccount
from smu_core.services.platforms import linkedin_oauth


accounts_bp = Blueprint("accounts", __name__)


def _get_or_create_current_user_accounts():
    accounts = ConnectedAccount.query.filter_by(
        user_id=current_user.id
    ).first()

    if not accounts:
        accounts = ConnectedAccount(
            user_id=current_user.id
        )
        db.session.add(accounts)
        db.session.commit()

    return accounts


def _clear_linkedin_state(accounts):
    accounts.linkedin_connected = False
    accounts.linkedin_access_token = None
    accounts.linkedin_access_token_expires_at = None
    accounts.linkedin_scopes = None
    accounts.linkedin_member_id = None
    accounts.linkedin_member_urn = None
    accounts.linkedin_display_name = None
    accounts.linkedin_refresh_token = None
    accounts.linkedin_refresh_token_expires_at = None


@login_required
def connected_accounts():
    accounts = _get_or_create_current_user_accounts()

    if request.method == "POST":
        accounts.instagram_connected = (
            request.form.get("instagram_connected") == "on"
        )
        accounts.facebook_connected = (
            request.form.get("facebook_connected") == "on"
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


@login_required
def linkedin_connect():
    client_id = current_app.config.get("LINKEDIN_CLIENT_ID", "")
    client_secret = current_app.config.get("LINKEDIN_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        current_app.logger.warning(
            "LinkedIn OAuth configuration missing: client_id=%s client_secret=%s",
            bool(client_id),
            bool(client_secret),
        )
        flash("LinkedIn connection is not configured.", "warning")
        return redirect(url_for("connected_accounts"))

    state = secrets.token_urlsafe(32)
    session["linkedin_oauth_state"] = state
    redirect_uri = _linkedin_redirect_uri()

    authorization_url = linkedin_oauth.build_authorization_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
    )
    return redirect(authorization_url)


@login_required
def linkedin_callback():
    expected_state = session.pop("linkedin_oauth_state", None)
    returned_state = request.args.get("state", "")

    if not expected_state or not returned_state or not hmac.compare_digest(
        expected_state,
        returned_state,
    ):
        flash("LinkedIn connection could not be verified. Please try again.", "danger")
        return redirect(url_for("connected_accounts"))

    if request.args.get("error"):
        flash("LinkedIn connection was cancelled or denied.", "warning")
        return redirect(url_for("connected_accounts"))

    code = request.args.get("code", "")
    if not code:
        flash("LinkedIn did not return an authorization code.", "danger")
        return redirect(url_for("connected_accounts"))

    try:
        token_data = linkedin_oauth.exchange_code_for_token(
            code=code,
            client_id=current_app.config.get("LINKEDIN_CLIENT_ID", ""),
            client_secret=current_app.config.get("LINKEDIN_CLIENT_SECRET", ""),
            redirect_uri=_linkedin_redirect_uri(),
        )
        identity = linkedin_oauth.fetch_member_identity(token_data.access_token)

        accounts = _get_or_create_current_user_accounts()
        accounts.linkedin_connected = True
        accounts.linkedin_access_token = token_data.access_token
        accounts.linkedin_access_token_expires_at = (
            token_data.access_token_expires_at
        )
        accounts.linkedin_scopes = token_data.scopes
        accounts.linkedin_member_id = identity.member_id
        accounts.linkedin_member_urn = identity.member_urn
        accounts.linkedin_display_name = identity.display_name
        accounts.linkedin_refresh_token = token_data.refresh_token
        accounts.linkedin_refresh_token_expires_at = (
            token_data.refresh_token_expires_at
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.warning(
            "LinkedIn OAuth callback failed: stage=%s type=%s",
            getattr(exc, "stage", "unknown"),
            exc.__class__.__name__,
        )
        flash("LinkedIn could not be connected. Please try again.", "danger")
        return redirect(url_for("connected_accounts"))

    flash("LinkedIn connected successfully.", "success")
    return redirect(url_for("connected_accounts"))


@login_required
def linkedin_disconnect():
    accounts = _get_or_create_current_user_accounts()
    _clear_linkedin_state(accounts)
    db.session.commit()

    flash("LinkedIn disconnected.", "success")
    return redirect(url_for("connected_accounts"))


def _linkedin_redirect_uri():
    configured_uri = current_app.config.get("LINKEDIN_REDIRECT_URI", "")
    if configured_uri:
        return configured_uri
    return url_for("linkedin_callback", _external=True)


@accounts_bp.record_once
def register_accounts_routes(state):
    state.app.add_url_rule(
        "/settings/accounts",
        "connected_accounts",
        connected_accounts,
        methods=["GET", "POST"],
    )
    state.app.add_url_rule(
        "/accounts/linkedin/connect",
        "linkedin_connect",
        linkedin_connect,
        methods=["GET"],
    )
    state.app.add_url_rule(
        "/accounts/linkedin/callback",
        "linkedin_callback",
        linkedin_callback,
        methods=["GET"],
    )
    state.app.add_url_rule(
        "/accounts/linkedin/disconnect",
        "linkedin_disconnect",
        linkedin_disconnect,
        methods=["POST"],
    )
