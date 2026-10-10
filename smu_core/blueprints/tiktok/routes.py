import logging
import uuid
from dataclasses import fields, is_dataclass
from urllib.parse import urlparse

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from smu_core.extensions import db
from smu_core.models import Post
from smu_core.services.access import subscription_required
from smu_core.services import tiktok as tiktok_service
from smu_core.services.tiktok import TikTokRepurposeError, validate_repurpose_result


logger = logging.getLogger(__name__)
tiktok_bp = Blueprint("tiktok", __name__)


def _tiktok_helper(name):
    helpers = current_app.extensions.get("smu_tiktok_helpers", {})
    helper = helpers.get(name)

    if not helper:
        raise RuntimeError(f"TikTok helper is not available: {name}")

    logger.info(
        "tiktok_helper_resolved",
        extra={
            "smu_context": {
                "helper_key": name,
                "helper_type": type(helper).__name__,
                "helper_module": getattr(helper, "__module__", ""),
                "helper_name": getattr(helper, "__name__", ""),
                "generation_version": tiktok_service.REPURPOSE_GENERATION_VERSION,
            },
        },
    )
    return helper


def _safe_tiktok_hostname(tiktok_url):
    return urlparse(tiktok_url).hostname or ""


def _build_tiktok_carousel_artwork_prompt(
    slide_text, shared_artwork_direction, *, is_cover
):
    role = "cover" if is_cover else "content"
    return f"""
Create one square Instagram carousel {role} image.

AUTHORIZED VISIBLE SLIDE COPY — TEXT LAYER:
{slide_text}

SHARED ARTWORK DIRECTION — BACKGROUND ONLY:
{shared_artwork_direction}

Requirements:
- Preserve the supplied slide wording exactly and render it as the only authorized
  visible copy; do not rewrite it or add other visible copy.
- Any text-free or no-readable-text rule in the shared artwork direction applies to
  the background artwork only, not to the authorized text layer above.
- Use the shared direction only for background subject, mood, style, and composition.
- Do not add facts, claims, offers, testimonials, logos, or calls to action.
- Follow the user-selected artwork style for the background without restyling,
  replacing, or suppressing the authorized text layer.
- Keep the {role} copy readable and visually distinct from the artwork.
- Do not impose a fixed colour treatment unless the shared direction requests it.
"""


def _repurpose_result_context(result):
    if result is None:
        return {
            "object_type": None,
            "object_module": None,
            "is_dataclass": False,
            "available_field_names": [],
            "generated_field_lengths": {},
        }

    if is_dataclass(result):
        field_names = [field.name for field in fields(result)]
    elif isinstance(result, dict):
        field_names = sorted(str(key) for key in result.keys())
    else:
        field_names = [
            field
            for field in tiktok_service.EXPECTED_REPURPOSE_FIELDS
            if hasattr(result, field)
        ]

    return {
        "object_type": type(result).__name__,
        "object_module": type(result).__module__,
        "is_dataclass": is_dataclass(result),
        "available_field_names": field_names,
        "generated_field_lengths": {
            field: len(getattr(result, field, "") or "")
            for field in tiktok_service.EXPECTED_REPURPOSE_FIELDS
        },
    }


