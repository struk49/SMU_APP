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
from smu_core.models import ConnectedAccount, UserUsage
from smu_core.services import zernio
from smu_core.services import usage as usage_service
from smu_core.services.access import has_product_access, subscription_required
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
    user = current_user._get_current_object()
    accounts = _get_or_create_current_user_accounts()

    if request.method == "POST":
        if not has_product_access(user):
            flash("An active SMU subscription is required to use this feature.", "warning")
            return redirect(url_for("pricing"))

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
    account_limit = usage_service.connected_account_limit_status(
        user,
        accounts,
        usage_model=UserUsage,
        db_session=db.session,
    )

    return render_template(
        "connected_accounts.html",
        accounts=accounts,
        enabled_count=enabled_count,
        webhooks_ready=webhooks_ready,
        account_limit=account_limit,
    )


@login_required
@subscription_required
def linkedin_connect():
    user = current_user._get_current_object()
    accounts = _get_or_create_current_user_accounts()
    if not usage_service.can_connect_social_account(
        user,
        accounts,
        platform="linkedin",
        usage_model=UserUsage,
        db_session=db.session,
    ):
        status = usage_service.connected_account_limit_status(
            user,
            accounts,
            usage_model=UserUsage,
            db_session=db.session,
        )
        flash(usage_service.account_limit_message(status["limit"]), "warning")
        return redirect(url_for("connected_accounts"))

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
@subscription_required
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

        user = current_user._get_current_object()
        accounts = _get_or_create_current_user_accounts()
        existing_linkedin_account_id = usage_service.linkedin_account_id(accounts)
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

        status = usage_service.connected_account_limit_status(
            user,
            accounts,
            usage_model=UserUsage,
            db_session=db.session,
        )
        if status["over_limit"] and not existing_linkedin_account_id:
            _clear_linkedin_state(accounts)
            db.session.commit()
            flash(usage_service.account_limit_message(status["limit"]), "warning")
            return redirect(url_for("connected_accounts"))

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
@subscription_required
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


def _zernio_config():
    return {
        "api_key": current_app.config.get("ZERNIO_API_KEY", ""),
        "base_url": current_app.config.get("ZERNIO_BASE_URL", zernio.DEFAULT_BASE_URL),
    }


@login_required
@subscription_required
def zernio_connect(platform):
    platform = (platform or "").strip().lower()
    if platform not in zernio.SUPPORTED_POC_PLATFORMS:
        flash("Direct publishing currently supports Instagram and Facebook only.", "warning")
        return redirect(url_for("connected_accounts"))

    user = current_user._get_current_object()
    accounts = _get_or_create_current_user_accounts()
    if not usage_service.can_connect_social_account(
        user,
        accounts,
        platform=platform,
        usage_model=UserUsage,
        db_session=db.session,
    ):
        status = usage_service.connected_account_limit_status(
            user,
            accounts,
            usage_model=UserUsage,
            db_session=db.session,
        )
        flash(usage_service.account_limit_message(status["limit"]), "warning")
        return redirect(url_for("connected_accounts"))

    config = _zernio_config()

    try:
        profile_id = zernio.ensure_profile_for_user(
            user,
            accounts,
            **config,
        )
        db.session.commit()
        session["zernio_connect_platform"] = platform
        connect_url = zernio.create_connection_url(
            profile_id=profile_id,
            platform=platform,
            redirect_url=url_for("zernio_callback", _external=True),
            **config,
        )
    except zernio.ZernioError as exc:
        db.session.rollback()
        current_app.logger.warning(
            "Zernio connect failed: platform=%s stage=%s status=%s type=%s",
            platform,
            exc.stage,
            exc.status_code,
            exc.__class__.__name__,
        )
        flash("We couldn't start the connection. Please try again.", "warning")
        return redirect(url_for("connected_accounts"))

    return redirect(connect_url)


@login_required
@subscription_required
def zernio_callback():
    user = current_user._get_current_object()
    accounts = _get_or_create_current_user_accounts()
    platform = (
        request.args.get("platform")
        or request.args.get("connected")
        or session.pop("zernio_connect_platform", "")
    )
    platform = platform.strip().lower()

    try:
        existing_account_ids = {
            platform_name: usage_service.zernio_account_id_for_platform(
                accounts,
                platform_name,
            )
            for platform_name in zernio.SUPPORTED_POC_PLATFORMS
        }
        connected = zernio.sync_connected_account_ids(
            accounts,
            platform=platform if platform in zernio.SUPPORTED_POC_PLATFORMS else None,
            **_zernio_config(),
        )
        _enforce_zernio_account_limit_after_sync(user, accounts, existing_account_ids)
        db.session.commit()
    except zernio.ZernioError as exc:
        db.session.rollback()
        current_app.logger.warning(
            "Zernio callback sync failed: stage=%s status=%s type=%s",
            exc.stage,
            exc.status_code,
            exc.__class__.__name__,
        )
        flash("Social account connection could not be confirmed yet.", "warning")
        return redirect(url_for("connected_accounts"))

    if connected:
        flash("Social account connection confirmed.", "success")
    else:
        flash("No connected social account was found yet.", "warning")
    return redirect(url_for("connected_accounts"))


def _enforce_zernio_account_limit_after_sync(user, accounts, existing_account_ids):
    status = usage_service.connected_account_limit_status(
        user,
        accounts,
        usage_model=UserUsage,
        db_session=db.session,
    )
    if status["is_admin"] or not status["over_limit"]:
        return

    accepted_new_accounts = max(status["limit"] - len({
        account_id
        for account_id in existing_account_ids.values()
        if account_id
    }), 0)
    retained_new_accounts = 0

    for platform_name in sorted(zernio.SUPPORTED_POC_PLATFORMS):
        current_account_id = usage_service.zernio_account_id_for_platform(
            accounts,
            platform_name,
        )
        if not current_account_id or existing_account_ids.get(platform_name):
            continue
        if retained_new_accounts < accepted_new_accounts:
            retained_new_accounts += 1
            continue
        field_name = usage_service.ZERNIO_SOCIAL_ACCOUNT_FIELDS[platform_name]
        setattr(accounts, field_name, None)

    flash(
        (
            f"{status['count']} connected - your "
            f"{status['plan'].title()} plan includes {status['limit']}. "
            "Disconnect accounts or upgrade your plan before adding another."
        ),
        "warning",
    )


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
    state.app.add_url_rule(
        "/accounts/zernio/connect/<platform>",
        "zernio_connect",
        zernio_connect,
        methods=["GET"],
    )
    state.app.add_url_rule(
        "/accounts/zernio/callback",
        "zernio_callback",
        zernio_callback,
        methods=["GET"],
    )
