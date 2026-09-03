from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from smu_core.extensions import db
from smu_core.models import Post
from smu_core.services.access import has_product_access


dashboard_bp = Blueprint("dashboard", __name__)


def _dashboard_helper(name):
    helpers = current_app.extensions.get("smu_dashboard_helpers", {})
    helper = helpers.get(name)

    if not helper:
        raise RuntimeError(f"Dashboard helper is not available: {name}")

    return helper


def index():
    if not current_user.is_authenticated:
        return render_template("landing.html")

    user = current_user._get_current_object()
    if not has_product_access(user):
        flash("An active SMU subscription is required to use this feature.", "warning")
        return redirect(url_for("pricing"))

    status_filter = request.args.get("status", "all")
    type_filter = request.args.get("type", "all")
    platform_filter = request.args.get("platform", "all")
    search_query = request.args.get("q", "").strip()

    query = Post.query.filter_by(user_id=current_user.id)

    if status_filter != "all":
        query = query.filter(Post.status == status_filter)

    if type_filter != "all":
        query = query.filter(Post.post_type == type_filter)

    if platform_filter != "all":
        query = query.filter(Post.platforms.ilike(f"%{platform_filter}%"))

    if search_query:
        search_term = f"%{search_query}%"
        query = query.filter(
            db.or_(
                Post.caption.ilike(search_term),
                Post.prompt.ilike(search_term),
                Post.platforms.ilike(search_term),
                Post.status.ilike(search_term),
                Post.post_type.ilike(search_term),
            )
        )

    posts = query.order_by(
        Post.created_at.desc(),
        Post.is_cover.desc(),
        Post.sort_order.asc(),
        Post.id.asc(),
    ).all()

    stats = {
        "total": Post.query.filter_by(user_id=current_user.id).count(),
        "drafts": Post.query.filter_by(user_id=current_user.id, status="draft").count(),
        "scheduled": Post.query.filter_by(
            user_id=current_user.id, status="scheduled"
        ).count(),
        "sent": Post.query.filter_by(
            user_id=current_user.id, status="sent_to_make"
        ).count(),
        "carousels": Post.query.filter_by(
            user_id=current_user.id, post_type="carousel"
        ).count(),
    }

    return render_template(
        "index.html",
        posts=posts,
        status_filter=status_filter,
        type_filter=type_filter,
        platform_filter=platform_filter,
        search_query=search_query,
        stats=stats,
        onboarding=_dashboard_helper("build_onboarding_progress")(current_user.id),
        connected_platforms=_dashboard_helper("build_connected_platform_cards")(
            current_user.id
        ),
        usage_summary=_dashboard_helper("get_usage_summary")(user),
    )


@dashboard_bp.record_once
def register_routes(state):
    state.app.add_url_rule("/", "index", index)
