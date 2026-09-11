import logging
import re
import uuid

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from smu_core.extensions import db
from smu_core.models import Post
from smu_core.services.access import subscription_required
from smu_core.services.carousel_generation import build_content_pack_overlay_prompt
from smu_core.services.content import ContentPackGenerationError
from smu_core.services.social_text import preflight_viral_carousel_text


content_pack_bp = Blueprint("content_pack", __name__)
logger = logging.getLogger(__name__)

SLIDE_MARKER_RE = re.compile(r"^Slide\s+\d+\s*:\s*(.*)$", re.IGNORECASE)
SLIDE_FIELD_RE = re.compile(
    r"^(Title|Subtitle|Phrase|Translation|Body|Tip|CTA|Visual|Visual Weight|Eyebrow|Emphasis)\s*:\s*(.*)$",
    re.IGNORECASE,
)
BODY_FIELD_NAMES = {"subtitle", "translation", "body", "tip"}
CONTENT_PACK_CAROUSEL_MIN_SLIDES = 2
CONTENT_PACK_CAROUSEL_MAX_SLIDES = 6
GENERIC_CLOSING_HEADLINES = {"takeaway", "summary", "final thought", "conclusion"}
COPY_WORD_RE = re.compile(r"\b[\w']+(?:[-‐‑–][\w']+)*\b", re.UNICODE)
VISUAL_WEIGHTS = {"heavy", "medium", "light"}
STRUCTURE_ROLES = {"cover", "phrase", "info", "cta"}
STRUCTURE_TREATMENTS = {
    "typography_only", "illustration", "diagram", "process", "comparison",
    "visual_focus", "feature_cards",
}
STRUCTURE_LAYOUTS = {
    "hero_left", "hero_center", "split_left", "split_right",
    "editorial_statement", "visual_focus", "closing",
}
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


def _select_layout_variant(
    layout_role, slide_index, visual_treatment=None, title=None
):
    if layout_role == "cover":
        return "hero_center" if visual_treatment == "visual_focus" else "hero_left"
    if layout_role == "cta":
        return "closing"
    if layout_role == "phrase":
        return "split_left" if slide_index % 2 else "split_right"
    if visual_treatment == "visual_focus":
        return "visual_focus"
    if visual_treatment == "diagram" and _copy_word_count(title) > 6:
        return "editorial_statement"
    if visual_treatment in {"diagram", "process", "comparison"}:
        return "split_right" if slide_index % 2 else "split_left"
    if visual_treatment in {"illustration", "feature_cards"}:
        return "split_right" if slide_index % 2 else "split_left"
    return ("editorial_statement", "visual_focus", "split_right")[slide_index % 3]


def _select_visual_treatment(visual, layout_role, semantic_text=None):
    normalized = " ".join(value for value in (visual, semantic_text) if value).lower()
    if layout_role == "cta" or not normalized or "typography-only" in normalized:
        return "typography_only"
    if any(word in normalized for word in ("generic", "decorative", "abstract shape", "random geometry")):
        return "typography_only"
    if any(word in normalized for word in ("compare", "comparison", "before", "after", "versus")):
        return "comparison"
    if any(word in normalized for word in ("step", "sequence", "stage", "process", "progression")):
        return "process"
    if any(word in normalized for word in ("instagram", "pinterest", "image frame", "media tile")):
        return "visual_focus"
    if any(word in normalized for word in ("linkedin", "editorial", "document", "article", "insight")):
        return "illustration"
    if any(word in normalized for word in ("reddit", "discussion", "thread", "community", "conversation")):
        return "diagram"
    if any(
        phrase in normalized
        for phrase in (
            "three benefits", "three features", "four benefits", "four features",
            "grouped elements", "feature cards",
        )
    ):
        return "feature_cards"
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
            elif label == "visual weight":
                active_field = "visual_weight"
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
    return len(COPY_WORD_RE.findall(value or ""))


