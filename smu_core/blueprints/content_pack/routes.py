import logging
import re
from time import perf_counter
import uuid
from collections import Counter

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from smu_core.extensions import db
from smu_core.models import Post
from smu_core.services.access import subscription_required
from smu_core.services.carousel_generation import build_content_pack_overlay_prompt
from smu_core.services.content import (
    CAROUSEL_STRUCTURE_REPAIR_REASONS as REPAIRABLE_STORY_REASONS,
    CarouselStructureRepairError,
    ContentPackGenerationError,
)
from smu_core.services.social_text import (
    preflight_viral_carousel_text,
    select_editorial_composition,
    select_optical_lock,
    select_typography_presentation,
)


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
CAMPAIGN_ART_STYLES = {
    "realistic": "high-quality photography with cinematic editorial art direction",
    "viral_carousel": "viral Instagram business carousel with premium SaaS editorial illustration",
    "luxury": "luxury brand aesthetic with refined modern magazine artwork",
    "minimal": "minimalist modern design with sophisticated flat editorial illustration",
    "corporate": "professional corporate social media design with polished editorial illustration",
    "pixar": "3D animated film look with conceptual dimensional illustration",
}
DESIGN_MANAGER_STYLES = {
    "auto", "editorial_illustration", "minimal_premium", "photorealistic",
    "three_d_clay", "bold_graphic", "collage_magazine",
}
DESIGN_MANAGER_PALETTES = {
    "auto", "smu_classic", "monochrome", "warm_sunset", "cool_tech",
    "earth_and_cream", "electric", "soft_pastel",
}
STYLE_GRAMMARS = {
    "editorial_illustration": ("contemporary editorial illustration", "layered editorial depth", "controlled paper grain", "expressive simplified silhouettes"),
    "minimal_premium": ("premium minimalist product-editorial art", "precise restrained depth", "refined matte surfaces", "geometric forms with generous negative space"),
    "photorealistic": ("cinematic editorial photography", "believable natural depth of field", "realistic materials", "environmental forms with no stock-photo posing"),
    "three_d_clay": ("contemporary sculptural 3D clay editorial design", "soft directional studio depth", "tactile clay and matte materials", "bold dimensional focal forms"),
    "bold_graphic": ("high-contrast graphic poster art", "flat dramatic depth through scale and crop", "clean ink and print surfaces", "oversized angular geometry"),
    "collage_magazine": ("asymmetric editorial magazine collage", "layered cutout depth and overlap", "paper, photographic, and printed textures", "cut-paper forms with controlled irregularity"),
}
PALETTE_INTENTS = {
    "smu_classic": "deep navy, warm yellow, mint, and controlled blue",
    "monochrome": "black, charcoal, warm off-white, and controlled grey",
    "warm_sunset": "deep burgundy, coral, amber, and cream",
    "cool_tech": "deep blue, cyan, violet, and cool white",
    "earth_and_cream": "forest green, terracotta, sand, and cream",
    "electric": "near-black, electric blue, vivid lime, and selective magenta",
    "soft_pastel": "soft blue, lavender, peach, mint, and warm off-white",
}
TYPOGRAPHY_ONLY_BACKGROUND = "LOCAL_TYPOGRAPHY_CANVAS_NO_ARTWORK_PROMPT"
STRUCTURE_ROLES = {"cover", "phrase", "info", "cta"}
STRUCTURE_TREATMENTS = {
    "typography_only", "illustration", "diagram", "process", "comparison",
    "visual_focus", "feature_cards",
}
STRUCTURE_LAYOUTS = {
    "hero_left", "hero_center", "split_left", "split_right",
    "editorial_statement", "visual_focus", "closing",
}
SCENE_MODES = {
    "establishing", "interaction", "over_shoulder", "detail", "object_focus",
    "action", "environmental", "portrait", "symbolic",
}
SHOT_TYPES = {
    "wide", "medium", "close", "macro_detail", "over_shoulder", "top_down",
    "low_angle", "eye_level",
}
SUBJECT_CATEGORIES = {
    "person_interaction", "document_object", "food_object", "device",
    "environment", "hands_action", "abstract_symbol", "focal_object",
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
    phrase_pairs = []
    active_phrase_pair = None

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
                    active_phrase_pair = {"phrase": value, "translation": None}
                    phrase_pairs.append(active_phrase_pair)
            elif label in BODY_FIELD_NAMES:
                active_field = "body"
                if label == "translation" and active_phrase_pair is not None:
                    active_phrase_pair["translation"] = value
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

    if phrase_pairs:
        slide["phrase_pairs"] = tuple(
            (pair["phrase"], pair["translation"]) for pair in phrase_pairs
        )

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


EXPLICIT_PAIR_FIELD_RE = re.compile(
    r"^(Polish|Target Language|English|Translation)\s*:\s*(.*)$",
    re.IGNORECASE,
)


def _recover_exact_phrase_pairs(carousel_idea, slides):
    """Recover only explicitly labelled adjacent source pairs without translation."""
    blocks, current = [], None
    for line in carousel_idea.splitlines():
        if SLIDE_MARKER_RE.match(line.strip()):
            if current is not None:
                blocks.append(current)
            current = []
        elif current is not None and line.strip():
            current.append(line.strip())
    if current is not None:
        blocks.append(current)
    if len(blocks) != len(slides):
        return slides, 0

    recovered, recovery_count = [], 0
    for slide, block in zip(slides, blocks):
        updated = dict(slide)
        if updated.get("phrase_pairs"):
            recovered.append(updated)
            continue
        fields = []
        for line in block:
            match = EXPLICIT_PAIR_FIELD_RE.match(line)
            title_match = re.match(r"^Title\s*:\s*(.*)$", line, re.IGNORECASE)
            fields.append(
                (match.group(1).lower(), match.group(2)) if match
                else ("title", title_match.group(1)) if title_match
                else ("unsupported", "")
            )
        pair = None
        for left, right in zip(fields, fields[1:]):
            if left[0] in {"polish", "target language"} and right[0] in {
                "english", "translation",
            }:
                pair = (left[1], right[1])
                break
            if left[0] == "title" and right[0] == "translation":
                pair = (left[1], right[1])
                break
        if pair and pair[0] and pair[1]:
            updated.update({
                "title": pair[0],
                "body": pair[1],
                "layout_role": "phrase",
                "phrase_pairs": (pair,),
            })
            recovery_count += 1
        recovered.append(updated)
    return recovered, recovery_count


def _repair_cardinality_bounds(reason, original_slide_count):
    original = max(CONTENT_PACK_CAROUSEL_MIN_SLIDES, original_slide_count)
    policies = {
        "multiple_primary_headings": (original, original),
        "missing_campaign_cover": (original, min(CONTENT_PACK_CAROUSEL_MAX_SLIDES, original + 2)),
        "cover_is_teaching": (original, min(CONTENT_PACK_CAROUSEL_MAX_SLIDES, original + 2)),
        "teaching_unit_overload": (original, CONTENT_PACK_CAROUSEL_MAX_SLIDES),
        "closing_unit_overload": (original, min(CONTENT_PACK_CAROUSEL_MAX_SLIDES, original + 1)),
        "visual_budget_exceeded": (original, CONTENT_PACK_CAROUSEL_MAX_SLIDES),
    }
    return policies[reason]


def _normalized_copy(value):
    return " ".join(re.findall(r"[\w']+", (value or "").lower(), re.UNICODE))


def _copy_word_count(value):
    return len(COPY_WORD_RE.findall(value or ""))


SEMANTIC_DOMAIN_TERMS = (
    ("restaurant", ("restaurant", "menu", "waiter", "server", "food", "drink", "water", "bill", "dining", "order")),
    ("travel", ("travel", "airport", "hotel", "train", "journey", "trip", "ticket", "station")),
    ("language_learning", ("polish", "phrase", "translation", "vocabulary", "language", "speak", "conversation")),
    ("everyday_conversation", ("everyday", "greeting", "politeness", "useful question", "real conversation")),
    ("workplace", ("workplace", "office", "colleague", "meeting", "manager", "career")),
    ("healthcare", ("health", "doctor", "clinic", "hospital", "patient", "medical")),
    ("relationships", ("love", "romance", "relationship", "couple", "affection", "partner")),
    ("business", ("business", "customer", "sales", "marketing", "founder", "strategy")),
    ("technology", ("technology", "software", "platform", "app", "digital", "ai")),
    ("education", ("teach", "learn", "lesson", "student", "study", "education")),
    ("fitness", ("fitness", "exercise", "workout", "training", "gym", "running")),
    ("property", ("property", "home", "house", "estate", "mortgage", "rent")),
    ("finance", ("finance", "money", "budget", "saving", "investment", "bank")),
    ("cooking", ("cooking", "recipe", "kitchen", "ingredient", "bake")),
    ("beauty", ("beauty", "skincare", "cosmetic", "makeup", "salon")),
    ("lifestyle", ("lifestyle", "fashion", "wellness", "routine")),
)


def _semantic_domains_for_text(value):
    normalized = str(value or "").lower()
    return tuple(
        domain
        for domain, terms in SEMANTIC_DOMAIN_TERMS
        if any(
            re.search(rf"\b{re.escape(term)}\w*\b", normalized, re.UNICODE)
            for term in terms
        )
    )


def _campaign_semantic_domains(slides):
    """Prefer current cover and repeated slide evidence over incidental details."""
    if not slides:
        return ("general",)
    cover_corpus = " ".join(
        str(slides[0].get(key) or "") for key in ("title", "body", "visual")
    )
    cover_domains = _semantic_domains_for_text(cover_corpus)
    slide_domains = [set(_semantic_domains_for_text(" ".join(
        str(slide.get(key) or "")
        for key in ("title", "body", "visual")
    ))) for slide in slides]
    repeated_domains = {
        domain
        for domain, _ in SEMANTIC_DOMAIN_TERMS
        if sum(domain in evidence for evidence in slide_domains) >= 2
    }
    domains = tuple(
        domain
        for domain, _ in SEMANTIC_DOMAIN_TERMS
        if domain in cover_domains or domain in repeated_domains
    )
    if any(slide.get("phrase_pairs") for slide in slides) and "language_learning" not in domains:
        domains = (*domains, "language_learning")
    return domains or ("general",)


def _campaign_grounding(slides, campaign_direction):
    """Build new request-local semantic identity without retaining customer copy."""
    domains = _campaign_semantic_domains(slides)
    domain_set = set(domains)
    if {"language_learning", "restaurant"} <= domain_set:
        subject = "language-learning activity in a restaurant dining and service setting"
        goal = "teach practical restaurant conversation through concrete dining interactions"
    elif {"language_learning", "travel"} <= domain_set:
        subject = "language-learning activity in a practical travel setting"
        goal = "teach practical conversation through concrete travel interactions"
    elif {"language_learning", "relationships"} <= domain_set:
        subject = "language-learning activity in a personal-connection setting"
        goal = "teach relationship-oriented conversation through relevant human interactions"
    elif "restaurant" in domain_set:
        subject = "restaurant dining and service interaction"
        goal = "explain the current restaurant topic through concrete dining scenes"
    elif "relationships" in domain_set:
        subject = "relationship and personal-connection interaction"
        goal = "explain the current relationship topic through relevant human scenes"
    else:
        subject = f"current {' and '.join(domains)} topic"
        goal = "explain the current topic through concrete domain-relevant scenes"
    return {
        "campaign_subject": subject,
        "campaign_goal": goal,
        "audience": "a mobile social viewer seeking immediate practical understanding",
        "content_category": "language_learning" if "language_learning" in domain_set else domains[0],
        "semantic_domain": " + ".join(domains),
        "current_source_summary": subject,
        "current_carousel_theme": subject,
        "resolved_style": campaign_direction.get("resolved_style") or "legacy",
        "resolved_palette": campaign_direction.get("resolved_palette") or "legacy",
    }


def _slide_grounding(slide, campaign_grounding, role):
    """Ground one slide in its current campaign without exposing overlay wording."""
    normalized = " ".join(
        str(slide.get(key) or "") for key in ("title", "body", "visual")
    ).lower()
    campaign_domains = set(campaign_grounding["semantic_domain"].split(" + "))
    slide_domains = set(_semantic_domains_for_text(normalized))
    restaurant_scope = "restaurant" in slide_domains or (
        "restaurant" in campaign_domains and "everyday_conversation" not in campaign_domains
    )
    if restaurant_scope:
        if any(term in normalized for term in ("bill", "check", "pay", "payment")):
            purpose = "requesting and paying the restaurant bill"
            subject = "a diner and restaurant server completing payment at the table"
            action = "the diner politely requests a blank bill folder and prepares a payment card"
            environment = "a clearly recognizable restaurant dining room and table setting"
        elif any(term in normalized for term in ("water", "drink", "beverage")):
            purpose = "requesting or ordering a drink in a restaurant"
            subject = "a diner speaking with a restaurant server beside a dining table"
            action = "the server brings a glass and carafe while the diner makes a polite request"
            environment = "a restaurant dining room with tableware and clearly visible beverage service"
        elif any(term in normalized for term in ("food", "dish", "meal", "order")):
            purpose = "choosing and ordering food in a restaurant"
            subject = "a diner ordering a meal from a restaurant server at the table"
            action = "the diner gestures toward a blank menu as the server takes the order"
            environment = "a recognizable restaurant dining room with food, tableware, and service context"
        elif any(term in normalized for term in ("table", "reservation", "seat", "host")):
            purpose = "arriving and asking a restaurant host for a table"
            subject = "a guest speaking with a restaurant host near the entrance"
            action = "the host welcomes the guest and gestures toward an available dining table"
            environment = "a recognizable restaurant entrance, host stand, and dining room"
        else:
            purpose = "introducing practical conversation in a restaurant"
            subject = "a diner and restaurant staff member in a clear service interaction"
            action = "the people engage in a practical restaurant conversation"
            environment = "a recognizable restaurant with dining tables, tableware, and service activity"
        environment_category = "restaurant"
    elif "relationships" in slide_domains or "relationships" in campaign_domains:
        purpose = "supporting personal connection through an appropriate human interaction"
        subject = "two people sharing a warm but natural personal interaction"
        action = "the people communicate affection or appreciation through expression and gesture"
        environment = "an everyday social setting appropriate to the current relationship context"
        environment_category = "public_space"
    elif any(term in normalized for term in ("greeting", "hello", "meet", "introduc")):
        purpose = "practising an everyday greeting in a natural social interaction"
        subject = "two people greeting each other naturally"
        action = "the people begin a friendly everyday conversation"
        environment = "an open public space suitable for a casual greeting"
        environment_category = "public_space"
    elif any(term in normalized for term in ("direction", "where", "question", "ask")):
        purpose = "asking a useful everyday question"
        subject = "two people exchanging practical information"
        action = "one person asks for help while the other responds with a clear gesture"
        environment = "a readable street or public-space setting"
        environment_category = "street"
    elif "technology" in slide_domains or "technology" in campaign_domains:
        purpose = "explaining the current technology topic through a concrete interaction"
        subject = "a person interacting with one clear digital tool"
        action = "the tool supports one focused task without showing readable interface text"
        environment = "a clean digital workspace with restrained contextual objects"
        environment_category = "digital"
    elif "fitness" in slide_domains or "fitness" in campaign_domains:
        purpose = "supporting the current fitness idea through physical activity"
        subject = "a person performing one clear exercise movement"
        action = "the movement demonstrates the current practical fitness point"
        environment = "an appropriate gym or outdoor training setting"
        environment_category = "outdoors"
    elif "property" in slide_domains or "property" in campaign_domains:
        purpose = "supporting the current property idea with a concrete home context"
        subject = "a person considering one clear home or property detail"
        action = "the person evaluates the space through one practical action"
        environment = "a believable home or property setting"
        environment_category = "home"
    else:
        purpose = "supporting the current slide within the current semantic domain"
        subject = f"a concrete subject belonging to the current {campaign_grounding['semantic_domain']} domain"
        action = "the subject performs one clear action that supports the current slide meaning"
        environment = f"a recognizable {campaign_grounding['semantic_domain']} environment"
        environment_category = "neutral"
    if role == "cover":
        purpose = f"introducing {campaign_grounding['campaign_subject']}"
    elif role == "cta":
        purpose = "closing the current campaign with a restrained practical next step"
        subject = "one quiet domain-relevant environmental or object detail"
        action = "the scene settles into a calm visual release with generous negative space"
        if not restaurant_scope:
            environment = "a calm neutral campaign setting with generous negative space"
            environment_category = "neutral"
    return {
        "slide_purpose": purpose,
        "scene_subject": subject,
        "scene_action": action,
        "scene_environment": environment,
        "environment_category": environment_category,
    }


def _scene_variety_plan(slides, presentations, campaign_grounding):
    """Assign bounded, request-local storytelling treatments without another model."""
    plan = []
    fallback_modes = ("interaction", "detail", "action", "object_focus", "environmental")
    fallback_shots = ("medium", "close", "over_shoulder", "top_down", "eye_level")
    for index, (slide, presentation) in enumerate(zip(slides, presentations)):
        role = presentation["role"]
        grounding = _slide_grounding(slide, campaign_grounding, role)
        purpose = grounding["slide_purpose"].lower()
        action = grounding["scene_action"].lower()
        slide_semantic = " ".join(
            str(slide.get(field) or "") for field in ("title", "body", "visual")
        ).lower()
        semantic = f"{purpose} {action} {slide_semantic}"
        essential = False
        if role == "cover":
            mode, shot, subject = "establishing", "wide", "environment"
        elif role == "cta":
            mode, shot, subject = "environmental", "eye_level", "environment"
        elif any(term in semantic for term in ("bill", "payment", "receipt")):
            mode, shot, subject, essential = "object_focus", "close", "document_object", True
        elif "menu" in semantic:
            mode, shot, subject = "over_shoulder", "over_shoulder", "document_object"
        elif any(term in semantic for term in ("food", "meal", "dish", "diet", "vegetarian")):
            mode, shot, subject = "detail", "top_down", "food_object"
        elif any(term in semantic for term in ("speak", "conversation", "request", "order")):
            mode, shot, subject = "interaction", "medium", "person_interaction"
        elif "technology" in campaign_grounding["semantic_domain"]:
            mode, shot, subject = "object_focus", "close", "device"
        else:
            mode = fallback_modes[index % len(fallback_modes)]
            shot = fallback_shots[index % len(fallback_shots)]
            subject = "abstract_symbol" if mode == "symbolic" else "focal_object"
        if plan and not essential:
            previous = plan[-1]
            if (mode, shot, subject) == (
                previous["scene_mode"], previous["shot_type"], previous["subject_category"]
            ):
                mode = fallback_modes[index % len(fallback_modes)]
                shot = fallback_shots[(index + 1) % len(fallback_shots)]
                if subject == previous["subject_category"]:
                    subject = "hands_action" if subject == "person_interaction" else "focal_object"
        plan.append({
            "semantic_role": (
                "campaign_cover" if role == "cover" else
                "closing" if role == "cta" else "teaching"
            ),
            "semantic_purpose": grounding["slide_purpose"],
            "scene_mode": mode,
            "shot_type": shot,
            "subject_category": subject,
            "environment_category": grounding["environment_category"],
            "semantic_repetition_required": essential,
        })
    return plan


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


class CarouselStoryError(ValueError):
    """A bounded structural failure that never contains customer copy."""

    REASONS = {
        "missing_campaign_cover", "cover_is_teaching", "teaching_unit_overload",
        "closing_unit_overload", "visual_budget_exceeded",
        "multiple_primary_headings", "story_structure_invalid",
    }

    def __init__(self, reason, *, slide_index=0, story_role="unknown"):
        self.reason = reason if reason in self.REASONS else "story_structure_invalid"
        self.slide_index = slide_index
        self.story_role = story_role
        super().__init__(self.reason)


def _story_reject(reason, *, slide_index=0, story_role="unknown"):
    raise CarouselStoryError(
        reason, slide_index=slide_index, story_role=story_role
    )


def _visible_story_blocks(slide):
    return sum(bool(str(slide.get(field) or "").strip()) for field in (
        "title", "body", "cta", "eyebrow",
    ))


def _story_characters(slide):
    return sum(len(str(slide.get(field) or "").strip()) for field in (
        "title", "body", "cta", "eyebrow",
    ))


def _is_campaign_cover(slide, campaign_grounding):
    if slide.get("phrase_pairs") or slide.get("layout_role") in {"phrase", "cta"}:
        return False
    if _visible_story_blocks(slide) > 2 or _story_characters(slide) > 140:
        return False
    domains = set(campaign_grounding["semantic_domain"].split(" + "))
    if "language_learning" not in domains:
        return True
    normalized = " ".join(
        str(slide.get(field) or "") for field in ("title", "body", "visual")
    ).lower()
    campaign_terms = {
        "language", "learn", "phrase", "phrases", "polish", "vocabulary",
        "conversation", "speak", "restaurant", "travel", "workplace",
    }
    return any(re.search(rf"\b{term}\w*\b", normalized, re.UNICODE) for term in campaign_terms)


def _validate_carousel_story(
    slides, campaign_grounding, *, enforce_visual_budget=True
):
    """Normalize only safe ordering/roles, then enforce one semantic job per slide."""
    if not 2 <= len(slides) <= CONTENT_PACK_CAROUSEL_MAX_SLIDES:
        _story_reject("story_structure_invalid")
    domains = set(campaign_grounding["semantic_domain"].split(" + "))
    language_learning = "language_learning" in domains
    if not _is_campaign_cover(slides[0], campaign_grounding):
        later_cover = next(
            (
                index for index, slide in enumerate(slides[1:], start=1)
                if _is_campaign_cover(slide, campaign_grounding)
            ),
            None,
        )
        if later_cover is None:
            _story_reject(
                "cover_is_teaching" if slides[0].get("phrase_pairs") else "missing_campaign_cover",
                slide_index=1, story_role="campaign_cover",
            )
        slides = [slides[later_cover], *slides[:later_cover], *slides[later_cover + 1:]]

    normalized = []
    for index, original in enumerate(slides):
        slide = dict(original)
        phrase_pairs = tuple(slide.get("phrase_pairs") or ())
        is_final = index == len(slides) - 1
        if index == 0:
            story_role = "campaign_cover"
        elif is_final and slide.get("layout_role") == "cta":
            story_role = "closing"
        elif phrase_pairs:
            story_role = "teaching"
        elif is_final:
            story_role = "takeaway"
        else:
            story_role = "development"
        slide["story_role"] = story_role

        if any(not phrase or not translation for phrase, translation in phrase_pairs):
            _story_reject("story_structure_invalid", slide_index=index + 1, story_role=story_role)
        if language_learning and story_role == "teaching":
            if len(phrase_pairs) > 2:
                _story_reject("teaching_unit_overload", slide_index=index + 1, story_role=story_role)
            if len(phrase_pairs) == 2 and any(
                _copy_word_count(phrase) > 5 or _copy_word_count(translation) > 6
                for phrase, translation in phrase_pairs
            ):
                _story_reject("teaching_unit_overload", slide_index=index + 1, story_role=story_role)
            if phrase_pairs and slide.get("cta"):
                _story_reject("teaching_unit_overload", slide_index=index + 1, story_role=story_role)
        if story_role in {"closing", "takeaway"} and phrase_pairs:
            _story_reject("closing_unit_overload", slide_index=index + 1, story_role=story_role)
        if "\n" in str(slide.get("title") or "") and not phrase_pairs:
            _story_reject(
                "multiple_primary_headings",
                slide_index=index + 1,
                story_role=story_role,
            )

        limits = {
            "campaign_cover": (2, 140),
            "teaching": (3, 240),
            "development": (3, 280),
            "takeaway": (3, 180),
            "closing": (3, 180),
        }
        max_blocks, max_characters = limits[story_role]
        if enforce_visual_budget and (
            _visible_story_blocks(slide) > max_blocks
            or _story_characters(slide) > max_characters
        ):
            _story_reject("visual_budget_exceeded", slide_index=index + 1, story_role=story_role)
        normalized.append(slide)
    return normalized


def _repair_preserves_phrase_pairs(original_slides, repaired_slides):
    def pairs(slides):
        return Counter(
            pair
            for slide in slides
            for pair in tuple(slide.get("phrase_pairs") or ())
        )

    return pairs(original_slides) == pairs(repaired_slides)


def _reject_carousel_copy(
    reason, *, slide_index, role, value, treatment=None, layout=None,
    measured_lines=None, font_size=None, structure_reason=None,
):
    word_count = _copy_word_count(value)
    character_count = len((value or "").strip())
    logger.warning(
        "carousel_copy_quality_rejected slide_index=%s role=%s content_type=%s word_count=%s "
        "character_count=%s treatment=%s layout=%s measured_lines=%s "
        "font_size=%s reason=%s structure_reason=%s",
        slide_index,
        role,
        "language_learning" if role == "phrase" else "general",
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


def _resolve_design_style(requested, slides):
    if requested in DESIGN_MANAGER_STYLES - {"auto"}:
        return requested
    corpus = " ".join(str(slide.get(key) or "") for slide in slides for key in ("title", "body", "visual")).lower()
    tendencies = (
        ("photorealistic", ("founder", "lifestyle", "human story", "emotion", "community")),
        ("three_d_clay", ("technology", "system", "product", "software", "platform")),
        ("bold_graphic", ("warning", "mistake", "myth", "list", "stop", "strongest")),
        ("collage_magazine", ("culture", "creative", "story", "history", "expressive")),
        ("minimal_premium", ("saas", "professional", "strategy", "leadership", "business")),
    )
    return next((style for style, terms in tendencies if any(term in corpus for term in terms)), "editorial_illustration")


def _resolve_palette(requested, resolved_style, slides):
    if requested in DESIGN_MANAGER_PALETTES - {"auto"}:
        return requested
    defaults = {
        "editorial_illustration": "warm_sunset", "minimal_premium": "monochrome",
        "photorealistic": "earth_and_cream", "three_d_clay": "soft_pastel",
        "bold_graphic": "electric", "collage_magazine": "warm_sunset",
    }
    return defaults[resolved_style]


def _campaign_art_direction(image_style, slides, design_style=None, palette=None):
    """Normalize one safe visual world for every artwork slide in the set."""
    corpus = " ".join(
        value
        for slide in slides
        for value in (slide.get("visual"), slide.get("title"))
        if isinstance(value, str)
    ).lower()
    motif = (
        "transformation"
        if any(word in corpus for word in ("transform", "adapt", "format", "platform"))
        else "insight_reveal"
        if any(word in corpus for word in ("understand", "insight", "source", "research"))
        else "human_connection"
        if any(word in corpus for word in ("people", "audience", "community"))
        else "focal_object_system"
    )
    if design_style is None and palette is None:
        return {
            "art_style": CAMPAIGN_ART_STYLES.get(image_style, "premium editorial illustration"),
            "visual_theme": "one coherent conceptual campaign",
            "palette_intent": "deep navy with controlled yellow, mint, and blue accents",
            "lighting_or_depth": "confident dimensional depth and controlled studio lighting" if image_style in {"realistic", "pixar"} else "layered editorial depth with crisp focal separation",
            "texture_intent": "soft matte dimensional surfaces with restrained tactile detail" if image_style in {"realistic", "pixar"} else "layered paper and editorial cutout surfaces with controlled grain",
            "shape_language": "bold simple silhouettes with consistent rounded geometry",
            "composition_energy": "confident asymmetry with one immediate focal hierarchy",
            "campaign_motif": motif,
            "image_detail_level": "editorial detail legible at mobile thumbnail size",
        }
    requested_style = design_style if design_style in DESIGN_MANAGER_STYLES else "auto"
    requested_palette = palette if palette in DESIGN_MANAGER_PALETTES else "auto"
    resolved_style = _resolve_design_style(requested_style, slides)
    resolved_palette = _resolve_palette(requested_palette, resolved_style, slides)
    art_style, depth, texture, shapes = STYLE_GRAMMARS[resolved_style]
    return {
        "resolved_style": resolved_style,
        "resolved_palette": resolved_palette,
        "art_style": art_style,
        "visual_theme": "one coherent conceptual campaign",
        "palette_intent": PALETTE_INTENTS[resolved_palette],
        "lighting_or_depth": depth,
        "texture_intent": texture,
        "shape_language": shapes,
        "composition_energy": "confident asymmetry with one immediate focal hierarchy",
        "campaign_motif": motif,
        "image_detail_level": "editorial detail legible at mobile thumbnail size",
    }


def _scene_brief(
    visual, semantic_text, treatment, visual_weight, layout, metaphor_family,
    campaign_grounding=None, slide_grounding=None, scene_plan=None,
):
    """Build a bounded, copy-safe scene specification for provider prompting."""
    normalized = " ".join(
        value for value in (visual, semantic_text) if isinstance(value, str)
    ).lower()
    category = (
        "transformation" if any(word in normalized for word in
            ("transform", "adapt", "format", "platform", "before", "after"))
        else "insight" if any(word in normalized for word in
            ("insight", "source", "research", "evidence", "understand"))
        else "conversation" if any(word in normalized for word in
            ("people", "audience", "community", "conversation", "discussion"))
        else "process" if any(word in normalized for word in
            ("step", "sequence", "stage", "process", "workflow"))
        else "content_system" if any(word in normalized for word in
            ("content", "campaign", "publish", "channel"))
        else "everyday_context"
    )
    subjects = {
        "transformation": "one oversized raw source form becoming several visibly distinct finished forms",
        "insight": "layered source fragments surrounding one clearly elevated focal discovery",
        "conversation": "distinct editorial figures gathered around one shared focal object",
        "process": "one continuous subject shown evolving through clearly related stages",
        "content_system": "one substantial source object anchoring a coordinated set of distinct outputs",
        "everyday_context": "one oversized source-relevant object in a purposeful editorial setting",
    }
    actions = {
        "transformation": "the source separates, reshapes, and resolves into differentiated outcomes",
        "insight": "the surrounding fragments are filtered back while the central discovery is revealed and elevated",
        "conversation": "the figures visibly converge, react, and contribute toward the shared focus",
        "process": "the same subject unfolds and changes from one stage into the next",
        "content_system": "the anchored source assembles the supporting outputs into one coherent system",
        "everyday_context": "the focal object is being opened, examined, or put into active use",
    }
    if treatment == "diagram":
        subject = "specific source-relevant entities arranged around one dominant entity"
        action = "directional relationships show exactly how the entities connect or influence one another"
    elif treatment == "process":
        subject = "one consistent source-relevant subject repeated only to show its sequential evolution"
        action = "the subject visibly transforms through ordered, causally connected stages"
    elif treatment == "comparison":
        subject = "two clearly different states of the same source-relevant subject"
        action = "the states diverge in one coherent split composition so the contrast is immediate"
    elif treatment == "feature_cards":
        subject = "one grouped family of source-relevant objects unified by a dominant theme object"
        action = "the supporting objects assemble around the theme without forming a dashboard or labelled cards"
    else:
        subject, action = subjects[category], actions[category]

    heavy = visual_weight == "heavy"
    medium = visual_weight == "medium"
    viewpoint = (
        "angled three-quarter editorial view" if heavy and layout in {"split_left", "hero_left"}
        else "asymmetric side view" if heavy
        else "slight top-down editorial view" if medium and treatment in {"process", "feature_cards"}
        else "wide conceptual editorial scene" if medium
        else "centred poster-like view"
    )
    if scene_plan:
        viewpoint = {
            "wide": "wide establishing view",
            "medium": "medium eye-level view",
            "close": "close detail view",
            "macro_detail": "macro detail view",
            "over_shoulder": "over-the-shoulder view",
            "top_down": "top-down view",
            "low_angle": "restrained low-angle view",
            "eye_level": "calm eye-level view",
        }[scene_plan["shot_type"]]
    return {
        "scene_subject": slide_grounding["scene_subject"] if slide_grounding else subject,
        "scene_action": slide_grounding["scene_action"] if slide_grounding else action,
        "foreground_elements": (
            "one partially cropped supporting element entering the artwork zone to establish scale"
            if heavy else "none required" if not medium else
            "at most one supporting element overlapping the artwork-zone edge"
        ),
        "midground_elements": "the primary subject and its clearest semantic action",
        "background_environment": slide_grounding["scene_environment"] if slide_grounding else (
            "a simplified contextual environment with layered planes, never a blank framed box"
            if heavy or medium else "a quiet campaign-colour field with minimal context"
        ),
        "spatial_relationship": (
            "one dominant subject, with one to three smaller supports visibly acting on or responding to it"
            if heavy else "one clear subject with one subordinate context layer" if medium else
            "one simple subject with no competing elements"
        ),
        "camera_or_viewpoint": viewpoint,
        "depth_strategy": (
            "foreground overlap, occlusion, scale contrast, layered planes, and controlled shadows"
            if heavy else "one overlapping context plane with clear depth separation" if medium else
            "mostly flat separation with restrained shadow depth"
        ),
        "cropping_strategy": (
            "oversize and deliberately crop the focal subject at one or two artwork-zone edges"
            if heavy else "use a confident close crop while keeping the whole action legible" if medium else
            "keep the simple subject fully legible with comfortable artwork-zone space"
        ),
        "material_or_surface_language": "use the campaign material language exactly; do not switch medium",
        "focal_scale": (
            "dominant and large, confidently filling the artwork zone" if heavy else
            "substantial and balanced within the artwork zone" if medium else
            "restrained but intentional within the artwork zone"
        ),
        "supporting_element_limit": "one to three" if heavy else "zero to two" if medium else "zero or one",
        "negative_space_intent": (
            "protect only the separate text-safe zone; create no large dead margins inside the artwork zone"
            if heavy else "protect the text-safe zone while keeping balanced artwork occupancy" if medium else
            "use intentional breathing room inside the artwork zone without shrinking into an icon"
        ),
        "metaphor_family": metaphor_family or "focal_object",
        **({
            "scene_mode": scene_plan["scene_mode"],
            "shot_type": scene_plan["shot_type"],
            "subject_category": scene_plan["subject_category"],
        } if scene_plan else {}),
    }


def _carousel_presentations(slides):
    presentations = []
    for index, slide in enumerate(slides):
        role = "cover" if index == 0 else slide["layout_role"]
        semantic_text = " ".join(
            value for value in (slide["title"], slide["body"]) if value
        )
        treatment = _select_visual_treatment(slide["visual"], role, semantic_text)
        metaphor = _visual_metaphor(slide)
        if role == "cover" and treatment == "diagram":
            treatment = "visual_focus"
            metaphor = "transformation"
        if treatment == "diagram" and metaphor == "distinct_object":
            metaphor = "node_network"
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
        layout = _select_layout_variant(
            role, index, treatment, slide["title"]
        )
        if visual_weight == "heavy" and layout == "editorial_statement":
            layout = "visual_focus"
        typography_presentation = select_typography_presentation(
            role, treatment, visual_weight
        )
        presentations.append(
            {
                "role": role,
                "treatment": treatment,
                "layout": layout,
                "semantic_text": semantic_text,
                "visual_weight": visual_weight,
                "typography_presentation": typography_presentation,
                "editorial_composition": select_editorial_composition(
                    role, treatment, visual_weight, layout,
                    typography_presentation, slide["title"],
                ),
                "metaphor": metaphor,
                "artwork_required": treatment != "typography_only",
                "furniture": (
                    "dual_rail" if role == "cover" else
                    "single_rail" if role == "cta" else
                    "none" if treatment == "visual_focus" else
                    "corner_marker" if index % 2 else "framed_edge"
                ),
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
            current["metaphor"] = "focal_object"
        elif (
            current["metaphor"] == "node_network"
            and any(
                item["metaphor"] == "node_network"
                for item in presentations[:index]
            )
            and not genuine_sequence
        ):
            current["treatment"] = "visual_focus"
            current["metaphor"] = "focal_object"
        current["layout"] = _select_layout_variant(
            current["role"], index, current["treatment"], slides[index]["title"]
        )
        if (
            current["visual_weight"] == "heavy"
            and current["layout"] == "editorial_statement"
        ):
            current["layout"] = "visual_focus"
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
    for index, presentation in enumerate(presentations):
        presentation["typography_presentation"] = select_typography_presentation(
            presentation["role"], presentation["treatment"],
            presentation["visual_weight"],
        )
        presentation["editorial_composition"] = select_editorial_composition(
            presentation["role"], presentation["treatment"],
            presentation["visual_weight"], presentation["layout"],
            presentation["typography_presentation"], slides[index]["title"],
        )
        if index:
            previous = presentations[index - 1]
            semantic = presentation["semantic_text"].lower()
            genuine_split = (
                presentation["treatment"] in {"comparison", "process"}
                or any(word in semantic for word in ("versus", "compare", "before", "after"))
            )
            mirrored_pair = (
                {previous["layout"], presentation["layout"]}
                == {"split_left", "split_right"}
                and previous["treatment"] == presentation["treatment"]
                and previous["visual_weight"] != "heavy"
                and presentation["visual_weight"] != "heavy"
            )
            if mirrored_pair and not genuine_split:
                presentation["editorial_composition"] = "vertical_editorial"
                presentation["furniture"] = "none"
        presentation["optical_lock"] = select_optical_lock(
            presentation["editorial_composition"], presentation["layout"],
            presentation["visual_weight"],
        )
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
            typography_presentation=presentation["typography_presentation"],
            editorial_composition=presentation["editorial_composition"],
            optical_lock=presentation["optical_lock"],
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
    text_inviting_scenes = (
        (("document", "article", "workbook", "paper"),
         "blank layered paper objects with no printing, glyphs, handwriting, or letter-like marks"),
        (("screen", "interface", "dashboard", "phone", "mobile", "app"),
         "an abstract glowing display surface containing only plain colour fields and geometric shapes, with no UI, icons, numbers, or glyphs"),
        (("book", "notebook", "book page"),
         "a closed book or blank-page book object with no printing, handwriting, numbers, or letter-like marks"),
        (("poster", "sign", "signage"),
         "a blank graphic surface made only from colour, shape, texture, and imagery, with no lettering or symbols"),
        (("menu", "lesson card", "language lesson card"),
         "a blank card object supported by an everyday conversation scene, with no written content or glyphs"),
    )
    for keywords, direction in text_inviting_scenes:
        if any(keyword in normalized for keyword in keywords):
            directions.append(direction)
            break
    scene_categories = (
        (("instagram",),
         "a bold image frame with layered text-free media cards and a strong visual focal area"),
        (("linkedin",),
         "a text-free editorial document metaphor made from refined blank layered paper shapes, with no printing or glyphs"),
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
         "a culturally relevant everyday conversation supporting language learning through people, gesture, objects, and a speech or sound metaphor, with no written language"),
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
         "a display-shaped secondary prop containing only plain colour fields and geometric shapes, with no UI, icons, numbers, or glyphs"),
    )
    for keywords, direction in scene_categories:
        if any(keyword in normalized for keyword in keywords):
            directions.append(direction)
        if len(directions) == 2:
            break
    return "; ".join(dict.fromkeys(directions)) if directions else None


def _build_slide_background_prompt(
    styled_image_prompt,
    slide_index,
    visual=None,
    layout_role=None,
    layout_variant=None,
    visual_treatment=None,
    semantic_text=None,
    visual_weight="medium",
    metaphor_family=None,
    campaign_direction=None,
    campaign_grounding=None,
    slide_grounding=None,
    scene_plan=None,
):
    if visual_treatment == "typography_only":
        return None
    visual_concept = SLIDE_VISUAL_CONCEPTS[slide_index]
    safe_visual_direction = _safe_visual_direction(visual, semantic_text)
    if metaphor_family == "focal_object":
        safe_visual_direction = (
            "one source-specific focal object with a strong silhouette, avoiding "
            "nodes, branches, arrows, networks, and repeated diagram geometry"
        )
    elif metaphor_family == "transformation":
        safe_visual_direction = (
            "one source-specific transformation scene with visibly distinct output "
            "forms, avoiding nodes, branches, arrows, and network geometry"
        )
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
            "Contain the scene in the bounded secondary artwork zone and reserve the "
            "dominant protected field for editorial typography."
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
    campaign_direction = campaign_direction or _campaign_art_direction("", [])
    scene = _scene_brief(
        visual, semantic_text, visual_treatment or "illustration", visual_weight,
        design_layout, metaphor_family, campaign_grounding, slide_grounding, scene_plan,
    )
    occupancy = {"heavy": "dominant", "medium": "substantial", "light": "restrained"}[
        visual_weight
    ]
    sequence_role = (
        "opening campaign hero" if role == "cover" else
        "restrained final payoff" if role == "cta" else
        "internal visual peak" if visual_weight == "heavy" else
        "editorial visual pause" if visual_weight == "light" else
        "supporting campaign progression"
    )
    weight_direction = {
        "heavy": "Fill the artwork zone confidently with a dominant subject, deeper layering, dramatic asymmetry, and minimal dead space.",
        "medium": "Use balanced, substantial occupancy with moderate scene depth and one context layer.",
        "light": "Use a simple restrained scene with intentional breathing room and no unnecessary complexity.",
    }[visual_weight]
    treatment_direction = {
        "visual_focus": "Make one dominant subject and its action unmistakable; use large scale, a strong crop, and minimal competition.",
        "illustration": "Stage a contextual editorial scene with a subject, action, environment, and visible depth.",
        "diagram": "Show source-relevant entities, their directional relationship, and one focal hierarchy; use diagram logic only because the relationship requires it.",
        "process": "Show the same subject evolving through visibly related sequential stages, not disconnected symbols.",
        "comparison": "Show two distinct states in one coherent split conceptual scene; do not duplicate the same image.",
        "feature_cards": "Support the grouped theme with objects, not fake UI, dashboards, cards containing text, or labelled panels.",
    }.get(visual_treatment or "illustration")
    heavy_ban = "" if visual_weight != "heavy" else """
- no tiny central icon, three small rounded rectangles, simple node cluster, logo-like mark,
  isolated symbol, generic SaaS UI cards, evenly spaced widgets, empty framed box, or clip-art composition"""
    grounding_section = "" if not campaign_grounding or not slide_grounding else f"""
0. CURRENT SEMANTIC GROUNDING — HIGHEST PRIORITY
- current campaign subject: {campaign_grounding["campaign_subject"]}
- current semantic domain: {campaign_grounding["semantic_domain"]}
- current campaign goal: {campaign_grounding["campaign_goal"]}
- current slide purpose: {slide_grounding["slide_purpose"]}
- current scene action: {slide_grounding["scene_action"]}
- subject, environment, and action must remain in this current domain
- style, palette, composition, and motif variety may change presentation only;
  they must never replace or weaken the current subject, environment, or action
"""
    variety_section = "" if not scene_plan else (
        "VISUAL STORYTELLING — scene mode: " + scene_plan["scene_mode"]
        + "; shot: " + scene_plan["shot_type"]
        + "; subject category: " + scene_plan["subject_category"]
        + ". This may vary presentation only, never semantic grounding."
    )
    return f"""
Create one text-free artwork scene for a cohesive Instagram carousel.
{grounding_section}
{variety_section}

1. CAMPAIGN STYLE LOCK
- the normalized campaign visual world below controls the medium for every slide
- keep that same medium, colour treatment, lighting treatment, visual polish, and brand mood
- the slide-specific concept changes only the scene, subjects, props, framing, and composition
- do not let the slide-specific concept introduce a different visual medium or art style

- art style: {campaign_direction["art_style"]}
- visual theme: {campaign_direction["visual_theme"]}
- palette intent: {campaign_direction["palette_intent"]}
- lighting/depth: {campaign_direction["lighting_or_depth"]}
- texture: {campaign_direction["texture_intent"]}
- shape language: {campaign_direction["shape_language"]}
- composition energy: {campaign_direction["composition_energy"]}
- shared campaign motif: {campaign_direction["campaign_motif"]}
- detail level: {campaign_direction["image_detail_level"]}

2. SCENE BRIEF
- main subject: {scene["scene_subject"]}
- action: {scene["scene_action"]}
- foreground: {scene["foreground_elements"]}
- midground: {scene["midground_elements"]}
- background: {scene["background_environment"]}
- spatial relationship: {scene["spatial_relationship"]}
- viewpoint: {scene["camera_or_viewpoint"]}
- depth: {scene["depth_strategy"]}
- crop: {scene["cropping_strategy"]}
- focal scale: {scene["focal_scale"]}
- supporting elements: {scene["supporting_element_limit"]}
- material continuity: {scene["material_or_surface_language"]}
- scene intent: {safe_visual_direction or visual_concept}
- sequence-specific framing: {visual_concept}

3. COMPOSITION / GEOMETRY
- slide role: {role}; sequence purpose: {sequence_role}
- treatment: {visual_treatment or "illustration"}; visual weight: {visual_weight}
- layout: {design_layout}; artwork-zone occupancy: {occupancy}
- {weight_direction}
- {treatment_direction}
- {composition_direction}
- text-safe space versus artwork space: {scene["negative_space_intent"]}
- square 1:1 format
- keep faces, facial features, and primary objects completely outside the text-safe zone

4. QUALITY BAR
- high contrast, mobile-thumbnail legibility, strong silhouette, coherent depth, and deliberate hierarchy
- preserve the campaign palette, material, edge language, lighting logic, and sophistication
- create a complete semantic scene, not placeholder geometry

 5. NEGATIVE REQUIREMENTS — ABSOLUTE TEXT-FREE POLICY
- ABSOLUTELY NO READABLE TEXT. All visible typography is added later by SMU.
- no words, letters, numbers, language characters, captions, labels, titles, or UI copy
- no signs, logos, watermarks, written language, pseudo-text, gibberish typography,
  fake handwriting, text-like decorative marks, or letter-like marks
- no readable text
- no letters
- no handwriting
- no pseudo-text
- no gibberish text
- no typography
- no captions
- no labels
- no readable logos
- no watermarks
- no text on screens
- no text on paper
- no written signs
- create only scenes, people, objects, environments, shapes, materials, visual
  metaphors, lighting, and negative space
- never infer or reproduce overlay wording, translated phrases, or teaching content
- no UI screenshots
- no generic clip-art look
- no isolated tiny object surrounded by unnecessary empty space
- screens and displays must contain only blank colour fields or abstract geometry
- paper, books, cards, posters, signs, and menus must be blank and glyph-free
- no generated app-store badges
- no readable app wordmarks
- no official platform logos or trademark-shaped icons
- no fake buttons, fake interfaces, automatic badges, slide numbers, or generic glossy SaaS dashboards{heavy_ban}
- text-free requirements override conflicting typography or signage
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
    repair_trace = uuid.uuid4().hex[:10]
    content_pack_result = request.form.get("content_pack_result", "").strip()
    image_style = request.form.get("image_style", "").strip()
    design_manager_style = request.form.get("design_manager_style")
    colour_theme = request.form.get("colour_theme")
    design_manager_style = design_manager_style.strip().lower() if design_manager_style is not None else None
    colour_theme = colour_theme.strip().lower() if colour_theme is not None else None
    if design_manager_style is not None and design_manager_style not in DESIGN_MANAGER_STYLES:
        design_manager_style = "auto"
    if colour_theme is not None and colour_theme not in DESIGN_MANAGER_PALETTES:
        colour_theme = "auto"

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
        slides, phrase_recovery_count = _recover_exact_phrase_pairs(
            carousel_idea, slides
        )
        logger.warning(
            "carousel_repair_trace trace_id=%s stage=initial_parse result=valid "
            "slide_count=%s phrase_recovery_count=%s semantic_reset=true",
            repair_trace,
            len(slides),
            phrase_recovery_count,
        )

        if len(slides) < CONTENT_PACK_CAROUSEL_MIN_SLIDES:
            flash(
                "SMU couldn't create enough carousel slides from this Content Pack. "
                "Please regenerate the Content Pack and try again.",
                "danger",
            )
            return redirect(url_for("content_pack"))

        campaign_direction = _campaign_art_direction(
            image_style, slides, design_manager_style, colour_theme
        )
        campaign_grounding = _campaign_grounding(slides, campaign_direction)
        logger.warning(
            "carousel_repair_trace trace_id=%s stage=campaign_grounding "
            "result=ready campaign_domain=%s semantic_reset=true",
            repair_trace,
            campaign_grounding["semantic_domain"],
        )
        try:
            slides = _validate_carousel_story(
                slides, campaign_grounding,
                enforce_visual_budget=image_style == "viral_carousel",
            )
            logger.warning(
                "carousel_repair_trace trace_id=%s stage=initial_validation "
                "result=valid repair_attempted=false",
                repair_trace,
            )
        except CarouselStoryError as initial_error:
            logger.warning(
                "carousel_repair_trace trace_id=%s stage=initial_validation "
                "result=invalid reason=%s slide_index=%s story_role=%s",
                repair_trace,
                initial_error.reason,
                initial_error.slide_index,
                initial_error.story_role,
            )
            if initial_error.reason not in REPAIRABLE_STORY_REASONS:
                logger.warning(
                    "carousel_repair_trace trace_id=%s stage=repair_eligibility "
                    "result=ineligible reason=%s",
                    repair_trace,
                    initial_error.reason,
                )
                raise
            logger.warning(
                "carousel_repair_trace trace_id=%s stage=repair_eligibility "
                "result=eligible reason=%s",
                repair_trace,
                initial_error.reason,
            )
            original_slides = slides
            repair_min_slides, repair_max_slides = _repair_cardinality_bounds(
                initial_error.reason, len(original_slides)
            )
            try:
                try:
                    logger.warning(
                        "carousel_repair_trace trace_id=%s stage=repair_call "
                        "result=started",
                        repair_trace,
                    )
                    repaired_idea = _content_pack_helper("repair_carousel_structure")(
                        carousel_idea,
                        failure_reason=initial_error.reason,
                        semantic_domain=campaign_grounding["semantic_domain"],
                        slide_index=initial_error.slide_index,
                        story_role=initial_error.story_role,
                        original_slide_count=len(original_slides),
                        minimum_slide_count=repair_min_slides,
                        maximum_slide_count=repair_max_slides,
                    )
                except CarouselStructureRepairError as repair_error:
                    repair_result = {
                        "provider_unavailable": "provider_unavailable",
                        "provider_timeout": "timeout",
                        "invalid_repair_output": "malformed",
                    }.get(repair_error.reason, "exception")
                    logger.warning(
                        "carousel_repair_trace trace_id=%s stage=repair_call "
                        "result=%s reason=%s",
                        repair_trace,
                        repair_result,
                        repair_error.reason,
                    )
                    raise
                except Exception:
                    logger.warning(
                        "carousel_repair_trace trace_id=%s stage=repair_call "
                        "result=exception reason=unexpected_exception",
                        repair_trace,
                    )
                    raise
                logger.warning(
                    "carousel_repair_trace trace_id=%s stage=repair_call result=completed",
                    repair_trace,
                )
                if not isinstance(repaired_idea, str) or sum(
                    bool(SLIDE_MARKER_RE.match(line.strip()))
                    for line in repaired_idea.splitlines()
                ) < CONTENT_PACK_CAROUSEL_MIN_SLIDES:
                    logger.warning(
                        "carousel_repair_trace trace_id=%s stage=repair_parse "
                        "result=malformed reason=insufficient_slide_markers",
                        repair_trace,
                    )
                    raise ValueError("invalid_repair_output")
                try:
                    repaired_parsed = _parse_content_pack_carousel_slides(repaired_idea)
                except Exception:
                    logger.warning(
                        "carousel_repair_trace trace_id=%s stage=repair_parse "
                        "result=parse_failed reason=parser_exception",
                        repair_trace,
                    )
                    raise
                cardinality_valid = repair_min_slides <= len(
                    repaired_parsed
                ) <= min(repair_max_slides, CONTENT_PACK_CAROUSEL_MAX_SLIDES)
                logger.warning(
                    "carousel_repair_trace trace_id=%s stage=repair_cardinality "
                    "result=%s repair_original_slide_count=%s "
                    "repair_output_slide_count=%s minimum_slide_count=%s "
                    "maximum_slide_count=%s",
                    repair_trace,
                    "pass" if cardinality_valid else "fail",
                    len(original_slides),
                    len(repaired_parsed),
                    repair_min_slides,
                    repair_max_slides,
                )
                if not cardinality_valid:
                    raise ValueError("repair_cardinality_invalid")
                logger.warning(
                    "carousel_repair_trace trace_id=%s stage=repair_parse "
                    "result=valid slide_count=%s",
                    repair_trace,
                    len(repaired_parsed),
                )
                repaired_slides = _normalize_content_pack_carousel_slides(repaired_parsed)
                repaired_slides, repaired_recovery_count = _recover_exact_phrase_pairs(
                    repaired_idea, repaired_slides
                )
                if repaired_recovery_count:
                    logger.warning(
                        "carousel_repair_trace trace_id=%s stage=phrase_recovery "
                        "result=completed phrase_recovery_count=%s",
                        repair_trace,
                        repaired_recovery_count,
                    )
                original_pair_count = sum(
                    len(tuple(slide.get("phrase_pairs") or ()))
                    for slide in original_slides
                )
                repaired_pair_count = sum(
                    len(tuple(slide.get("phrase_pairs") or ()))
                    for slide in repaired_slides
                )
                if not _repair_preserves_phrase_pairs(original_slides, repaired_slides):
                    logger.warning(
                        "carousel_repair_trace trace_id=%s stage=pair_preservation "
                        "result=fail original_pair_count=%s repaired_pair_count=%s",
                        repair_trace,
                        original_pair_count,
                        repaired_pair_count,
                    )
                    raise ValueError("translation_changed")
                logger.warning(
                    "carousel_repair_trace trace_id=%s stage=pair_preservation "
                    "result=pass original_pair_count=%s repaired_pair_count=%s",
                    repair_trace,
                    original_pair_count,
                    repaired_pair_count,
                )
                campaign_direction = _campaign_art_direction(
                    image_style, repaired_slides,
                    design_manager_style, colour_theme,
                )
                campaign_grounding = _campaign_grounding(
                    repaired_slides, campaign_direction
                )
                slides = _validate_carousel_story(
                    repaired_slides, campaign_grounding,
                    enforce_visual_budget=image_style == "viral_carousel",
                )
            except CarouselStoryError as repaired_error:
                logger.warning(
                    "carousel_repair_trace trace_id=%s stage=repaired_validation "
                    "result=invalid reason=%s slide_index=%s story_role=%s",
                    repair_trace,
                    repaired_error.reason,
                    repaired_error.slide_index,
                    repaired_error.story_role,
                )
                raise
            except Exception:
                raise initial_error from None
            logger.warning(
                "carousel_repair_trace trace_id=%s stage=repaired_validation "
                "result=valid slide_count=%s",
                repair_trace,
                len(slides),
            )
        presentations = _carousel_presentations(slides)
        scene_plan = _scene_variety_plan(slides, presentations, campaign_grounding)
        logger.warning(
            "carousel_design_plan slide_count=%s scene_modes=%s environment_plan=%s",
            len(slides),
            ",".join(item["scene_mode"] for item in scene_plan),
            ",".join(item["environment_category"] for item in scene_plan),
        )
        if image_style == "viral_carousel":
            preflight_started = perf_counter()
            _validate_viral_carousel_copy(slides, presentations)
            logger.info(
                "carousel_preflight_complete slide_count=%s duration_ms=%.1f",
                len(slides),
                (perf_counter() - preflight_started) * 1000,
            )

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
            slide_grounding = _slide_grounding(
                slide, campaign_grounding, presentation["role"]
            )
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
                presentation["metaphor"],
                campaign_direction,
                campaign_grounding,
                slide_grounding,
                scene_plan[index],
            )
            if background_prompt is None:
                background_prompt = TYPOGRAPHY_ONLY_BACKGROUND
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
                furniture_variant=presentation["furniture"],
                metaphor_family=presentation["metaphor"],
                typography_presentation=presentation["typography_presentation"],
                editorial_composition=presentation["editorial_composition"],
                optical_lock=presentation["optical_lock"],
                campaign_style=campaign_direction.get("resolved_style"),
                campaign_palette=campaign_direction.get("resolved_palette"),
                campaign_grounding={
                    "campaign_subject": campaign_grounding["campaign_subject"],
                    "semantic_domain": campaign_grounding["semantic_domain"],
                    "slide_purpose": slide_grounding["slide_purpose"],
                    "scene_action": slide_grounding["scene_action"],
                },
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
        logger.warning(
            "carousel_repair_trace trace_id=%s stage=request_outcome "
            "result=created slide_count=%s",
            repair_trace,
            len(slides),
        )

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

    except CarouselStoryError as exc:
        db.session.rollback()
        logger.warning(
            "carousel_repair_trace trace_id=%s stage=request_outcome "
            "result=rejected reason=%s",
            repair_trace,
            exc.reason,
        )
        logger.warning(
            "carousel_story_rejected reason=%s slide_index=%s story_role=%s",
            exc.reason,
            exc.slide_index,
            exc.story_role,
        )
        if exc.reason == "visual_budget_exceeded":
            flash(
                "We couldn't create this carousel because one slide was too "
                "text-heavy. Please regenerate the Content Pack.",
                "danger",
            )
        else:
            flash(
                "We couldn't turn that result into a clean carousel. "
                "Please generate the Content Pack again.",
                "danger",
            )
        return redirect(url_for("content_pack"))
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
