from datetime import datetime

from smu_core.extensions import db
from smu_core.models import Post


def check_scheduled_posts(
    *,
    publish_post,
    log_event,
    now_provider=None,
    post_model=None,
    db_session=None,
):
    if now_provider is None:
        now_provider = datetime.utcnow
    if post_model is None:
        post_model = Post
    if db_session is None:
        db_session = db.session

    try:
        now_utc = now_provider()

        print(
            "Scheduler check:",
            now_utc.strftime("%Y-%m-%d %H:%M:%S"),
            "UTC",
        )

        scheduled_query = post_model.query.filter(
            post_model.scheduled_time.isnot(None),
            post_model.status == "scheduled",
        )
        scheduled_count = scheduled_query.count()
        earliest_scheduled = (
            scheduled_query.order_by(post_model.scheduled_time.asc())
            .with_entities(post_model.scheduled_time)
            .first()
        )

        due_posts = (
            post_model.query.filter(
                post_model.scheduled_time.isnot(None),
                post_model.status == "scheduled",
                post_model.scheduled_time <= now_utc,
            )
            .order_by(
                post_model.scheduled_time.asc(),
                post_model.sort_order.asc(),
                post_model.id.asc(),
            )
            .all()
        )

        print(
            "Scheduler diagnostics:",
            {
                "current_utc_time": now_utc,
                "scheduled_row_count": scheduled_count,
                "earliest_scheduled_time": (
                    earliest_scheduled[0] if earliest_scheduled else None
                ),
                "due_row_count": len(due_posts),
            },
        )

        processed_groups = set()

        for post in due_posts:
            try:
                print(
                    f"Processing scheduled post {post.id}: "
                    f"scheduled={post.scheduled_time}, "
                    f"status={post.status}, "
                    f"user_id={post.user_id}"
                )

                if post.group_id:
                    if post.group_id in processed_groups:
                        continue

                publish_post(post, post.user_id)

                if post.group_id:
                    processed_groups.add(post.group_id)

                    print(
                        f"✅ Sent scheduled carousel "
                        f"{post.group_id}"
                    )
                    log_event(
                        "publishing_success",
                        post_id=post.id,
                        post_type="carousel",
                        user_id=post.user_id,
                        source="scheduler",
                    )

                else:
                    print(
                        f"✅ Sent scheduled post {post.id}"
                    )
                    log_event(
                        "publishing_success",
                        post_id=post.id,
                        post_type="single",
                        user_id=post.user_id,
                        source="scheduler",
                    )

                db_session.commit()

            except Exception as post_error:
                db_session.rollback()
                log_event(
                    "publishing_failure",
                    post_id=post.id,
                    post_type=post.post_type,
                    user_id=post.user_id,
                    source="scheduler",
                    error_type=type(post_error).__name__,
                )

                print(
                    f"❌ Scheduled post {post.id} failed:",
                    repr(post_error),
                )

                try:
                    post.status = "schedule_failed"
                    db_session.commit()
                except Exception as status_error:
                    db_session.rollback()
                    print(
                        f"Failed to mark scheduled post {post.id} "
                        "as failed:",
                        repr(status_error),
                    )

    except Exception as worker_error:
        db_session.rollback()

        print(
            "❌ Scheduled-post worker error:",
            repr(worker_error),
        )
