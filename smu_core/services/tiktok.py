"""TikTok-specific content repurposing helpers."""

from dataclasses import dataclass
import json
import logging


logger = logging.getLogger(__name__)
REPURPOSE_GENERATION_VERSION = "structured-v1"


class TikTokRepurposeError(Exception):
    """Raised when TikTok repurposing output cannot be safely used."""


@dataclass(frozen=True)
class TikTokRepurposeResult:
    instagram_caption: str
    facebook_caption: str
    carousel_idea: str
    image_prompt: str
    hashtags: str


EXPECTED_REPURPOSE_FIELDS = (
    "instagram_caption",
    "facebook_caption",
    "carousel_idea",
    "image_prompt",
    "hashtags",
)

REPURPOSE_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": list(EXPECTED_REPURPOSE_FIELDS),
    "properties": {
        "instagram_caption": {
            "type": "string",
            "description": "A polished Instagram caption.",
        },
        "facebook_caption": {
            "type": "string",
            "description": "A polished Facebook caption.",
        },
        "carousel_idea": {
            "type": "string",
            "description": (
                "A slide-by-slide carousel idea, formatted as Slide 1, Slide 2, "
                "and so on."
            ),
        },
        "image_prompt": {
            "type": "string",
            "description": "A concise image-generation prompt for the content.",
        },
        "hashtags": {
            "type": "string",
            "description": "Relevant social media hashtags.",
        },
    },
}


def _object_type_name(value):
    return type(value).__name__


def _payload_field_names(payload):
    if isinstance(payload, TikTokRepurposeResult):
        return list(EXPECTED_REPURPOSE_FIELDS)

    if isinstance(payload, dict):
        return sorted(str(key) for key in payload.keys())

    return []


def _payload_field_lengths(payload):
    values = {}

    if isinstance(payload, TikTokRepurposeResult):
        for field in EXPECTED_REPURPOSE_FIELDS:
            values[field] = len(getattr(payload, field, "") or "")
        return values

    if isinstance(payload, dict):
        for field in EXPECTED_REPURPOSE_FIELDS:
            value = payload.get(field)
            values[field] = len(value) if isinstance(value, str) else None
        return values

    return values


def _strip_json_code_fence(text):
    stripped = text.strip()

    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()

    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()

    return stripped


def parse_repurpose_result(raw_response):
    if not isinstance(raw_response, str):
        logger.warning(
            "tiktok_repurpose_parse_failed",
            extra={
                "smu_context": {
                    "stage": "repurpose_parse",
                    "reason": "non_text_response",
                    "object_type": _object_type_name(raw_response),
                    "generation_version": REPURPOSE_GENERATION_VERSION,
                },
            },
        )
        raise TikTokRepurposeError("TikTok repurpose response was not text.")

    try:
        payload = json.loads(_strip_json_code_fence(raw_response))
    except json.JSONDecodeError as exc:
        logger.warning(
            "tiktok_repurpose_parse_failed",
            extra={
                "smu_context": {
                    "stage": "repurpose_parse",
                    "exception_class": exc.__class__.__name__,
                    "parse_success": False,
                    "generation_version": REPURPOSE_GENERATION_VERSION,
                },
            },
        )
        raise TikTokRepurposeError("TikTok repurpose response was not valid JSON.") from exc

    logger.info(
        "tiktok_repurpose_parse_completed",
        extra={
            "smu_context": {
                "stage": "repurpose_parse",
                "parse_success": True,
                "returned_object_type": _object_type_name(payload),
                "returned_field_names": _payload_field_names(payload),
                "generated_field_lengths": _payload_field_lengths(payload),
                "generation_version": REPURPOSE_GENERATION_VERSION,
            },
        },
    )
    return validate_repurpose_result(payload)


