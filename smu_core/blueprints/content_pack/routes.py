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
    r"^(Title|Subtitle|Phrase|Translation|Body|Tip|CTA|Visual|Eyebrow|Emphasis)\s*:\s*(.*)$",
    re.IGNORECASE,
)
BODY_FIELD_NAMES = {"subtitle", "translation", "body", "tip"}
CONTENT_PACK_CAROUSEL_MIN_SLIDES = 2
CONTENT_PACK_CAROUSEL_MAX_SLIDES = 6
VIRAL_CAROUSEL_MAX_COVER_WORDS = 8
VIRAL_CAROUSEL_MAX_HEADLINE_WORDS = 8
VIRAL_CAROUSEL_MAX_SUPPORT_WORDS = 12
GENERIC_CLOSING_HEADLINES = {"takeaway", "summary", "final thought", "conclusion"}
SLIDE_VISUAL_CONCEPTS = (
    "A clean introductory hero composition with one relevant focal subject and strong "
    "negative space, without trying to illustrate every detail of the source.",
    "A simple scene or object grouping that supports the first distinct value point, "
    "with generous negative space and no speech bubbles.",
    "A different but cohesive scene supporting the next distinct idea, using an "
    "uncluttered composition and one clear focal subject.",
    "A restrained detail, object, or human moment that supports the slide's idea "
    "without becoming a literal diagram or labelled infographic.",
    "A calm takeaway composition with minimal supporting elements, clear visual "
    "hierarchy, and room for concise overlay copy.",
    "A simple closing composition with one relevant focal element and substantial "
    "clean space for a conclusion or call to action.",
)


def _select_layout_variant(layout_role, slide_index):
    if layout_role == "cover":
        return "hero_left"
    if layout_role == "cta":
        return "closing"
    if layout_role == "phrase":
        return "split_left" if slide_index % 2 else "split_right"
    return ("editorial_statement", "visual_focus", "split_right")[slide_index % 3]


def _select_visual_treatment(visual, layout_role):
    normalized = (visual or "").lower()
    if layout_role == "cta" or not normalized or "typography-only" in normalized:
        return "typography_only"
    if any(word in normalized for word in ("generic", "decorative", "abstract shape", "random geometry")):
        return "typography_only"
    if any(word in normalized for word in ("compare", "comparison", "before", "after", "versus")):
        return "comparison"
    if any(word in normalized for word in ("step", "sequence", "stage", "process", "progression")):
        return "process"
    if any(word in normalized for word in ("branch", "flow", "connect", "platform", "channel", "node")):
        return "diagram"
    return "illustration"


def _append_slide_value(slide, field, value):
    if not value:
        return
    existing = slide.get(field)
    slide[field] = f"{existing}\n{value}" if existing else value


def _parse_slide_block(lines):
    slide = {
        "title": None,
        "body": None,
        "cta": None,
        "brand": None,
        "visual": None,
        "layout_role": "info",
    }
    active_field = None

    for line in lines:
        if not line.strip():
            continue
        field_match = SLIDE_FIELD_RE.match(line.strip())
        if field_match:
            label, value = field_match.groups()
            label = label.lower()
            if label in {"title", "phrase"}:
                active_field = "title"
                if label == "phrase":
                    slide["layout_role"] = "phrase"
            elif label in BODY_FIELD_NAMES:
                active_field = "body"
            elif label == "visual":
                active_field = "visual"
            elif label == "eyebrow":
                active_field = "eyebrow"
            elif label == "emphasis":
                active_field = "emphasis"
            else:
                active_field = "cta"
                if slide["layout_role"] != "phrase":
                    slide["layout_role"] = "cta"
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
            {
                "title": line.strip(),
                "body": None,
                "cta": None,
                "brand": None,
                "visual": None,
                "layout_role": "info",
            }
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


def _normalize_content_pack_carousel_slides(slides):
    if len(slides) <= CONTENT_PACK_CAROUSEL_MAX_SLIDES:
        return slides

    final_cta_index = next(
        (
            index
            for index in range(len(slides) - 1, 0, -1)
            if slides[index]["layout_role"] == "cta"
        ),
        None,
    )
    if final_cta_index is None:
        return slides[:CONTENT_PACK_CAROUSEL_MAX_SLIDES]

    retained_content = [
        slide
        for index, slide in enumerate(slides[1:], start=1)
        if index != final_cta_index and slide["layout_role"] != "cta"
    ][: CONTENT_PACK_CAROUSEL_MAX_SLIDES - 2]
    return [slides[0], *retained_content, slides[final_cta_index]]


