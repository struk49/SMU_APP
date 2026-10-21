"""Exercise every production text call through the installed SDK with no network."""

import json
from types import SimpleNamespace

import httpx
from openai import BadRequestError, OpenAI
import pytest

import app as smu_app
from smu_core.services import content, text_models, tiktok


SLIDES = "Slide 1:\nTitle: Grow herbs in small spaces\nSubtitle: Practical container advice\nSlide 2:\nTitle: Check the soil\nBody: Water when the surface feels dry."
PACK = "\n\n".join(
    name + ":\n" + (SLIDES if name == "CAROUSEL_IDEA" else "Useful source-grounded text.")
    for name in content.CONTENT_PACK_SECTION_NAMES
)
TIKTOK_PAYLOAD = {
    "instagram_caption": "Herb tips", "facebook_caption": "Grow herbs",
    "carousel_idea": "Slide 1: Herb tips\nSlide 2: Check soil",
    "image_prompt": "Potted herbs", "hashtags": "#Herbs",
}
PATHS = (
    "pack", "repair", "caption_action", "grade", "caption_rewrite",
    "brand_match", "caption_improve", "tiktok",
)


def invoke(path, sdk, monkeypatch):
    post = SimpleNamespace(caption="Herb tips", platforms="instagram", post_type="single", grade_result="Improve clarity")
    monkeypatch.setattr(smu_app, "openai_client", sdk)
    monkeypatch.setattr(smu_app, "OPENAI_API_KEY", "test-key")
    if path == "pack":
        return content.generate_content_pack("Grow herbs in small spaces", openai_api_key="test-key", openai_client=sdk)
    if path == "repair":
        return content.repair_carousel_structure(SLIDES, failure_reason="missing_campaign_cover", semantic_domain="general_business", openai_api_key="test-key", openai_client=sdk)
    if path == "caption_action":
        return smu_app.rewrite_caption_with_action(post, "Herb brand", "shorten")
    if path == "grade":
        return smu_app.grade_post_with_ai(post, "Herb brand")
    if path == "caption_rewrite":
        return smu_app.rewrite_caption_with_ai(post.caption, "shorten")
    if path == "brand_match":
        return smu_app.evaluate_brand_match(post, "Herb brand")
    if path == "caption_improve":
        return smu_app.improve_post_with_ai(post, "Herb brand")
    return smu_app.repurpose_tiktok_content("Herb transcript", "Herb brand")


def sdk_response(output, model):
    return {"id": "resp_mock", "object": "response", "created_at": 0,
            "model": model, "status": "completed", "output": [
                {"id": "msg_mock", "type": "message", "role": "assistant", "status": "completed",
                 "content": [{"type": "output_text", "text": output, "annotations": []}]}]}


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("override", [None, "test-explicit-model"])
def test_all_text_paths_use_central_selection_and_compatible_sdk_requests(app, monkeypatch, path, override):
    model = override or "gpt-6-luna"
    monkeypatch.setitem(app.config, "OPENAI_TEXT_MODEL", model)
    output = PACK if path == "pack" else SLIDES if path == "repair" else json.dumps(TIKTOK_PAYLOAD) if path == "tiktok" else "Provider result"
    calls = []

    def handle(request):
        assert request.method == "POST"
        assert request.url.path == "/v1/responses"
        payload = json.loads(request.content)
        calls.append(payload)
        assert payload["model"] == model
        assert payload["reasoning"] == {"effort": "none"}
        assert isinstance(payload["input"], str) and payload["input"]
        assert not {"temperature", "top_p", "max_tokens", "reasoning_effort"}.intersection(payload)
        if path in {"pack", "repair"}:
            assert request.extensions["timeout"]["read"] == content.CONTENT_PACK_TIMEOUT_SECONDS
        return httpx.Response(200, json=sdk_response(output, model))

    with OpenAI(api_key="test-key", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handle))) as sdk:
        result = invoke(path, sdk, monkeypatch)
    assert len(calls) == 1
    if path == "tiktok":
        assert result == tiktok.TikTokRepurposeResult(**TIKTOK_PAYLOAD)
        assert calls[0]["text"]["format"]["schema"] == tiktok.REPURPOSE_RESPONSE_SCHEMA
        assert calls[0]["text"]["format"]["strict"] is True
    else:
        assert result == output
        assert "text" not in calls[0]  # Existing free-text contracts remain free text.


@pytest.mark.parametrize("path", PATHS)
def test_provider_rejection_never_falls_back_to_a_retiring_model(app, monkeypatch, path):
    monkeypatch.setitem(app.config, "OPENAI_TEXT_MODEL", "gpt-6-luna")
    calls = []
    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"message": "Model unavailable", "type": "invalid_request_error", "code": "model_not_found"}})
    with OpenAI(api_key="test-key", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handle))) as sdk:
        expected_error = {"pack": content.ContentPackGenerationError, "repair": content.CarouselStructureRepairError}.get(path, BadRequestError)
        with pytest.raises(expected_error):
            invoke(path, sdk, monkeypatch)
    assert len(calls) == 1
    assert calls[0]["model"] == "gpt-6-luna"


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("invalid", ["", "  ", None, "gpt-4.1-mini", "gpt-4.1-2025-04-14", "gpt-4o-mini"])
def test_invalid_configuration_fails_before_provider_without_fallback(app, monkeypatch, path, invalid):
    monkeypatch.setitem(app.config, "OPENAI_TEXT_MODEL", invalid)
    calls = []
    def handle(request):
        calls.append(request)
        pytest.fail("invalid configuration must not reach the provider")
    with OpenAI(api_key="test-key", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handle))) as sdk:
        expected_error = {"pack": content.ContentPackGenerationError, "repair": content.CarouselStructureRepairError}.get(path, text_models.TextModelConfigurationError)
        with pytest.raises(expected_error):
            invoke(path, sdk, monkeypatch)
    assert calls == []


def test_standalone_environment_override_and_default(monkeypatch):
    monkeypatch.delenv("OPENAI_TEXT_MODEL", raising=False)
    assert text_models.text_generation_options() == {"model": "gpt-6-luna", "reasoning": {"effort": "none"}}
    monkeypatch.setenv("OPENAI_TEXT_MODEL", " test-explicit-model ")
    assert text_models.text_generation_options()["model"] == "test-explicit-model"
    monkeypatch.setenv("OPENAI_TEXT_MODEL", "")
    with pytest.raises(text_models.TextModelConfigurationError):
        text_models.text_generation_options()


def test_app_configuration_wins_over_environment(app, monkeypatch):
    monkeypatch.setenv("OPENAI_TEXT_MODEL", "wrong-model")
    monkeypatch.setitem(app.config, "OPENAI_TEXT_MODEL", "gpt-6-luna")
    assert text_models.text_generation_options()["model"] == "gpt-6-luna"
