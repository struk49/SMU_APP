"""Strict provider schema and deterministic editable Content Pack serialization."""

import json

from pydantic import BaseModel, ConfigDict, Field

from smu_core.services.generation_contract import GenerationContractError


CONTENT_PACK_SECTION_NAMES = (
    "INSTAGRAM_CAPTION", "FACEBOOK_POST", "CAROUSEL_IDEA",
    "PINTEREST_PIN_TITLE", "PINTEREST_PIN_DESCRIPTION", "REDDIT_POST",
    "X_POST", "LINKEDIN_POST", "IMAGE_PROMPT", "HASHTAGS",
)
SLIDE_TEXT_FIELDS = (
    ("title", "Title"), ("subtitle", "Subtitle"), ("body", "Body"),
    ("tip", "Tip"), ("cta", "CTA"), ("visual", "Visual"),
    ("visual_weight", "Visual Weight"), ("eyebrow", "Eyebrow"),
    ("emphasis", "Emphasis"),
)


class StrictObject(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PhrasePair(StrictObject):
    phrase: str
    translation: str


class Slide(StrictObject):
    title: str
    subtitle: str | None
    body: str | None
    phrase_pairs: list[PhrasePair]
    tip: str | None
    cta: str | None
    visual: str | None
    visual_weight: str | None
    eyebrow: str | None
    emphasis: str | None


class ContentPackPayload(StrictObject):
    INSTAGRAM_CAPTION: str
    FACEBOOK_POST: str
    CAROUSEL_IDEA: list[Slide] = Field(min_length=2, max_length=6)
    PINTEREST_PIN_TITLE: str
    PINTEREST_PIN_DESCRIPTION: str
    REDDIT_POST: str
    X_POST: str
    LINKEDIN_POST: str
    IMAGE_PROMPT: str
    HASHTAGS: str


CONTENT_PACK_RESPONSE_FORMAT = {
    "type": "json_schema", "name": "content_pack", "strict": True,
    "schema": ContentPackPayload.model_json_schema(),
}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("invalid_json_constant")


def serialize_content_pack_response(response):
    """Reject refusals/partial or invalid JSON; never salvage or deduplicate it."""
    if getattr(response, "status", None) != "completed":
        raise GenerationContractError("incomplete_provider_response")
    for item in getattr(response, "output", ()) or ():
        for part in getattr(item, "content", ()) or ():
            if getattr(part, "type", None) == "refusal":
                raise GenerationContractError("provider_refusal")
    raw = getattr(response, "output_text", None)
    if not isinstance(raw, str) or not raw.strip():
        raise GenerationContractError("empty_generation_output")
    try:
        data = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        pack = ContentPackPayload.model_validate(data)
    except ValueError as exc:
        raise GenerationContractError("invalid_structured_content_pack") from exc

    sections = []
    for section in CONTENT_PACK_SECTION_NAMES:
        value = getattr(pack, section)
        if section == "CAROUSEL_IDEA":
            blocks = []
            for number, slide in enumerate(value, 1):
                lines = [f"Slide {number}:"]
                for name, label in SLIDE_TEXT_FIELDS:
                    field = getattr(slide, name)
                    if field is not None:
                        if not field.strip():
                            raise GenerationContractError("empty_slide_field")
                        lines.append(f"{label}: {field}")
                    if name == "body":
                        for pair in slide.phrase_pairs:
                            lines.extend((f"Phrase: {pair.phrase}", f"Translation: {pair.translation}"))
                blocks.append("\n".join(lines))
            value = "\n".join(blocks)
        sections.append(f"{section}:\n{value}")
    return "\n\n".join(sections)
