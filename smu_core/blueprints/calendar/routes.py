import re
import uuid
from datetime import datetime

import pytz
from flask import Blueprint, current_app, jsonify, render_template, request, session, url_for
from flask_login import current_user, login_required

from smu_core.extensions import db
from smu_core.models import Post


calendar_bp = Blueprint("calendar", __name__)

UK_TIMEZONE = pytz.timezone("Europe/London")
UTC_TIMEZONE = pytz.UTC

CALENDAR_STATUS_COLORS = {
    "draft": "#6c757d",
    "scheduled": "#0d6efd",
    "published": "#198754",
    "failed": "#dc3545",
}


def _calendar_helper(name):
    helpers = current_app.extensions.get("smu_calendar_helpers", {})
    helper = helpers.get(name)

    if not helper:
        raise RuntimeError(f"Calendar helper is not available: {name}")

    return helper


@login_required
def calendar_view():
    session["calendar_viewed"] = True
    return render_template("calendar.html")


def parse_calendar_range_datetime(value):
    if not value:
        raise ValueError("Missing date")

    normalized = value.strip().replace("Z", "+00:00")
    normalized = re.sub(r" ([0-9]{2}:[0-9]{2})$", r"+\1", normalized)
    parsed = datetime.fromisoformat(normalized)

    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC_TIMEZONE).replace(tzinfo=None)

    local_aware = UK_TIMEZONE.localize(parsed, is_dst=None)
    return local_aware.astimezone(UTC_TIMEZONE).replace(tzinfo=None)


def calendar_status_key(status):
    normalized_status = (status or "").strip().lower()

    if normalized_status in {"sent_to_make", "published"}:
        return "published"

    if "failed" in normalized_status:
        return "failed"

    if normalized_status == "scheduled":
        return "scheduled"

    return "draft"


def calendar_status_label(status):
    return calendar_status_key(status).title()


def calendar_post_title(post):
    caption = (post.caption or "").strip()

    if not caption:
        return "Untitled post"

    first_line = caption.splitlines()[0].strip()
    return first_line[:80] if first_line else "Untitled post"


def calendar_posts_for_range(start_utc, end_utc):
    posts = (
        Post.query.filter(
            Post.user_id == current_user.id,
            Post.scheduled_time.isnot(None),
            Post.scheduled_time >= start_utc,
            Post.scheduled_time < end_utc,
        )
        .order_by(
            Post.scheduled_time.asc(),
            Post.is_cover.desc(),
            Post.sort_order.asc(),
            Post.id.asc(),
        )
        .all()
    )

    calendar_posts = []
    seen_groups = set()

    for post in posts:
        if post.group_id:
            if post.group_id in seen_groups:
                continue

            seen_groups.add(post.group_id)

        calendar_posts.append(post)

    return calendar_posts


def filter_calendar_posts(posts, platform_filter=None, status_filter=None):
    platform_filter = (platform_filter or "all").strip().lower()
    status_filter = (status_filter or "all").strip().lower()
    filtered_posts = []

    for post in posts:
        platforms = [
            platform.lower()
            for platform in _calendar_helper("parse_platforms")(post.platforms)
        ]

        if platform_filter != "all" and platform_filter not in platforms:
            continue

        if status_filter != "all" and calendar_status_key(post.status) != status_filter:
            continue

        filtered_posts.append(post)

    return filtered_posts


def build_calendar_summary(posts):
    summary = {
        "scheduled": 0,
        "published": 0,
        "draft": 0,
        "failed": 0,
    }

    for post in posts:
        summary[calendar_status_key(post.status)] += 1

    return summary


def reschedule_calendar_post(post, new_date):
    if not post.scheduled_time:
        raise ValueError("Post does not have a scheduled time.")

    local_start = _calendar_helper("convert_utc_to_uk")(post.scheduled_time)
    local_rescheduled = UK_TIMEZONE.localize(
        datetime.combine(new_date, local_start.time().replace(tzinfo=None)),
        is_dst=None,
    )
    utc_rescheduled = local_rescheduled.astimezone(UTC_TIMEZONE).replace(
        tzinfo=None
    )

    if post.group_id:
        group_posts = _calendar_helper("get_ordered_carousel_posts")(
            post.group_id,
            user_id=current_user.id,
        )

        if not group_posts:
            raise ValueError("Carousel not found.")

        for group_post in group_posts:
            group_post.scheduled_time = utc_rescheduled

        return group_posts[0]

    post.scheduled_time = utc_rescheduled
    return post


