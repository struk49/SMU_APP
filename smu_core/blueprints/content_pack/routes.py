import re
import uuid

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from smu_core.extensions import db
from smu_core.models import Post
from smu_core.services.access import subscription_required
from smu_core.services.carousel_generation import build_content_pack_overlay_prompt


content_pack_bp = Blueprint("content_pack", __name__)

SLIDE_MARKER_RE = re.compile(r"^Slide\s+\d+\s*:\s*(.*)$", re.IGNORECASE)
SLIDE_FIELD_RE = re.compile(
    r"^(Title|Subtitle|Phrase|Translation|Body|CTA)\s*:\s*(.*)$",
    re.IGNORECASE,
)
BODY_FIELD_NAMES = {"subtitle", "translation", "body"}
SLIDE_VISUAL_CONCEPTS = (
    "A clean introductory hero composition with one bold smartphone focal object "
    "and an abstract red-and-white Polish flag colour motif, with strong negative space.",
    "A welcoming conversational scene with two people greeting each other, natural "
    "gestures, and generous negative space; use no speech bubbles.",
    "A close conversational crop focused on expressive faces and hand gestures, with "
    "shallow depth of field and an uncluttered overlay area.",
    "A neatly arranged vocabulary still life of recognisable everyday objects, using "
    "varied scale and an icon-like composition without books or labelled packaging.",
    "A balanced two-sided comparison scene with paired or mirrored matching objects, "
    "linked by abstract shapes only and without written labels.",
    "A closing product-focused composition featuring a smartphone with abstract UI "
    "blocks and icons only, framed as a clear final call-to-action moment.",
)


def _append_slide_value(slide, field, value):
    if not value:
        return
    slide[field] = f"{slide[field]}\n{value}" if slide[field] else value


def _parse_slide_block(lines):
    slide = {"title": None, "body": None, "cta": None, "brand": None}
    active_field = None

    for line in lines:
        if not line.strip():
            continue
        field_match = SLIDE_FIELD_RE.match(line.strip())
        if field_match:
            label, value = field_match.groups()
            label = label.lower()
            active_field = (
                "title"
                if label in {"title", "phrase"}
                else "body"
                if label in BODY_FIELD_NAMES
                else "cta"
            )
            _append_slide_value(slide, active_field, value)
        else:
            _append_slide_value(slide, active_field or "title", line.strip())

    # The renderer requires a title. Preserve copy from a body-only or CTA-only
    # slide by promoting that exact value rather than emitting an invalid payload.
    if not slide["title"]:
        for field in ("body", "cta"):
            if slide[field]:
                slide["title"] = slide[field]
                slide[field] = None
                break

    return slide if any(slide[field] for field in ("title", "body", "cta")) else None


def _parse_content_pack_carousel_slides(carousel_idea):
    lines = carousel_idea.splitlines()
    has_slide_markers = any(SLIDE_MARKER_RE.match(line.strip()) for line in lines)

    if not has_slide_markers:
        if any(SLIDE_FIELD_RE.match(line.strip()) for line in lines):
            slide = _parse_slide_block(lines)
            return [slide] if slide else []
        return [
            {"title": line.strip(), "body": None, "cta": None, "brand": None}
            for line in lines
            if line.strip()
        ]

    blocks = []
    current_block = None
    for line in lines:
        marker_match = SLIDE_MARKER_RE.match(line.strip())
        if marker_match:
            if current_block is not None:
                blocks.append(current_block)
            current_block = []
            if marker_match.group(1):
                current_block.append(marker_match.group(1))
        elif current_block is not None:
            current_block.append(line)
    if current_block is not None:
        blocks.append(current_block)

    return [slide for block in blocks if (slide := _parse_slide_block(block))]


def _build_slide_background_prompt(styled_image_prompt, slide_index):
    visual_concept = SLIDE_VISUAL_CONCEPTS[slide_index]
    return f"""
Create a text-free visual background for one slide in a cohesive Instagram carousel.

Shared art direction for the whole carousel:
{styled_image_prompt}

Slide-specific visual concept:
{visual_concept}

Design:
- maintain one consistent art style, colour palette, lighting, and premium brand mood
- square 1:1 format
- high contrast
- leave suitable uncluttered visual space for a later text overlay

Critical text-free requirements:
- no readable text
- no words
- no letters
- no handwriting
- no pseudo-text
- no gibberish text
- no typography
- no captions
- no labels
- no readable logos
- no text on screens
- no text on paper
- no written signs
"""


