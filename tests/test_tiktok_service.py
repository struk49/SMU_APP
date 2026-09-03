import inspect
import json
import logging

import pytest

import app as smu_app
from conftest import create_user, login
from smu_core.services import tiktok as tiktok_service


STRUCTURED_PAYLOAD = {
    "instagram_caption": "Instagram caption",
    "facebook_caption": "Facebook caption",
    "carousel_idea": "Slide 1: First\nSlide 2: Second",
    "image_prompt": "Image prompt",
    "hashtags": "#one #two",
}
STRUCTURED_OUTPUT = json.dumps(STRUCTURED_PAYLOAD)


class FakeOpenAIClient:
    def __init__(self, output_text=STRUCTURED_OUTPUT):
        self.calls = []
        self.output_text = output_text
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(kwargs)

        class Response:
            pass

        response = Response()
        response.output_text = self.output_text
        return response


def test_real_tiktok_repurpose_implementation_lives_in_service():
    service_source = inspect.getsource(tiktok_service.repurpose_tiktok_content)
    wrapper_source = inspect.getsource(smu_app.repurpose_tiktok_content)

    assert "openai_client.responses.create" in service_source
    assert "Turn this TikTok transcript into content for Instagram and Facebook." in service_source
    assert "openai_client.responses.create" not in wrapper_source
    assert "tiktok_service.repurpose_tiktok_content" in wrapper_source
    assert tiktok_service.REPURPOSE_GENERATION_VERSION == "structured-v1"


def test_tiktok_service_returns_validated_structured_result():
    client = FakeOpenAIClient()

    result = tiktok_service.repurpose_tiktok_content(
        "Transcript text",
        "Brand context",
        openai_api_key="test-key",
        openai_client=client,
    )

    assert result == tiktok_service.TikTokRepurposeResult(**STRUCTURED_PAYLOAD)
    assert result.instagram_caption == "Instagram caption"
    assert result.facebook_caption == "Facebook caption"
    assert result.carousel_idea == "Slide 1: First\nSlide 2: Second"
    assert result.image_prompt == "Image prompt"
    assert result.hashtags == "#one #two"
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model"] == "gpt-4.1-mini"
    assert set(call.keys()) == {"model", "input", "text"}
    assert call["input"].count("/human") == 1
    assert call["text"]["format"] == {
        "type": "json_schema",
        "name": "tiktok_repurpose_content",
        "strict": True,
        "schema": tiktok_service.REPURPOSE_RESPONSE_SCHEMA,
    }
    assert "Brand Brief:\nBrand context" in call["input"]
    assert "Transcript:\nTranscript text" in call["input"]
    assert '"instagram_caption"' in call["input"]
    assert '"facebook_caption"' in call["input"]
    assert '"carousel_idea"' in call["input"]
    assert '"image_prompt"' in call["input"]
    assert '"hashtags"' in call["input"]


def test_parse_repurpose_result_strips_whitespace_and_code_fence():
    output = "```json\n" + json.dumps({
        "instagram_caption": "  Instagram caption  ",
        "facebook_caption": "\nFacebook caption\n",
        "carousel_idea": " Slide 1: First\nSlide 2: Second ",
        "image_prompt": " Image prompt ",
        "hashtags": " #one #two ",
    }) + "\n```"

    result = tiktok_service.parse_repurpose_result(output)

    assert result == tiktok_service.TikTokRepurposeResult(
        instagram_caption="Instagram caption",
        facebook_caption="Facebook caption",
        carousel_idea="Slide 1: First\nSlide 2: Second",
        image_prompt="Image prompt",
        hashtags="#one #two",
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"facebook_caption": "Facebook", "carousel_idea": "Slides", "image_prompt": "Image", "hashtags": "#tag"},
        {**STRUCTURED_PAYLOAD, "instagram_caption": "   "},
        {**STRUCTURED_PAYLOAD, "instagram_caption": ["not", "text"]},
    ],
)
def test_parse_repurpose_result_rejects_missing_empty_or_non_string_fields(payload):
    with pytest.raises(tiktok_service.TikTokRepurposeError):
        tiktok_service.parse_repurpose_result(json.dumps(payload))