class CarouselQualityError(ValueError):
    """Safe categorical rejection containing metrics but never slide copy."""

    def __init__(
        self, reason, *, slide_index, role, word_count, character_count,
        treatment=None, layout=None, measured_lines=None, font_size=None,
        structure_reason=None,
    ):
        self.reason = reason
        self.slide_index = slide_index
        self.role = role
        self.word_count = word_count
        self.character_count = character_count
        self.treatment = treatment
        self.layout = layout
        self.measured_lines = measured_lines
        self.font_size = font_size
        self.structure_reason = structure_reason
        super().__init__(reason)


def _reject_carousel_copy(
    reason, *, slide_index, role, value, treatment=None, layout=None,
    measured_lines=None, font_size=None, structure_reason=None,
):
    word_count = _copy_word_count(value)
    character_count = len((value or "").strip())
    logger.warning(
        "carousel_copy_quality_rejected slide_index=%s role=%s word_count=%s "
        "character_count=%s treatment=%s layout=%s measured_lines=%s "
        "font_size=%s reason=%s structure_reason=%s",
        slide_index,
        role,
        word_count,
        character_count,
        treatment,
        layout,
        measured_lines,
        font_size,
        reason,
        structure_reason,
    )
    raise CarouselQualityError(
        reason,
        slide_index=slide_index,
        role=role,
        word_count=word_count,
        character_count=character_count,
        treatment=treatment,
        layout=layout,
        measured_lines=measured_lines,
        font_size=font_size,
        structure_reason=structure_reason,
    )


def _visual_metaphor(slide):
    normalized = " ".join(
        value for value in (slide.get("visual"), slide.get("title")) if value
    ).lower()
    categories = (
        ("node_network", ("node", "network", "branch", "connect", "discussion")),
        ("document", ("document", "article", "paper", "editorial")),
        ("device", ("phone", "device", "screen", "laptop")),
        ("card_stack", ("card", "tile", "grid", "feature")),
        ("human_figure", ("person", "people", "human", "figure")),
        ("process_arrow", ("step", "sequence", "process", "arrow", "workflow")),
    )
    return next(
        (name for name, words in categories if any(word in normalized for word in words)),
        "distinct_object",
    )


def _carousel_presentations(slides):
    presentations = []
    for index, slide in enumerate(slides):
        role = "cover" if index == 0 else slide["layout_role"]
        semantic_text = " ".join(
            value for value in (slide["title"], slide["body"]) if value
        )
        treatment = _select_visual_treatment(slide["visual"], role, semantic_text)
        requested_weight = (slide.get("visual_weight") or "").strip().lower()
        visual_weight = (
            "heavy"
            if role == "cover"
            else "light"
            if role == "cta"
            else requested_weight
            if requested_weight in VISUAL_WEIGHTS
            else "heavy"
            if treatment == "visual_focus"
            else "light"
            if treatment == "typography_only"
            else "medium"
        )
        presentations.append(
            {
                "role": role,
                "treatment": treatment,
                "layout": _select_layout_variant(
                    role, index, treatment, slide["title"]
                ),
                "semantic_text": semantic_text,
                "visual_weight": visual_weight,
                "metaphor": _visual_metaphor(slide),
            }
        )

    for index in range(1, len(presentations)):
        previous = presentations[index - 1]
        current = presentations[index]
        semantic = current["semantic_text"].lower()
        genuine_sequence = any(
            word in semantic for word in ("step", "sequence", "process", "workflow")
        )
        if (
            previous["treatment"] == current["treatment"] == "diagram"
            and not genuine_sequence
        ):
            current["treatment"] = "illustration"
        if (
            previous["metaphor"] == current["metaphor"]
            and current["metaphor"] != "distinct_object"
            and not genuine_sequence
            and current["treatment"] not in {"process", "comparison"}
        ):
            current["treatment"] = "visual_focus"
        current["layout"] = _select_layout_variant(
            current["role"], index, current["treatment"], slides[index]["title"]
        )
        if current["layout"] == previous["layout"]:
            if current["layout"] == "split_left":
                current["layout"] = "split_right"
            elif current["layout"] == "split_right":
                current["layout"] = "split_left"

    if len(presentations) >= 4 and all(
        item["visual_weight"] == "medium" for item in presentations[1:-1]
    ):
        relief_index = 1 + len(presentations[1:-1]) // 2
        presentations[relief_index]["visual_weight"] = "light"
    return presentations


