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
MAX_OVERLAY_EYEBROW_LENGTH = 60
MAX_OVERLAY_EMPHASIS_LENGTH = 120
OVERLAY_EMPHASIS_ROLES = {"primary", "accent", "normal"}
OVERLAY_VISUAL_TREATMENTS = {
    "typography_only",
    "illustration",
    "diagram",
    "process",
    "comparison",
    "visual_focus",
}
OVERLAY_LAYOUT_ROLES = {"cover", "phrase", "info", "cta"}
OVERLAY_LAYOUT_VARIANTS = {
    "hero_left",
    "hero_center",
    "split_left",
    "split_right",
    "editorial_statement",
    "visual_focus",
    "closing",
}
SAFE_GENERATION_FAILURE_REASONS = {
    "font_unavailable",
    "image_dimensions_unsupported",
    "image_too_large",
    "invalid_image",
    "invalid_overlay_payload",
    "invalid_text",
    "invalid_typography_metadata",
    "text_does_not_fit",
    "text_limit_exceeded",
    "title_required",
    "unsupported_layout",
    "unsupported_layout_role",
    "unsupported_layout_variant",
    "unsupported_text_character",
    "unsupported_visual_treatment",
}
DESIGN_STYLE_MARKERS = {
    "Style: realistic social media image": "realistic",
    "Style: viral Instagram business carousel": "viral_carousel",
    "Style: luxury brand aesthetic": "luxury",
    "Style: minimalist modern design": "minimal",
    "Style: professional corporate social media design": "corporate",
    "Style: charming 3D animated film look": "pixar",
}


class OverlayPayloadError(ValueError):
    """A safe, categorical compatibility-payload failure."""

    def __init__(self, reason="invalid_overlay_payload"):
        self.reason = reason
        super().__init__(reason)


def _valid_optional_overlay_text(value, max_length):
    return value is None or (isinstance(value, str) and len(value) <= max_length)


def _normalize_optional_overlay_text(value):
    return None if isinstance(value, str) and value == "" else value


def _valid_typography(typography, title):
    if typography is None:
        return True
    if (
        not isinstance(title, str)
        or not isinstance(typography, dict)
        or set(typography) != {"eyebrow", "emphasis"}
    ):
        return False
    eyebrow = typography["eyebrow"]
    emphasis = typography["emphasis"]
    if not _valid_optional_overlay_text(eyebrow, MAX_OVERLAY_EYEBROW_LENGTH):
        return False
    if emphasis is None:
        return True
    return (
        isinstance(emphasis, dict)
        and set(emphasis) == {"text", "role"}
        and isinstance(emphasis["text"], str)
        and bool(emphasis["text"])
        and len(emphasis["text"]) <= MAX_OVERLAY_EMPHASIS_LENGTH
        and emphasis["text"] in title
        and isinstance(emphasis["role"], str)
        and emphasis["role"] in OVERLAY_EMPHASIS_ROLES
    )


def _design_style_from_background_prompt(background_prompt):
    return next(
        (
            design_style
            for marker, design_style in DESIGN_STYLE_MARKERS.items()
            if marker in background_prompt
        ),
        None,
    )