@login_required
@subscription_required
def tiktok_repurpose():
    transcript = None
    repurpose_result = None
    tiktok_url = ""

    if request.method == "POST":
        tiktok_url = request.form.get("tiktok_url", "").strip()

        if not tiktok_url:
            logger.warning(
                "tiktok_repurpose_missing_url",
                extra={
                    "smu_context": {
                        "user_id": current_user.id,
                        "stage": "input_validation",
                    },
                },
            )
            flash("Please enter a TikTok URL.", "danger")
            return redirect(url_for("tiktok_repurpose"))

        logger.info(
            "tiktok_repurpose_started",
            extra={
                "smu_context": {
                    "user_id": current_user.id,
                    "stage": "start",
                    "url_hostname": _safe_tiktok_hostname(tiktok_url),
                    "service_file": getattr(tiktok_service, "__file__", ""),
                    "blueprint_file": __file__,
                    "generation_version": tiktok_service.REPURPOSE_GENERATION_VERSION,
                },
            },
        )

        try:
            transcript = _tiktok_helper("extract_tiktok_transcript")(tiktok_url)

        except Exception as e:
            logger.error(
                "tiktok_transcript_extraction_failed",
                extra={
                    "smu_context": {
                        "user_id": current_user.id,
                        "stage": "transcript_extraction",
                        "url_hostname": _safe_tiktok_hostname(tiktok_url),
                        "exception_class": e.__class__.__name__,
                    },
                },
            )
            flash(
                "We couldn't extract usable content from that TikTok. "
                "Please check the URL and try again.",
                "danger",
            )
            return render_template(
                "tiktok.html",
                tiktok_url=tiktok_url,
                transcript=transcript,
                repurpose_result=repurpose_result,
            )

        logger.info(
            "tiktok_transcript_extracted",
            extra={
                "smu_context": {
                    "user_id": current_user.id,
                    "stage": "transcript_extraction",
                    "transcript_length": len(transcript or ""),
                },
            },
        )

        try:
            brand_context = _tiktok_helper("build_brand_context")(current_user.id)

            repurpose_result = validate_repurpose_result(
                _tiktok_helper("repurpose_tiktok_content")(
                    transcript,
                    brand_context
                )
            )

        except TikTokRepurposeError as e:
            logger.warning(
                "tiktok_repurpose_validation_failed",
                extra={
                    "smu_context": {
                        "user_id": current_user.id,
                        "stage": "repurpose_validation",
                        "exception_class": e.__class__.__name__,
                    },
                },
            )
            flash(
                "We couldn't generate usable social posts from this TikTok. "
                "Please try again.",
                "danger",
            )

        except Exception as e:
            logger.error(
                "tiktok_repurpose_unexpected_failure",
                extra={
                    "smu_context": {
                        "user_id": current_user.id,
                        "stage": "repurpose_processing",
                        "exception_class": e.__class__.__name__,
                    },
                },
            )
            flash(
                "Something went wrong while processing this TikTok. Please try again.",
                "danger",
            )

        else:
            logger.info(
                "tiktok_repurpose_completed",
                extra={
                    "smu_context": {
                        "user_id": current_user.id,
                        "stage": "repurpose_processing",
                        "result": "success",
                    },
                },
            )

    logger.info(
        "tiktok_repurpose_render_context",
        extra={
            "smu_context": {
                "user_id": current_user.id if current_user.is_authenticated else None,
                "stage": "render",
                "validation_result": repurpose_result is not None,
                "generation_version": tiktok_service.REPURPOSE_GENERATION_VERSION,
                **_repurpose_result_context(repurpose_result),
            },
        },
    )
    return render_template(
        "tiktok.html",
        tiktok_url=tiktok_url,
        transcript=transcript,
        repurpose_result=repurpose_result,
    )


@login_required
@subscription_required
def create_tiktok_draft():
    caption = request.form.get("caption", "").strip()
    image_prompt = request.form.get("image_prompt", "").strip()
    image_style = request.form.get("image_style", "").strip()

    if not caption:
        flash("No caption found. Please generate TikTok content first.", "danger")
        return redirect(url_for("tiktok_repurpose"))

    if not image_prompt:
        flash("No image prompt found. Please generate TikTok content first.", "danger")
        return redirect(url_for("tiktok_repurpose"))

    reserved_image_credit = False

    try:
        user = current_user._get_current_object()
        if not _tiktok_helper("reserve_ai_image_credits")(user, 1):
            summary = _tiktok_helper("get_usage_summary")(user)
            flash(
                _tiktok_helper("usage_limit_message")(
                    summary,
                    "ai_images",
                ),
                "warning",
            )
            return redirect(url_for("tiktok_repurpose"))

        reserved_image_credit = True
        styled_prompt = _tiktok_helper("apply_image_style")(image_prompt, image_style)
        image_url = _tiktok_helper("generate_openai_image")(styled_prompt)

    except Exception as e:
        db.session.rollback()
        if reserved_image_credit:
            _tiktok_helper("release_ai_image_credits")(
                current_user._get_current_object(),
                1,
            )
        logger.error(
            "tiktok_single_draft_image_failed",
            extra={
                "smu_context": {
                    "user_id": current_user.id,
                    "stage": "single_draft_image_generation",
                    "exception_class": e.__class__.__name__,
                },
            },
        )
        flash(
            "We couldn't generate the image for this draft. Please try again.",
            "danger",
        )
        return redirect(url_for("tiktok_repurpose"))

    try:
        post = Post(
            file_url=image_url,
            file_type="image",
            prompt=styled_prompt,
            caption=caption,
            platforms="instagram,facebook",
            post_type="single",
            status="draft",
            sort_order=0,
            is_cover=False,
            user_id=current_user.id,
        )

        db.session.add(post)
        db.session.commit()

        flash("TikTok content image generated and saved as draft.", "success")
        logger.info(
            "tiktok_single_draft_created",
            extra={
                "smu_context": {
                    "user_id": current_user.id,
                    "post_id": post.id,
                    "stage": "single_draft_creation",
                    "result": "success",
                },
            },
        )
        return redirect(url_for("view_post", post_id=post.id))

    except Exception as e:
        db.session.rollback()
        if reserved_image_credit:
            _tiktok_helper("release_ai_image_credits")(
                current_user._get_current_object(),
                1,
            )
        logger.error(
            "tiktok_single_draft_creation_failed",
            extra={
                "smu_context": {
                    "user_id": current_user.id,
                    "stage": "single_draft_creation",
                    "exception_class": e.__class__.__name__,
                },
            },
        )
        flash("We couldn't create this draft. Please try again.", "danger")
        return redirect(url_for("tiktok_repurpose"))