@pytest.mark.parametrize("raw_response", ["not json", "[]", json.dumps(["not", "object"])])
def test_parse_repurpose_result_rejects_malformed_or_non_object_output(raw_response):
    with pytest.raises(tiktok_service.TikTokRepurposeError):
        tiktok_service.parse_repurpose_result(raw_response)


def test_validate_repurpose_result_rejects_blank_dataclass_result():
    payload = tiktok_service.TikTokRepurposeResult(
        instagram_caption="",
        facebook_caption="Facebook caption",
        carousel_idea="Carousel idea",
        image_prompt="Image prompt",
        hashtags="#one #two",
    )

    with pytest.raises(tiktok_service.TikTokRepurposeError, match="field was empty"):
        tiktok_service.validate_repurpose_result(payload)


def test_parse_repurpose_result_logs_safe_metadata_without_generated_content(caplog):
    caplog.set_level(logging.INFO, logger="smu_core.services.tiktok")

    with pytest.raises(tiktok_service.TikTokRepurposeError):
        tiktok_service.parse_repurpose_result(
            '{"instagram_caption": "full generated caption fixture"}'
        )

    assert "full generated caption fixture" not in caplog.text
    contexts = [
        getattr(record, "smu_context", {})
        for record in caplog.records
        if record.message == "tiktok_repurpose_validation_failed"
    ]
    assert contexts
    assert contexts[0]["stage"] == "repurpose_validation"
    assert contexts[0]["reason"] == "missing_field"
    assert contexts[0]["field"] == "facebook_caption"


def test_tiktok_service_preserves_missing_key_and_openai_failure_behaviour():
    client = FakeOpenAIClient()

    with pytest.raises(Exception, match="OPENAI_API_KEY is missing"):
        tiktok_service.repurpose_tiktok_content(
            "Transcript",
            "",
            openai_api_key="",
            openai_client=client,
        )

    class FailingOpenAIClient(FakeOpenAIClient):
        def create(self, **kwargs):
            raise RuntimeError("openai failed")

    with pytest.raises(RuntimeError, match="openai failed"):
        tiktok_service.repurpose_tiktok_content(
            "Transcript",
            "",
            openai_api_key="test-key",
            openai_client=FailingOpenAIClient(),
        )


def test_tiktok_service_logs_request_metadata_without_sensitive_text(caplog):
    client = FakeOpenAIClient()
    caplog.set_level(logging.INFO, logger="smu_core.services.tiktok")

    tiktok_service.repurpose_tiktok_content(
        "Raw transcript fixture with private detail",
        "Brand context",
        openai_api_key="OPENAI_API_KEY test secret",
        openai_client=client,
    )

    assert "Raw transcript fixture with private detail" not in caplog.text
    assert "OPENAI_API_KEY test secret" not in caplog.text
    assert "Instagram caption" not in caplog.text
    started = [
        getattr(record, "smu_context", {})
        for record in caplog.records
        if record.message == "tiktok_repurpose_request_started"
    ]
    completed = [
        getattr(record, "smu_context", {})
        for record in caplog.records
        if record.message == "tiktok_repurpose_request_completed"
    ]
    assert started
    assert completed
    assert started[0]["transcript_length"] == len("Raw transcript fixture with private detail")
    assert started[0]["brand_context_configured"] is True
    assert started[0]["model"] == "gpt-4.1-mini"
    assert started[0]["generation_version"] == "structured-v1"
    assert completed[0]["returned_object_type"] == "TikTokRepurposeResult"
    assert completed[0]["returned_field_names"] == list(
        tiktok_service.EXPECTED_REPURPOSE_FIELDS
    )
    assert completed[0]["generated_field_lengths"] == {
        field: len(value) for field, value in STRUCTURED_PAYLOAD.items()
    }