def validate_repurpose_result(payload):
    if isinstance(payload, TikTokRepurposeResult):
        payload = {
            field: getattr(payload, field, None)
            for field in EXPECTED_REPURPOSE_FIELDS
        }

    if not isinstance(payload, dict):
        logger.warning(
            "tiktok_repurpose_validation_failed",
            extra={
                "smu_context": {
                    "stage": "repurpose_validation",
                    "reason": "non_object",
                    "validation_success": False,
                    "returned_object_type": _object_type_name(payload),
                    "generation_version": REPURPOSE_GENERATION_VERSION,
                },
            },
        )
        raise TikTokRepurposeError("TikTok repurpose response was not a JSON object.")

    values = {}

    for field in EXPECTED_REPURPOSE_FIELDS:
        if field not in payload:
            logger.warning(
                "tiktok_repurpose_validation_failed",
                extra={
                    "smu_context": {
                        "stage": "repurpose_validation",
                        "reason": "missing_field",
                        "field": field,
                        "validation_success": False,
                        "returned_object_type": _object_type_name(payload),
                        "returned_field_names": _payload_field_names(payload),
                        "generated_field_lengths": _payload_field_lengths(payload),
                        "generation_version": REPURPOSE_GENERATION_VERSION,
                    },
                },
            )
            raise TikTokRepurposeError("TikTok repurpose response was missing a field.")

        value = payload[field]

        if not isinstance(value, str):
            logger.warning(
                "tiktok_repurpose_validation_failed",
                extra={
                    "smu_context": {
                        "stage": "repurpose_validation",
                        "reason": "non_string_field",
                        "field": field,
                        "validation_success": False,
                        "returned_object_type": _object_type_name(payload),
                        "returned_field_names": _payload_field_names(payload),
                        "generated_field_lengths": _payload_field_lengths(payload),
                        "generation_version": REPURPOSE_GENERATION_VERSION,
                    },
                },
            )
            raise TikTokRepurposeError("TikTok repurpose response field was not text.")

        value = value.strip()

        if not value:
            logger.warning(
                "tiktok_repurpose_validation_failed",
                extra={
                    "smu_context": {
                        "stage": "repurpose_validation",
                        "reason": "empty_field",
                        "field": field,
                        "validation_success": False,
                        "returned_object_type": _object_type_name(payload),
                        "returned_field_names": _payload_field_names(payload),
                        "generated_field_lengths": _payload_field_lengths(payload),
                        "generation_version": REPURPOSE_GENERATION_VERSION,
                    },
                },
            )
            raise TikTokRepurposeError("TikTok repurpose response field was empty.")

        values[field] = value

    result = TikTokRepurposeResult(**values)
    logger.info(
        "tiktok_repurpose_validation_completed",
        extra={
            "smu_context": {
                "stage": "repurpose_validation",
                "validation_success": True,
                "returned_object_type": _object_type_name(result),
                "returned_field_names": _payload_field_names(result),
                "generated_field_lengths": _payload_field_lengths(result),
                "generation_version": REPURPOSE_GENERATION_VERSION,
            },
        },
    )
    return result


def repurpose_tiktok_content(
    transcript,
    brand_context="",
    *,
    openai_api_key=None,
    openai_client=None,
):
    if not openai_api_key:
        raise Exception("OPENAI_API_KEY is missing from your .env file")

    logger.info(
        "tiktok_repurpose_request_started",
        extra={
            "smu_context": {
                "stage": "repurpose_request",
                "transcript_length": len(transcript or ""),
                "brand_context_configured": bool(brand_context),
                "model": "gpt-4.1-mini",
                "generation_version": REPURPOSE_GENERATION_VERSION,
            },
        },
    )

    prompt = f"""
You are a social media content repurposing assistant.

/human

Turn this TikTok transcript into content for Instagram and Facebook.

Brand Brief:
{brand_context}

Return only a valid JSON object with these exact string fields:

{{
  "instagram_caption": "...",
  "facebook_caption": "...",
  "carousel_idea": "Slide 1: ...\\nSlide 2: ...\\nSlide 3: ...\\nSlide 4: ...\\nSlide 5: ...\\nSlide 6: ...",
  "image_prompt": "...",
  "hashtags": "..."
}}

Transcript:
{transcript}
"""

    response = openai_client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "tiktok_repurpose_content",
                "strict": True,
                "schema": REPURPOSE_RESPONSE_SCHEMA,
            }
        },
    )

    output_text = getattr(response, "output_text", None)
    logger.info(
        "tiktok_repurpose_response_received",
        extra={
            "smu_context": {
                "stage": "repurpose_response",
                "model": "gpt-4.1-mini",
                "response_received": response is not None,
                "output_text_length": (
                    len(output_text) if isinstance(output_text, str) else None
                ),
                "returned_object_type": _object_type_name(response),
                "generation_version": REPURPOSE_GENERATION_VERSION,
            },
        },
    )
    result = parse_repurpose_result(response.output_text)
    logger.info(
        "tiktok_repurpose_request_completed",
        extra={
            "smu_context": {
                "stage": "repurpose_request",
                "result": "success",
                "returned_object_type": _object_type_name(result),
                "returned_field_names": _payload_field_names(result),
                "generated_field_lengths": _payload_field_lengths(result),
                "generation_version": REPURPOSE_GENERATION_VERSION,
            },
        },
    )
    return result