def _normalized_copy(value):
    return " ".join(re.findall(r"[\w']+", (value or "").lower(), re.UNICODE))


def _copy_word_count(value):
    return len(re.findall(r"\b[\w']+\b", value or "", re.UNICODE))


def _validate_viral_carousel_copy(slides):
    """Reject structurally poor artwork copy without rewriting approved wording."""
    seen_headlines = set()
    previous_message = None
    for index, slide in enumerate(slides):
        title = slide["title"] or ""
        body = slide["body"] or ""
        normalized_title = _normalized_copy(title)
        normalized_body = _normalized_copy(body)
        headline_limit = (
            VIRAL_CAROUSEL_MAX_COVER_WORDS
            if index == 0
            else VIRAL_CAROUSEL_MAX_HEADLINE_WORDS
        )
        if _copy_word_count(title) > headline_limit:
            raise ValueError("carousel_headline_too_dense")
        if _copy_word_count(body) > VIRAL_CAROUSEL_MAX_SUPPORT_WORDS:
            raise ValueError("carousel_support_too_dense")
        if len(re.findall(r"[.!?]+(?:\s|$)", body)) > 1:
            raise ValueError("carousel_support_too_dense")
        if normalized_body and normalized_body == normalized_title:
            raise ValueError("carousel_support_repeats_headline")
        if normalized_title in seen_headlines:
            raise ValueError("carousel_repeats_slide")
        seen_headlines.add(normalized_title)
        message = " ".join(value for value in (normalized_title, normalized_body) if value)
        if previous_message and message == previous_message:
            raise ValueError("carousel_repeats_slide")
        previous_message = message

    if _normalized_copy(slides[-1]["title"]) in GENERIC_CLOSING_HEADLINES:
        raise ValueError("carousel_generic_closing")


def _safe_visual_direction(visual):
    """Map untrusted visual prose to text-free scene categories."""
    normalized = (visual or "").lower()
    directions = []
    scene_categories = (
        (("copy", "duplicate", "same post", "rewrite", "branch"),
         "one original object branching into several visibly distinct destinations"),
        (("platform", "channel", "format", "destination"),
         "contrasting content environments connected by one coherent visual system"),
        (("source", "raw material", "notes", "organise", "organize"),
         "raw source materials being examined, sorted, and shaped into a clear idea"),
        (("angle", "perspective", "viewpoint", "different view"),
         "one focal object interpreted from clearly different visual perspectives"),
        (("publish", "campaign", "ready", "complete", "content system"),
         "a finished cohesive campaign system with distinct prepared outputs"),
        (("step", "sequence", "process", "journey"),
         "a clear progression of objects through distinct stages without labels"),
        (("problem", "mismatch", "doesn't fit", "does not fit"),
         "one rigid form contrasted against several differently shaped destinations"),
        (("word", "phrase", "vocabulary", "language", "conversation"),
         "a culturally relevant everyday context supporting language learning"),
        (("food", "travel", "object"),
         "a clean arrangement of relevant everyday objects in a specific setting"),
        (("fact", "research", "information", "evidence"),
         "source materials and abstract evidence shapes arranged around one focal insight"),
        (("product", "tool", "software", "workflow"),
         "a polished system of purposeful objects showing a practical workflow without UI"),
        (("sunset", "sunrise", "golden hour"), "warm sunset atmosphere"),
        (("people", "person", "smiling", "waving", "greeting"),
         "a friendly conversational interaction in purposeful environmental context"),
        (("phone", "mobile", "app"),
         "a smartphone used as a secondary prop with abstract text-free interface shapes"),
    )
    for keywords, direction in scene_categories:
        if any(keyword in normalized for keyword in keywords):
            directions.append(direction)
        if len(directions) == 2:
            break
    return "; ".join(directions) if directions else None