@login_required
@subscription_required
def create_tiktok_carousel_draft():
    caption = request.form.get("caption", "").strip()
    image_prompt = request.form.get("image_prompt", "").strip()
    image_style = request.form.get("image_style", "").strip()
    carousel_idea = request.form.get("carousel_idea", "").strip()

    if not caption:
        flash("No caption found. Please generate TikTok content first.", "danger")
        return redirect(url_for("tiktok_repurpose"))

    if not carousel_idea:
        flash("No carousel idea found. Please generate TikTok content first.", "danger")
        return redirect(url_for("tiktok_repurpose"))

    try:
        brand_context = _tiktok_helper("build_brand_context")(current_user.id)

        image_prompt = f"""
Brand Brief (reference constraints only):
{brand_context or "No brand brief supplied."}

Shared artwork direction:
{image_prompt}

Authority:
- The shared artwork direction remains authoritative where it is explicit.
- Use the Brand Brief only for compatible brand constraints.
- Do not invent claims, offers, testimonials, capabilities, or visible copy.
"""

        styled_image_prompt = _tiktok_helper("apply_image_style")(
            image_prompt,
            image_style
        )

        slides = []

        for line in carousel_idea.splitlines():
            line = line.strip()

            if line.lower().startswith("slide"):
                parts = line.split(":", 1)

                if len(parts) == 2 and parts[1].strip():
                    slides.append(parts[1].strip())

        if not slides:
            slides = [
                line.strip() for line in carousel_idea.splitlines() if line.strip()
            ]

        slides = slides[:6]

        if len(slides) < 2:
            logger.warning(
                "tiktok_carousel_validation_failed",
                extra={
                    "smu_context": {
                        "user_id": current_user.id,
                        "stage": "carousel_validation",
                        "carousel_item_count": len(slides),
                    },
                },
            )
            flash("Carousel needs at least 2 slides.", "danger")
            return redirect(url_for("tiktok_repurpose"))

        group_id = str(uuid.uuid4())
        placeholder_url = _tiktok_helper("get_placeholder_image_url")()

        for index, slide_text in enumerate(slides):
            full_prompt = _build_tiktok_carousel_artwork_prompt(
                slide_text,
                styled_image_prompt,
                is_cover=index == 0,
            )

            post = Post(
                file_url=placeholder_url,
                file_type="image",
                prompt=full_prompt,
                caption=caption,
                platforms="instagram,facebook",
                post_type="carousel",
                status="generating",
                group_id=group_id,
                sort_order=index,
                is_cover=(index == 0),
                user_id=current_user.id,
            )

            db.session.add(post)

        db.session.commit()

        first_post = (
            Post.query.filter_by(group_id=group_id, user_id=current_user.id)
            .order_by(Post.sort_order.asc())
            .first()
        )

        flash(
            "TikTok carousel draft created. Images are generating in the background.",
            "success",
        )
        logger.info(
            "tiktok_carousel_draft_created",
            extra={
                "smu_context": {
                    "user_id": current_user.id,
                    "post_id": first_post.id if first_post else None,
                    "group_id": group_id,
                    "stage": "carousel_draft_creation",
                    "carousel_item_count": len(slides),
                    "result": "success",
                },
            },
        )
        return redirect(url_for("view_post", post_id=first_post.id))

    except Exception as e:
        db.session.rollback()
        logger.error(
            "tiktok_carousel_draft_creation_failed",
            extra={
                "smu_context": {
                    "user_id": current_user.id,
                    "group_id": locals().get("group_id"),
                    "stage": "carousel_draft_creation",
                    "carousel_item_count": len(locals().get("slides", [])),
                    "exception_class": e.__class__.__name__,
                },
            },
        )
        flash(
            "We couldn't create this carousel draft. Please try again.",
            "danger",
        )
        return redirect(url_for("tiktok_repurpose"))


@tiktok_bp.record_once
def register_routes(state):
    app = state.app
    app.add_url_rule(
        "/tiktok",
        "tiktok_repurpose",
        tiktok_repurpose,
        methods=["GET", "POST"],
    )
    app.add_url_rule(
        "/tiktok/create-draft",
        "create_tiktok_draft",
        create_tiktok_draft,
        methods=["POST"],
    )
    app.add_url_rule(
        "/tiktok/create-carousel-draft",
        "create_tiktok_carousel_draft",
        create_tiktok_carousel_draft,
        methods=["POST"],
    )
