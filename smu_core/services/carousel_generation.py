"""Background carousel image generation helpers."""

import json
import logging


logger = logging.getLogger(__name__)

CAROUSEL_GENERATION_BATCH_SIZE = 5
OVERLAY_PAYLOAD_PREFIX = "SMU_OVERLAY_V1:"
OVERLAY_PAYLOAD_VERSION = 1
MAX_OVERLAY_PAYLOAD_BYTES = 8192
MAX_BACKGROUND_PROMPT_LENGTH = 6000
MAX_OVERLAY_TITLE_LENGTH = 180
MAX_OVERLAY_BODY_LENGTH = 600
MAX_OVERLAY_CTA_LENGTH = 120
MAX_OVERLAY_BRAND_LENGTH = 120


class OverlayPayloadError(ValueError):
    """A safe, categorical compatibility-payload failure."""

    def __init__(self, reason="invalid_overlay_payload"):
        self.reason = reason
        super().__init__(reason)


def _valid_optional_overlay_text(value, max_length):
    return value is None or (isinstance(value, str) and value and len(value) <= max_length)


def build_content_pack_overlay_prompt(
    background_prompt,
    title,
    *,
    body=None,
    cta=None,
    brand=None,
):
    if (
        not isinstance(background_prompt, str)
        or not background_prompt.strip()
        or len(background_prompt) > MAX_BACKGROUND_PROMPT_LENGTH
        or not isinstance(title, str)
        or not title
        or len(title) > MAX_OVERLAY_TITLE_LENGTH
        or not _valid_optional_overlay_text(body, MAX_OVERLAY_BODY_LENGTH)
        or not _valid_optional_overlay_text(cta, MAX_OVERLAY_CTA_LENGTH)
        or not _valid_optional_overlay_text(brand, MAX_OVERLAY_BRAND_LENGTH)
    ):
        raise OverlayPayloadError()

    payload = {
        "version": OVERLAY_PAYLOAD_VERSION,
        "kind": "content_pack_carousel",
        "background_prompt": background_prompt,
        "overlay": {
            "title": title,
            "body": body,
            "cta": cta,
            "brand": brand,
        },
    }
    try:
        encoded = OVERLAY_PAYLOAD_PREFIX + json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        encoded_size = len(encoded.encode("utf-8"))
    except (TypeError, UnicodeError, ValueError) as exc:
        raise OverlayPayloadError() from exc
    if encoded_size > MAX_OVERLAY_PAYLOAD_BYTES:
        raise OverlayPayloadError()
    return encoded


def parse_overlay_prompt(prompt):
    if not isinstance(prompt, str) or not prompt.startswith(OVERLAY_PAYLOAD_PREFIX):
        return None
    try:
        prompt_size = len(prompt.encode("utf-8"))
    except UnicodeError as exc:
        raise OverlayPayloadError() from exc
    if prompt_size > MAX_OVERLAY_PAYLOAD_BYTES:
        raise OverlayPayloadError()

    try:
        payload = json.loads(prompt[len(OVERLAY_PAYLOAD_PREFIX):])
    except (TypeError, ValueError) as exc:
        raise OverlayPayloadError() from exc

    expected_keys = {"version", "kind", "background_prompt", "overlay"}
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise OverlayPayloadError()
    if payload["version"] != OVERLAY_PAYLOAD_VERSION:
        raise OverlayPayloadError()
    if payload["kind"] != "content_pack_carousel":
        raise OverlayPayloadError()

    background_prompt = payload["background_prompt"]
    overlay = payload["overlay"]
    if (
        not isinstance(background_prompt, str)
        or not background_prompt.strip()
        or len(background_prompt) > MAX_BACKGROUND_PROMPT_LENGTH
        or not isinstance(overlay, dict)
        or set(overlay) != {"title", "body", "cta", "brand"}
        or not isinstance(overlay["title"], str)
        or not overlay["title"]
        or len(overlay["title"]) > MAX_OVERLAY_TITLE_LENGTH
        or not _valid_optional_overlay_text(
            overlay["body"], MAX_OVERLAY_BODY_LENGTH
        )
        or not _valid_optional_overlay_text(overlay["cta"], MAX_OVERLAY_CTA_LENGTH)
        or not _valid_optional_overlay_text(
            overlay["brand"], MAX_OVERLAY_BRAND_LENGTH
        )
    ):
        raise OverlayPayloadError()
    return payload


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
            overlay_payload = parse_overlay_prompt(pending_post.prompt)
            if overlay_payload is None:
                image_url = image_generator(pending_post.prompt)
            else:
                image_url = image_generator(
                    overlay_payload["background_prompt"],
                    overlay=overlay_payload["overlay"],
                )
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