def _build_slide_background_prompt(
    styled_image_prompt,
    slide_index,
    visual=None,
    layout_role=None,
    layout_variant=None,
):
    visual_concept = SLIDE_VISUAL_CONCEPTS[slide_index]
    safe_visual_direction = _safe_visual_direction(visual)
    role = layout_role or ("cover" if slide_index == 0 else "info")
    design_layout = layout_variant or _select_layout_variant(role, slide_index)
    composition_directions = {
        "hero_left": (
            "Reserve a broad, dramatic low-detail region across the left 60% for oversized "
            "headline typography; place the focal subject on the right."
        ),
        "hero_center": (
            "Keep a large calm central field for oversized headline typography and frame "
            "the focal environment around its edges."
        ),
        "split_left": (
            "Keep the left half calm and low-detail for prominent typography; weight the "
            "subject or object toward the right half without drawing a divider."
        ),
        "split_right": (
            "Keep the right half calm and low-detail for prominent typography; weight the "
            "subject or object toward the left half without drawing a divider."
        ),
        "editorial_statement": (
            "Reserve a generous calm upper and central field for an oversized editorial "
            "statement; keep atmospheric artwork secondary and away from the type."
        ),
        "visual_focus": (
            "Let one strong focal subject carry the upper composition while preserving a "
            "wide, calm lower-third region for large typography."
        ),
        "closing": (
            "Reserve a large calm central region for a confident concluding statement; keep "
            "supporting artwork asymmetric and avoid button-like or interface shapes."
        ),
    }
    composition_direction = composition_directions[design_layout]
    return f"""
Create a text-free visual background for one slide in a cohesive Instagram carousel.

Shared art direction for the whole carousel:
{styled_image_prompt}

Mandatory carousel style lock:
- the shared art direction above controls the rendering medium for every slide
- keep that same medium, colour treatment, lighting treatment, visual polish, and brand mood
- the slide-specific concept changes only the scene, subjects, props, framing, and composition
- do not let the slide-specific concept introduce a different visual medium or art style

Slide-specific visual concept:
{visual_concept}
{f"Additional sanitized scene direction: {safe_visual_direction}." if safe_visual_direction else ""}

Slide role: {role}
Internal design layout: {design_layout}
Text-overlay composition:
{composition_direction}

Design:
- maintain one consistent art style, colour palette, lighting, and premium brand mood
- square 1:1 format
- high contrast
- compose the artwork and reserved typography space as one intentional social-carousel design
- avoid decorative dashes, fake buttons, fake interfaces, automatic badges, and slide numbers
- leave suitable uncluttered visual space for a later text overlay
- keep faces, facial features, and primary objects completely outside the text-safe zone
- do not place an important subject beneath or behind the intended typography region

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
- no generated app-store badges
- no readable app wordmarks
- text-free requirements override any conflicting typography or signage in the style direction
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
    reserve_ai_image_credits = _content_pack_helper("reserve_ai_image_credits")

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
        slides = _normalize_content_pack_carousel_slides(
            _parse_content_pack_carousel_slides(carousel_idea)
        )

        if len(slides) < CONTENT_PACK_CAROUSEL_MIN_SLIDES:
            flash(
                "SMU couldn't create enough carousel slides from this Content Pack. "
                "Please regenerate the Content Pack and try again.",
                "danger",
            )
            return redirect(url_for("content_pack"))

        if image_style == "viral_carousel":
            _validate_viral_carousel_copy(slides)

        required_images = len(slides)
        user = current_user._get_current_object()
        if not reserve_ai_image_credits(user, required_images, commit=False):
            db.session.rollback()
            summary = _content_pack_helper("get_usage_summary")(user)
            remaining = summary["ai_images_remaining"]
            flash(
                f"You need {required_images} AI image credits to create this "
                f"carousel, but you have {remaining} remaining.",
                "warning",
            )
            return redirect(url_for("content_pack"))

        group_id = str(uuid.uuid4())
        placeholder_url = get_placeholder_image_url()

        for index, slide in enumerate(slides):
            layout_role = "cover" if index == 0 else slide["layout_role"]
            layout_variant = _select_layout_variant(layout_role, index)
            visual_treatment = _select_visual_treatment(slide["visual"], layout_role)
            background_prompt = _build_slide_background_prompt(
                styled_image_prompt,
                index,
                slide["visual"],
                layout_role,
                layout_variant,
            )
            stored_prompt = build_content_pack_overlay_prompt(
                background_prompt,
                slide["title"],
                body=slide["body"],
                cta=slide["cta"],
                brand=slide["brand"],
                credits_reserved=True,
                layout_role=layout_role,
                layout_variant=layout_variant,
                typography={
                    "eyebrow": slide.get("eyebrow"),
                    "emphasis": (
                        {"text": slide["emphasis"], "role": "accent"}
                        if slide.get("emphasis") and slide["emphasis"] in slide["title"]
                        else None
                    ),
                },
                visual_treatment=visual_treatment,
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
        db.session.rollback()
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
