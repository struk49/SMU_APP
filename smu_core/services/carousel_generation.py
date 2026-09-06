"""Background carousel image generation helpers."""

import logging


logger = logging.getLogger(__name__)

CAROUSEL_GENERATION_BATCH_SIZE = 5


def _mark_generation_failed(post_model, db_session, post_id):
    post = db_session.get(post_model, post_id)

    if not post:
        return False

    post.status = "generation_failed"
    db_session.commit()
    return True


def generate_pending_carousel_images(
    *,
    post_model,
    db_session,
    image_generator,
    reserve_image_credits=None,
    release_image_credits=None,
    batch_size=CAROUSEL_GENERATION_BATCH_SIZE,
):
    pending_posts = (
        post_model.query.filter_by(status="generating", file_type="image")
        .order_by(
            post_model.created_at.asc(),
            post_model.group_id.asc(),
            post_model.sort_order.asc(),
            post_model.id.asc(),
        )
        .limit(batch_size)
        .all()
    )

    selected_count = len(pending_posts)
    logger.info(
        "carousel_generation_batch_started",
        extra={
            "smu_context": {
                "stage": "carousel_generation_batch",
                "selected_count": selected_count,
                "batch_size": batch_size,
            },
        },
    )

    processed_count = 0
    succeeded_count = 0
    failed_count = 0

    for pending_post in pending_posts:
        post_id = pending_post.id
        group_id = pending_post.group_id
        processed_count += 1
        reserved_credit = False

        try:
            if reserve_image_credits and not reserve_image_credits(pending_post, 1):
                pending_post.status = "generation_failed"
                db_session.commit()
                failed_count += 1
                logger.warning(
                    "carousel_generation_row_credit_exhausted",
                    extra={
                        "smu_context": {
                            "stage": "carousel_generation_row",
                            "result": "credit_exhausted",
                            "post_id": post_id,
                            "group_id": group_id,
                        },
                    },
                )
                continue

            reserved_credit = bool(reserve_image_credits)
            image_url = image_generator(pending_post.prompt)
            pending_post.file_url = image_url
            pending_post.status = "draft"
            db_session.commit()
            succeeded_count += 1

            logger.info(
                "carousel_generation_row_succeeded",
                extra={
                    "smu_context": {
                        "stage": "carousel_generation_row",
                        "result": "success",
                        "post_id": post_id,
                        "group_id": group_id,
                    },
                },
            )

        except Exception as exc:
            db_session.rollback()
            if reserved_credit and release_image_credits:
                release_image_credits(pending_post, 1)
            failed_count += 1
            logger.error(
                "carousel_generation_row_failed post_id=%s error_type=%s",
                post_id,
                exc.__class__.__name__,
                extra={
                    "smu_context": {
                        "stage": "carousel_generation_row",
                        "result": "failed",
                        "post_id": post_id,
                        "group_id": group_id,
                        "exception_class": exc.__class__.__name__,
                    },
                },
            )

            try:
                _mark_generation_failed(post_model, db_session, post_id)
            except Exception as mark_exc:
                db_session.rollback()
                logger.error(
                    "carousel_generation_mark_failed_error",
                    extra={
                        "smu_context": {
                            "stage": "carousel_generation_mark_failed",
                            "result": "failed",
                            "post_id": post_id,
                            "group_id": group_id,
                            "exception_class": mark_exc.__class__.__name__,
                        },
                    },
                )

    logger.info(
        "carousel_generation_batch_finished",
        extra={
            "smu_context": {
                "stage": "carousel_generation_batch",
                "selected_count": selected_count,
                "processed_count": processed_count,
                "succeeded_count": succeeded_count,
                "failed_count": failed_count,
            },
        },
    )

    return {
        "selected_count": selected_count,
        "processed_count": processed_count,
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
    }
