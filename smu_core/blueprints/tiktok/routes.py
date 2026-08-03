import uuid

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from smu_core.extensions import db
from smu_core.models import Post


tiktok_bp = Blueprint("tiktok", __name__)


def _tiktok_helper(name):
    helpers = current_app.extensions.get("smu_tiktok_helpers", {})
    helper = helpers.get(name)

    if not helper:
        raise RuntimeError(f"TikTok helper is not available: {name}")

    return helper


@login_required
def tiktok_repurpose():
    transcript = None
    generated_content = None
    tiktok_url = ""

    if request.method == "POST":
        tiktok_url = request.form.get("tiktok_url", "").strip()

        if not tiktok_url:
            flash("Please enter a TikTok URL.", "danger")
            return redirect(url_for("tiktok_repurpose"))

        try:
            transcript = _tiktok_helper("extract_tiktok_transcript")(tiktok_url)
            brand_context = _tiktok_helper("build_brand_context")(current_user.id)

            generated_content = _tiktok_helper("repurpose_tiktok_content")(
                transcript,
                brand_context
            )

        except Exception as e:
            print("TikTok repurpose error:", e)
            flash(f"Failed: {e}", "danger")

    return render_template(
        "tiktok.html",
        tiktok_url=tiktok_url,
        transcript=transcript,
        generated_content=generated_content,
    )


@login_required
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

    try:
        styled_prompt = _tiktok_helper("apply_image_style")(image_prompt, image_style)
        image_url = _tiktok_helper("generate_openai_image")(styled_prompt)

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
        return redirect(url_for("view_post", post_id=post.id))

    except Exception as e:
        print("Create TikTok draft error:", e)
        flash(f"Failed to create draft: {e}", "danger")
        return redirect(url_for("tiktok_repurpose"))


@login_required
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
        {brand_context}

        {image_prompt}
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
            flash("Carousel needs at least 2 slides.", "danger")
            return redirect(url_for("tiktok_repurpose"))

        group_id = str(uuid.uuid4())
        placeholder_url = _tiktok_helper("get_placeholder_image_url")()

        for index, slide_text in enumerate(slides):
            if index == 0:
                full_prompt = f"""
Create a HIGH-CONVERTING viral Instagram carousel COVER slide.

Main headline:
{slide_text}

Use this visual direction:
{styled_image_prompt}

Design style:
- dark background
- bold typography
- yellow accent blocks
- white headline text
- green and blue highlight colours
- square 1:1 format
"""
            else:
                full_prompt = f"""
Create a HIGH-CONVERTING Instagram carousel educational slide.

Slide content:
{slide_text}

Use this visual direction:
{styled_image_prompt}

Design style:
- dark background
- bold typography
- yellow highlight boxes
- white main text
- green accents
- square 1:1 format
"""

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
        return redirect(url_for("view_post", post_id=first_post.id))

    except Exception as e:
        print("Create TikTok carousel draft error:", e)
        flash(f"Failed to create carousel draft: {e}", "danger")
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
