"""Shared Responses text-model selection; image/audio models are independent."""

import os

from flask import current_app, has_app_context


DEFAULT_TEXT_MODEL = "gpt-6-luna"


class TextModelConfigurationError(ValueError):
    """An explicit text-model setting cannot be used safely."""


def text_generation_options():
    """Keep the former non-reasoning workload explicit on GPT-6 Luna.

    Flask configuration wins in app requests; standalone services use the same
    environment variable. Invalid overrides fail instead of selecting a fallback.
    Other model overrides must support Responses and reasoning.effort=none.
    """
    model = (
        current_app.config.get("OPENAI_TEXT_MODEL", DEFAULT_TEXT_MODEL)
        if has_app_context()
        else os.getenv("OPENAI_TEXT_MODEL", DEFAULT_TEXT_MODEL)
    )
    if not isinstance(model, str) or not model.strip():
        raise TextModelConfigurationError("OPENAI_TEXT_MODEL must be a non-empty model ID")
    model = model.strip()
    if model.startswith(("gpt-4.1", "gpt-4o")):
        raise TextModelConfigurationError(
            "OPENAI_TEXT_MODEL selects a retiring text model; configure gpt-6-luna"
        )
    return {"model": model, "reasoning": {"effort": "none"}}
