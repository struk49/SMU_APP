import uuid

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from smu_core.extensions import db
from smu_core.models import Post
from smu_core.services.access import subscription_required
from smu_core.services.carousel_generation import build_content_pack_overlay_prompt


content_pack_bp = Blueprint("content_pack", __name__)


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
            flash("Carousel needs at least 2 slides.", "danger")
            return redirect(url_for("content_pack"))

        group_id = str(uuid.uuid4())
        placeholder_url = get_placeholder_image_url()

        for index, slide_text in enumerate(slides):
            background_prompt = f"""
Create a text-free visual background for an Instagram carousel slide.

Visual direction:
{styled_image_prompt}

Design:
- dark background
- high contrast
- square 1:1 format
- premium social media style

Critical text-free requirements:
- no readable text
- no words or letters
- no typography
- no captions or labels
- no logos containing text
- no pseudo-text or gibberish
- leave suitable uncluttered visual space for a later text overlay
"""
            stored_prompt = build_content_pack_overlay_prompt(
                background_prompt,
                slide_text,
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
