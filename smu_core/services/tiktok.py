"""TikTok-specific content repurposing helpers."""

from dataclasses import dataclass
import json
import logging


logger = logging.getLogger(__name__)


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
                },
            },
        )
        raise TikTokRepurposeError("TikTok repurpose response was not valid JSON.") from exc

    return validate_repurpose_result(payload)


def validate_repurpose_result(payload):
    if isinstance(payload, TikTokRepurposeResult):
        return payload

    if not isinstance(payload, dict):
        logger.warning(
            "tiktok_repurpose_validation_failed",
            extra={
                "smu_context": {
                    "stage": "repurpose_validation",
                    "reason": "non_object",
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
                    },
                },
            )
            raise TikTokRepurposeError("TikTok repurpose response field was empty.")

        values[field] = value

    return TikTokRepurposeResult(**values)


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
            },
        },
    )

    prompt = f"""
You are a social media content repurposing assistant.

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
    )

    result = parse_repurpose_result(response.output_text)
    logger.info(
        "tiktok_repurpose_request_completed",
        extra={
            "smu_context": {
                "stage": "repurpose_request",
                "result": "success",
            },
        },
    )
    return result