def build_content_pack_overlay_prompt(
    background_prompt,
    title,
    *,
    body=None,
    cta=None,
    brand=None,
    credits_reserved=False,
    layout_role=None,
    layout_variant=None,
    typography=None,
    visual_treatment=None,
):
    body = _normalize_optional_overlay_text(body)
    cta = _normalize_optional_overlay_text(cta)
    brand = _normalize_optional_overlay_text(brand)
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
        or not isinstance(credits_reserved, bool)
        or not _valid_typography(typography, title)
        or (
            visual_treatment is not None
            and (
                not isinstance(visual_treatment, str)
                or visual_treatment not in OVERLAY_VISUAL_TREATMENTS
            )
        )
        or (
            layout_role is not None
            and (
                not isinstance(layout_role, str)
                or layout_role not in OVERLAY_LAYOUT_ROLES
            )
        )
        or (
            layout_variant is not None
            and (
                layout_role is None
                or
                not isinstance(layout_variant, str)
                or layout_variant not in OVERLAY_LAYOUT_VARIANTS
            )
        )
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
    if credits_reserved:
        payload["credits_reserved"] = True
    if layout_role is not None:
        payload["layout_role"] = layout_role
    if layout_variant is not None:
        payload["layout_variant"] = layout_variant
    if typography is not None:
        payload["typography"] = {
            "eyebrow": _normalize_optional_overlay_text(typography["eyebrow"]),
            "emphasis": typography["emphasis"],
        }
    if visual_treatment is not None:
        payload["visual_treatment"] = visual_treatment
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
    allowed_keys = expected_keys | {
        "credits_reserved",
        "layout_role",
        "layout_variant",
        "typography",
        "visual_treatment",
    }
    if (
        not isinstance(payload, dict)
        or not expected_keys.issubset(payload)
        or not set(payload).issubset(allowed_keys)
        or (
            "credits_reserved" in payload
            and not isinstance(payload["credits_reserved"], bool)
        )
        or (
            "layout_role" in payload
            and (
                not isinstance(payload["layout_role"], str)
                or payload["layout_role"] not in OVERLAY_LAYOUT_ROLES
            )
        )
        or (
            "layout_variant" in payload
            and (
                "layout_role" not in payload
                or
                not isinstance(payload["layout_variant"], str)
                or payload["layout_variant"] not in OVERLAY_LAYOUT_VARIANTS
            )
        )
        or (
            "typography" in payload
            and not _valid_typography(
                payload["typography"],
                (
                    payload.get("overlay", {}).get("title")
                    if isinstance(payload.get("overlay"), dict)
                    else None
                ),
            )
        )
        or (
            "visual_treatment" in payload
            and (
                not isinstance(payload["visual_treatment"], str)
                or payload["visual_treatment"] not in OVERLAY_VISUAL_TREATMENTS
            )
        )
    ):
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
    for key in ("body", "cta", "brand"):
        overlay[key] = _normalize_optional_overlay_text(overlay[key])
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
            overlay_payload = parse_overlay_prompt(pending_post.prompt)
            credits_pre_reserved = bool(
                overlay_payload and overlay_payload.get("credits_reserved")
            )
            if (
                reserve_image_credits
                and not credits_pre_reserved
                and not reserve_image_credits(pending_post, 1)
            ):
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

            reserved_credit = credits_pre_reserved or bool(reserve_image_credits)
            if overlay_payload is None:
                image_url = image_generator(pending_post.prompt)
            else:
                overlay = dict(overlay_payload["overlay"])
                if "layout_role" in overlay_payload:
                    overlay["layout_role"] = overlay_payload["layout_role"]
                    if "layout_variant" in overlay_payload:
                        overlay["layout_variant"] = overlay_payload["layout_variant"]
                    design_style = _design_style_from_background_prompt(
                        overlay_payload["background_prompt"]
                    )
                    if design_style:
                        overlay["design_style"] = design_style
                if "typography" in overlay_payload:
                    overlay.update(overlay_payload["typography"])
                if "visual_treatment" in overlay_payload:
                    overlay["visual_treatment"] = overlay_payload["visual_treatment"]
                image_url = image_generator(
                    overlay_payload["background_prompt"],
                    overlay=overlay,
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
            candidate_reason = getattr(exc, "reason", None)
            failure_reason = (
                candidate_reason
                if candidate_reason in SAFE_GENERATION_FAILURE_REASONS
                else "provider_or_processing_error"
            )
            logger.error(
                "carousel_generation_row_failed post_id=%s error_type=%s failure_reason=%s",
                post_id,
                exc.__class__.__name__,
                failure_reason,
                extra={
                    "smu_context": {
                        "stage": "carousel_generation_row",
                        "result": "failed",
                        "post_id": post_id,
                        "group_id": group_id,
                        "exception_class": exc.__class__.__name__,
                        "failure_reason": failure_reason,
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