def _validate_viral_carousel_copy(slides, presentations=None):
    """Reject structurally poor artwork copy without rewriting approved wording."""
    presentations = presentations or _carousel_presentations(slides)
    seen_headlines = set()
    previous_message = None
    for index, slide in enumerate(slides):
        slide_index = index + 1
        title = slide["title"] or ""
        body = slide["body"] or ""
        presentation = presentations[index]
        role = presentation["role"]
        treatment = presentation["treatment"]
        layout = presentation["layout"]
        structure_reason = None
        if not isinstance(title, str) or not title.strip():
            structure_reason = "invalid_required_title"
        elif any(
            value is not None and not isinstance(value, str)
            for value in (
                slide.get("body"), slide.get("cta"), slide.get("brand"),
                slide.get("visual"), slide.get("eyebrow"), slide.get("emphasis"),
                slide.get("visual_weight"),
            )
        ):
            structure_reason = "invalid_optional_field_type"
        elif role not in STRUCTURE_ROLES:
            structure_reason = "unsupported_role"
        elif treatment not in STRUCTURE_TREATMENTS:
            structure_reason = "unsupported_treatment"
        elif layout not in STRUCTURE_LAYOUTS:
            structure_reason = "unsupported_layout"
        elif presentation.get("visual_weight") not in VISUAL_WEIGHTS:
            structure_reason = "unsupported_visual_weight"
        if structure_reason:
            _reject_carousel_copy(
                "carousel_copy_structure_invalid",
                structure_reason=structure_reason,
                slide_index=slide_index,
                role=role,
                value=title if isinstance(title, str) else "",
                treatment=treatment,
                layout=layout,
            )
        normalized_title = _normalized_copy(title)
        normalized_body = _normalized_copy(body)
        if normalized_body and normalized_body == normalized_title:
            raise ValueError("carousel_support_repeats_headline")
        if normalized_title in seen_headlines:
            raise ValueError("carousel_repeats_slide")
        seen_headlines.add(normalized_title)
        message = " ".join(value for value in (normalized_title, normalized_body) if value)
        if previous_message and message == previous_message:
            raise ValueError("carousel_repeats_slide")
        previous_message = message

        emphasis = (
            {"text": slide["emphasis"], "role": "accent"}
            if slide.get("emphasis") and slide["emphasis"] in title
            else None
        )
        result = preflight_viral_carousel_text(
            title=title,
            body=body,
            cta=slide["cta"],
            brand=slide["brand"],
            eyebrow=slide.get("eyebrow"),
            emphasis=emphasis,
            layout_role=role,
            layout_variant=layout,
            visual_treatment=treatment,
            visual_weight=presentation["visual_weight"],
        )
        if not result["fits"]:
            support_failure = bool(body) and not result["support_fits"]
            _reject_carousel_copy(
                (
                    "carousel_support_does_not_fit"
                    if support_failure
                    else "carousel_headline_does_not_fit"
                ),
                slide_index=slide_index,
                role=role,
                value=body if support_failure else title,
                treatment=treatment,
                layout=layout,
                measured_lines=result.get(
                    "support_lines" if support_failure else "headline_lines"
                ),
                font_size=result.get(
                    "support_font_size" if support_failure else "headline_font_size"
                ),
            )

    if _normalized_copy(slides[-1]["title"]) in GENERIC_CLOSING_HEADLINES:
        raise ValueError("carousel_generic_closing")