def test_tiktok_repurpose_logs_response_and_parse_shape_without_content(caplog):
    client = FakeOpenAIClient()
    caplog.set_level(logging.INFO, logger="smu_core.services.tiktok")

    result = tiktok_service.repurpose_tiktok_content(
        "Private transcript fixture",
        "",
        openai_api_key="test-key",
        openai_client=client,
    )

    output = caplog.text
    response_contexts = [
        getattr(record, "smu_context", {})
        for record in caplog.records
        if record.message == "tiktok_repurpose_response_received"
    ]
    parse_contexts = [
        getattr(record, "smu_context", {})
        for record in caplog.records
        if record.message == "tiktok_repurpose_parse_completed"
    ]
    validation_contexts = [
        getattr(record, "smu_context", {})
        for record in caplog.records
        if record.message == "tiktok_repurpose_validation_completed"
    ]

    assert result == tiktok_service.TikTokRepurposeResult(**STRUCTURED_PAYLOAD)
    assert "Private transcript fixture" not in output
    assert "Instagram caption" not in output
    assert "#one #two" not in output
    assert response_contexts
    assert response_contexts[0]["response_received"] is True
    assert response_contexts[0]["output_text_length"] == len(STRUCTURED_OUTPUT)
    assert parse_contexts
    assert parse_contexts[0]["parse_success"] is True
    assert parse_contexts[0]["returned_object_type"] == "dict"
    assert parse_contexts[0]["returned_field_names"] == sorted(
        tiktok_service.EXPECTED_REPURPOSE_FIELDS
    )
    assert validation_contexts[-1]["validation_success"] is True
    assert validation_contexts[-1]["returned_object_type"] == "TikTokRepurposeResult"


def test_app_wrapper_delegates_to_tiktok_service(monkeypatch):
    calls = {}

    def fake_repurpose(transcript, brand_context="", **kwargs):
        calls["transcript"] = transcript
        calls["brand_context"] = brand_context
        calls["kwargs"] = kwargs
        return tiktok_service.TikTokRepurposeResult(**STRUCTURED_PAYLOAD)

    monkeypatch.setattr(tiktok_service, "repurpose_tiktok_content", fake_repurpose)

    assert smu_app.repurpose_tiktok_content("Transcript", "Brand") == (
        tiktok_service.TikTokRepurposeResult(**STRUCTURED_PAYLOAD)
    )
    assert calls["transcript"] == "Transcript"
    assert calls["brand_context"] == "Brand"
    assert calls["kwargs"]["openai_api_key"] == smu_app.OPENAI_API_KEY
    assert calls["kwargs"]["openai_client"] is smu_app.openai_client


def test_tiktok_helper_bridge_remains_late_bound_to_app_wrapper(app, module, monkeypatch):
    calls = {}

    def fake_wrapper(transcript, brand_context=""):
        calls["transcript"] = transcript
        calls["brand_context"] = brand_context
        return tiktok_service.TikTokRepurposeResult(**STRUCTURED_PAYLOAD)

    monkeypatch.setattr(module, "repurpose_tiktok_content", fake_wrapper)

    helper = app.extensions["smu_tiktok_helpers"]["repurpose_tiktok_content"]

    assert helper.__module__ == "app"
    assert helper.__name__ == "<lambda>"
    assert helper("Transcript", "Brand") == (
        tiktok_service.TikTokRepurposeResult(**STRUCTURED_PAYLOAD)
    )
    assert calls == {"transcript": "Transcript", "brand_context": "Brand"}


def test_tiktok_route_still_uses_bridge_successfully(client, app, module, monkeypatch):
    user = create_user(module)
    login(client, user)
    calls = {}

    def fake_extract(url):
        calls["url"] = url
        return "Transcript text"

    def fake_brand_context(user_id):
        calls["user_id"] = user_id
        return "Brand context"

    def fake_repurpose(transcript, brand_context):
        calls["repurpose"] = (transcript, brand_context)
        return tiktok_service.TikTokRepurposeResult(**STRUCTURED_PAYLOAD)

    monkeypatch.setitem(
        app.extensions["smu_tiktok_helpers"],
        "extract_tiktok_transcript",
        fake_extract,
    )
    monkeypatch.setitem(
        app.extensions["smu_tiktok_helpers"],
        "build_brand_context",
        fake_brand_context,
    )
    monkeypatch.setitem(
        app.extensions["smu_tiktok_helpers"],
        "repurpose_tiktok_content",
        fake_repurpose,
    )

    response = client.post(
        "/tiktok",
        data={"tiktok_url": "https://tiktok.test/video"},
    )

    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert calls == {
        "url": "https://tiktok.test/video",
        "user_id": user.id,
        "repurpose": ("Transcript text", "Brand context"),
    }
    assert "Instagram caption" in html
    assert "Facebook caption" in html
    assert "Image prompt" in html
    assert "Slide 1: First" in html