def duplicate_calendar_post_as_draft(post):
    if post.group_id:
        original_posts = _calendar_helper("get_ordered_carousel_posts")(
            post.group_id,
            user_id=current_user.id,
        )

        if not original_posts:
            raise ValueError("Carousel not found.")

        new_group_id = str(uuid.uuid4())
        first_new_post = None

        for original_post in original_posts:
            new_post = Post(
                file_url=original_post.file_url,
                file_type=original_post.file_type,
                prompt=original_post.prompt,
                caption=original_post.caption,
                status="draft",
                platforms=original_post.platforms,
                post_type="carousel",
                group_id=new_group_id,
                sort_order=original_post.sort_order,
                is_cover=original_post.is_cover,
                scheduled_time=None,
                sent_at=None,
                user_id=current_user.id,
                brand_score=original_post.brand_score,
                brand_feedback=original_post.brand_feedback,
            )

            db.session.add(new_post)

            if first_new_post is None:
                first_new_post = new_post

        return first_new_post

    new_post = Post(
        file_url=post.file_url,
        file_type=post.file_type,
        prompt=post.prompt,
        caption=post.caption,
        status="draft",
        platforms=post.platforms,
        post_type="single",
        sort_order=0,
        is_cover=False,
        group_id=None,
        scheduled_time=None,
        sent_at=None,
        user_id=current_user.id,
        brand_score=post.brand_score,
        brand_feedback=post.brand_feedback,
    )

    db.session.add(new_post)
    return new_post


def build_calendar_event(post):
    local_start = _calendar_helper("convert_utc_to_uk")(post.scheduled_time)
    platforms = _calendar_helper("parse_platforms")(post.platforms)
    first_platform = platforms[0].title() if platforms else "Post"
    post_type_label = "Carousel" if post.group_id else "Single"
    status_key = calendar_status_key(post.status)
    event_color = CALENDAR_STATUS_COLORS[status_key]

    return {
        "id": post.id,
        "title": f"{local_start.strftime('%H:%M')} {first_platform} · {post_type_label}",
        "start": local_start.isoformat(),
        "status": status_key,
        "status_label": calendar_status_label(post.status),
        "post_type": "carousel" if post.group_id else "single",
        "post_type_label": post_type_label,
        "platforms": platforms,
        "platform_label": first_platform,
        "detail_url": url_for("view_post", post_id=post.id),
        "backgroundColor": event_color,
        "borderColor": event_color,
        "textColor": "#ffffff",
        "tooltip": {
            "title": calendar_post_title(post),
            "platform": first_platform,
            "post_type": post_type_label,
            "status": calendar_status_label(post.status),
            "scheduled_time": local_start.strftime("%d %b %Y %H:%M"),
        },
    }


@login_required
def calendar_events():
    try:
        start_utc = parse_calendar_range_datetime(
            request.args.get("start", "")
        )
        end_utc = parse_calendar_range_datetime(
            request.args.get("end", "")
        )
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid start or end date."}), 400

    if end_utc <= start_utc:
        return jsonify({"error": "Invalid start or end date."}), 400

    posts = calendar_posts_for_range(start_utc, end_utc)
    posts = filter_calendar_posts(
        posts,
        platform_filter=request.args.get("platform"),
        status_filter=request.args.get("status"),
    )
    events = [build_calendar_event(post) for post in posts]

    return jsonify(events)


@login_required
def calendar_summary():
    try:
        start_utc = parse_calendar_range_datetime(
            request.args.get("start", "")
        )
        end_utc = parse_calendar_range_datetime(
            request.args.get("end", "")
        )
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid start or end date."}), 400

    if end_utc <= start_utc:
        return jsonify({"error": "Invalid start or end date."}), 400

    posts = calendar_posts_for_range(start_utc, end_utc)
    return jsonify(build_calendar_summary(posts))


@login_required
def calendar_reschedule_event(post_id):
    post = Post.query.filter_by(
        id=post_id,
        user_id=current_user.id,
    ).first()

    if not post:
        return jsonify({"error": "Post not found."}), 404

    data = request.get_json(silent=True) or {}
    new_date_raw = (data.get("date") or "").strip()

    try:
        new_date = datetime.strptime(new_date_raw, "%Y-%m-%d").date()
        updated_post = reschedule_calendar_post(post, new_date)
        db.session.commit()
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Post could not be rescheduled."}), 500

    return jsonify({
        "success": True,
        "event": build_calendar_event(updated_post),
    })


@login_required
def calendar_duplicate_event(post_id):
    post = Post.query.filter_by(
        id=post_id,
        user_id=current_user.id,
    ).first()

    if not post:
        return jsonify({"error": "Post not found."}), 404

    try:
        new_post = duplicate_calendar_post_as_draft(post)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Post could not be duplicated."}), 500

    return jsonify({
        "success": True,
        "post_id": new_post.id,
        "detail_url": url_for("view_post", post_id=new_post.id),
    })


@calendar_bp.record_once
def register_routes(state):
    app = state.app
    app.add_url_rule("/calendar", "calendar_view", calendar_view)
    app.add_url_rule("/calendar/events", "calendar_events", calendar_events)
    app.add_url_rule("/calendar/summary", "calendar_summary", calendar_summary)
    app.add_url_rule(
        "/calendar/events/<int:post_id>/reschedule",
        "calendar_reschedule_event",
        calendar_reschedule_event,
        methods=["POST"],
    )
    app.add_url_rule(
        "/calendar/events/<int:post_id>/duplicate",
        "calendar_duplicate_event",
        calendar_duplicate_event,
        methods=["POST"],
    )