def _safe_visual_direction(visual, semantic_text=None):
    """Map untrusted visual prose to text-free scene categories."""
    normalized = " ".join(value for value in (visual, semantic_text) if value).lower()
    directions = []
    scene_categories = (
        (("instagram",),
         "a bold image frame with layered text-free media cards and a strong visual focal area"),
        (("linkedin",),
         "a refined editorial document composition suggesting considered professional insight"),
        (("reddit",),
         "an organic network of conversation nodes suggesting discussion and shared context"),
        (("pinterest",),
         "a curated pinboard-inspired grid of varied text-free visual cards"),
        (("facebook",),
         "a connected community feed metaphor using text-free content cards"),
        ((" x ", "short message"),
         "a concise message-and-network metaphor without symbols, branding, or text"),
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
    visual_treatment=None,
    semantic_text=None,
    visual_weight="medium",
):
    visual_concept = SLIDE_VISUAL_CONCEPTS[slide_index]
    safe_visual_direction = _safe_visual_direction(visual, semantic_text)
    role = layout_role or ("cover" if slide_index == 0 else "info")
    design_layout = layout_variant or _select_layout_variant(role, slide_index)
    composition_directions = {
        "hero_left": (
            "Use one isolated focal subject weighted to the right artwork zone. Keep the "
            "protected left typography zone completely free of artwork and detail."
        ),
        "hero_center": (
            "Keep a large calm central field for oversized headline typography and frame "
            "the focal environment around its edges."
        ),
        "split_left": (
            "Contain one subject in the designated right artwork zone. Keep the protected "
            "left typography zone empty, with a clean gutter and no divider."
        ),
        "split_right": (
            "Contain one subject in the designated left artwork zone. Keep the protected "
            "right typography zone empty, with a clean gutter and no divider."
        ),
        "editorial_statement": (
            "Keep artwork as one small isolated supporting object in the bounded secondary "
            "zone; reserve the dominant protected field for editorial typography."
        ),
        "visual_focus": (
            "Center one strong focal subject inside the bounded upper artwork zone. Keep "
            "the separate lower typography zone clean and completely artwork-free."
        ),
        "closing": (
            "Typography must dominate. If artwork is useful, isolate one minimal accent "
            "object in the small upper-right zone and keep it away from the conclusion."
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
Selected visual treatment: {visual_treatment or "illustration"}
Allowlisted visual weight: {visual_weight}
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
- make the selected treatment structurally visible: diagrams use meaningful connected
  geometry, illustrations use one semantic scene, and visual-focus slides use one
  dominant object or bounded content metaphor
- never use official platform logos, trademark-shaped icons, or readable platform names

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

        except ContentPackGenerationError:
            if reserved_content_pack_credit:
                _content_pack_helper("release_content_pack_credits")(
                    current_user._get_current_object()
                )
            flash(
                "We couldn't generate your Content Pack. Please try again.",
                "danger",
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

        presentations = _carousel_presentations(slides)
        if image_style == "viral_carousel":
            _validate_viral_carousel_copy(slides, presentations)

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
            presentation = presentations[index]
            layout_role = presentation["role"]
            semantic_text = presentation["semantic_text"]
            visual_treatment = presentation["treatment"]
            layout_variant = presentation["layout"]
            background_prompt = _build_slide_background_prompt(
                styled_image_prompt,
                index,
                slide["visual"],
                layout_role,
                layout_variant,
                visual_treatment,
                semantic_text,
                presentation["visual_weight"],
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
                visual_weight=presentation["visual_weight"],
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

    except CarouselQualityError:
        db.session.rollback()
        flash(
            "We couldn't create this carousel because one slide was too "
            "text-heavy. Please regenerate the Content Pack.",
            "danger",
        )
        return redirect(url_for("content_pack"))
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