def _content_pack_helper(name):
    helpers = current_app.extensions.get("smu_content_pack_helpers", {})
    helper = helpers.get(name)

    if not helper:
        raise RuntimeError(f"Content Pack helper is not available: {name}")

    return helper


@login_required
@subscription_required
def content_pack():
    source_text = ""
    content_pack_result = None
    session["content_pack_started"] = True

    if request.method == "POST":
        source_type = request.form.get("source_type", "text")
        source_input = request.form.get("source_input", "").strip()
        reserved_content_pack_credit = False

        if not source_input:
            flash("Please enter a TikTok URL or topic/text.", "danger")
            return redirect(url_for("content_pack"))

        try:
            user = current_user._get_current_object()
            if not _content_pack_helper("reserve_content_pack_credits")(user):
                summary = _content_pack_helper("get_usage_summary")(user)
                flash(
                    _content_pack_helper("usage_limit_message")(
                        summary,
                        "content_packs",
                    ),
                    "warning",
                )
                return redirect(url_for("content_pack"))

            reserved_content_pack_credit = True

            if source_type == "tiktok":
                source_text = _content_pack_helper("extract_tiktok_transcript")(
                    source_input
                )
            else:
                source_text = source_input

            brand_context = _content_pack_helper("build_brand_context")(current_user.id)
            content_pack_result = _content_pack_helper("generate_content_pack")(
                source_text,
                brand_context,
            )

        except Exception as e:
            if reserved_content_pack_credit:
                _content_pack_helper("release_content_pack_credits")(
                    current_user._get_current_object()
                )
            print("Content pack error:", e)
            flash(f"Failed: {e}", "danger")

    return render_template(
        "content_pack.html",
        source_text=source_text,
        content_pack_result=content_pack_result,
    )


@login_required
@subscription_required
def create_content_pack_carousel():
    content_pack_result = request.form.get("content_pack_result", "").strip()
    image_style = request.form.get("image_style", "").strip()

    if not content_pack_result:
        flash("No content pack found.", "danger")
        return redirect(url_for("content_pack"))

    extract_content_pack_section = _content_pack_helper("extract_content_pack_section")
    apply_image_style = _content_pack_helper("apply_image_style")
    get_placeholder_image_url = _content_pack_helper("get_placeholder_image_url")

    caption = extract_content_pack_section(content_pack_result, "INSTAGRAM_CAPTION")
    carousel_idea = extract_content_pack_section(content_pack_result, "CAROUSEL_IDEA")
    image_prompt = extract_content_pack_section(content_pack_result, "IMAGE_PROMPT")
    hashtags = extract_content_pack_section(content_pack_result, "HASHTAGS")

    if hashtags:
        caption = caption + "\n\n" + hashtags

    if not carousel_idea:
        flash("No carousel idea found in the content pack.", "danger")
        return redirect(url_for("content_pack"))

    try:
        styled_image_prompt = apply_image_style(image_prompt, image_style)
        slides = _parse_content_pack_carousel_slides(carousel_idea)[:6]

        if len(slides) < 2:
            flash("Carousel needs at least 2 slides.", "danger")
            return redirect(url_for("content_pack"))

        group_id = str(uuid.uuid4())
        placeholder_url = get_placeholder_image_url()

        for index, slide in enumerate(slides):
            background_prompt = _build_slide_background_prompt(
                styled_image_prompt,
                index,
            )
            stored_prompt = build_content_pack_overlay_prompt(
                background_prompt,
                slide["title"],
                body=slide["body"],
                cta=slide["cta"],
                brand=slide["brand"],
            )

            post = Post(
                file_url=placeholder_url,
                file_type="image",
                prompt=stored_prompt,
                caption=caption,
                platforms="instagram",
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
            "Carousel draft created. Images are generating in the background.",
            "success",
        )
        return redirect(url_for("view_post", post_id=first_post.id))

    except Exception as e:
        print("Create content pack carousel error:", e)
        flash(f"Failed to create content pack carousel: {e}", "danger")
        return redirect(url_for("content_pack"))


@login_required
@subscription_required
def create_content_pack_platform_draft():
    content_pack_result = request.form.get("content_pack_result", "").strip()
    platform = request.form.get("platform", "").strip()
    image_style = request.form.get("image_style", "").strip()

    allowed_platforms = [
        "instagram",
        "facebook",
        "linkedin",
        "pinterest",
        "reddit",
        "x",
    ]

    if not content_pack_result:
        flash("No content pack found.", "danger")
        return redirect(url_for("content_pack"))

    if platform not in allowed_platforms:
        flash("Invalid platform selected.", "danger")
        return redirect(url_for("content_pack"))

    extract_content_pack_section = _content_pack_helper("extract_content_pack_section")
    build_brand_context = _content_pack_helper("build_brand_context")
    apply_image_style = _content_pack_helper("apply_image_style")
    get_placeholder_image_url = _content_pack_helper("get_placeholder_image_url")

    image_prompt = extract_content_pack_section(content_pack_result, "IMAGE_PROMPT")
    hashtags = extract_content_pack_section(content_pack_result, "HASHTAGS")

    if platform == "instagram":
        caption = extract_content_pack_section(content_pack_result, "INSTAGRAM_CAPTION")

        if hashtags:
            caption = caption + "\n\n" + hashtags

    elif platform == "facebook":
        caption = extract_content_pack_section(content_pack_result, "FACEBOOK_POST")

    elif platform == "linkedin":
        caption = extract_content_pack_section(content_pack_result, "LINKEDIN_POST")

    elif platform == "pinterest":
        title = extract_content_pack_section(content_pack_result, "PINTEREST_PIN_TITLE")
        description = extract_content_pack_section(
            content_pack_result,
            "PINTEREST_PIN_DESCRIPTION"
        )
        caption = f"{title}\n\n{description}".strip()

    elif platform == "reddit":
        caption = extract_content_pack_section(content_pack_result, "REDDIT_POST")

    elif platform == "x":
        caption = extract_content_pack_section(content_pack_result, "X_POST")

    if not caption:
        flash(f"No {platform} content found in the content pack.", "danger")
        return redirect(url_for("content_pack"))

    try:
        brand_context = build_brand_context(current_user.id)

        enhanced_prompt = f"""
Brand Brief:
{brand_context}

Create a social media image for this {platform} post.

Post Caption:
{caption}

Extra Visual Direction:
{image_prompt}

Requirements:
- Match the meaning and mood of the post
- Avoid random unrelated objects
- Avoid generic stock image style
- Square 1:1 format
- High quality
- Suitable for {platform}
"""

        styled_prompt = apply_image_style(enhanced_prompt, image_style)
        placeholder_url = get_placeholder_image_url()

        post = Post(
            file_url=placeholder_url,
            file_type="image",
            prompt=styled_prompt,
            caption=caption,
            platforms=platform,
            post_type="single",
            status="generating",
            sort_order=0,
            is_cover=False,
            user_id=current_user.id,
        )

        db.session.add(post)
        db.session.commit()

        flash(
            f"{platform.title()} draft created. Image is generating in the background.",
            "success",
        )
        return redirect(url_for("view_post", post_id=post.id))

    except Exception as e:
        print("Create platform draft error:", e)
        flash(f"Failed to create {platform} draft: {e}", "danger")
        return redirect(url_for("content_pack"))


@content_pack_bp.record_once
def register_content_pack_routes(state):
    routes = [
        ("/content-pack", "content_pack", content_pack, ["GET", "POST"]),
        (
            "/content-pack/create-carousel",
            "create_content_pack_carousel",
            create_content_pack_carousel,
            ["POST"],
        ),
        (
            "/content-pack/create-platform-draft",
            "create_content_pack_platform_draft",
            create_content_pack_platform_draft,
            ["POST"],
        ),
    ]

    for rule, endpoint, view_func, methods in routes:
        state.app.add_url_rule(rule, endpoint, view_func, methods=methods)
