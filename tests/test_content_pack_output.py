import json
import re
from pathlib import Path

import httpx
from openai import OpenAI
from openai.types.responses import Response
import pytest

from conftest import create_user, login
from smu_core.services import content, carousel_generation
from smu_core.services.content_pack_output import CONTENT_PACK_RESPONSE_FORMAT, serialize_content_pack_response


SECTIONS = (
    "INSTAGRAM_CAPTION", "FACEBOOK_POST", "CAROUSEL_IDEA", "PINTEREST_PIN_TITLE",
    "PINTEREST_PIN_DESCRIPTION", "REDDIT_POST", "X_POST", "LINKEDIN_POST", "IMAGE_PROMPT", "HASHTAGS",
)
SOURCE = "Create a Content Pack about five tips for growing herbs in small spaces."
SAVED = Path(__file__).parent / "fixtures" / "content_pack_luna_duplicate.txt"


def payload():
    def slide(title, body=None):
        return dict(title=title, subtitle=None, body=body, phrase_pairs=[], tip=None,
                    cta=None, visual=None, visual_weight=None, eyebrow=None, emphasis=None)
    result = {name: "Text for " + name for name in SECTIONS}
    result["CAROUSEL_IDEA"] = [slide("Grow herbs"), slide("Check soil", "First line.\nSecond line: keep punctuation.")]
    return result


def response_body(raw, status="completed", refusal=False, extra_text=None):
    parts = [{"type": "output_text", "text": raw, "annotations": []}]
    if refusal:
        parts.append({"type": "refusal", "refusal": "Cannot comply"})
    output = [{"id": "msg_test", "type": "message", "status": "completed", "role": "assistant", "content": parts}]
    if extra_text:
        output.append({"id": "msg_extra", "type": "message", "status": "completed", "role": "assistant",
                       "content": [{"type": "output_text", "text": extra_text, "annotations": []}]})
    return dict(id="resp_test", object="response", created_at=0, model="gpt-6-luna", status=status, output=output, parallel_tool_calls=True, tool_choice="auto", tools=[])


def provider(body, calls):
    def handle(request):
        calls.append(json.loads(request.content))
        assert request.url.path == "/v1/responses"
        return httpx.Response(200, json=body)
    return OpenAI(api_key="test-key", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handle)))


def test_saved_failure_has_duplicate_inside_one_provider_block_not_sdk_handling():
    raw = SAVED.read_text(encoding="utf-8")
    response = Response.model_validate(response_body(raw))
    assert len(response.output) == 1
    assert len(response.output[0].content) == 1
    assert response.output[0].content[0].text == response.output_text == raw
    assert len(re.findall(r"(?m)^LINKEDIN_POST:", raw)) == 2
    calls = []
    with provider(response_body(raw), calls) as sdk:
        with pytest.raises(content.ContentPackGenerationError, match="invalid_output"):
            content.generate_content_pack(SOURCE, openai_api_key="test-key", openai_client=sdk)
    assert len(calls) == 1
    assert calls[0]["text"]["format"] == CONTENT_PACK_RESPONSE_FORMAT


def test_schema_requires_all_sections_once_and_structured_slide_fields():
    fmt = CONTENT_PACK_RESPONSE_FORMAT
    assert fmt["strict"] is True
    schema = fmt["schema"]
    assert tuple(schema["properties"]) == SECTIONS
    assert set(schema["required"]) == set(SECTIONS)
    def check_objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["properties"]) == set(node["required"])
            for value in node.values():
                check_objects(value)
        elif isinstance(node, list):
            for value in node:
                check_objects(value)
    check_objects(schema)
    assert schema["properties"]["CAROUSEL_IDEA"]["minItems"] == 2
    assert schema["properties"]["CAROUSEL_IDEA"]["maxItems"] == 6


def test_valid_wire_payload_serializes_in_fixed_order_without_changing_copy():
    data = payload()
    data["CAROUSEL_IDEA"][1]["phrase_pairs"] = [
        {"phrase": "Dziękuję!", "translation": "Thank you!"},
        {"phrase": "Dziękuję!", "translation": "Thank you!"},
    ]
    calls = []
    with provider(response_body(json.dumps(data, ensure_ascii=False)), calls) as sdk:
        result = content.generate_content_pack(SOURCE, openai_api_key="test-key", openai_client=sdk)
    assert tuple(re.findall(r"(?m)^([A-Z_]+):$", result)) == SECTIONS
    assert "Slide 1:\nTitle: Grow herbs\nSlide 2:\nTitle: Check soil\nBody: First line.\nSecond line: keep punctuation." in result
    assert result.count("Phrase: Dziękuję!\nTranslation: Thank you!") == 2
    assert len(content._parse_generated_carousel_blocks(content.extract_content_pack_section(result, "CAROUSEL_IDEA"))) == 2
    reordered = Response.model_validate(response_body(json.dumps(dict(reversed(list(data.items()))), ensure_ascii=False)))
    assert serialize_content_pack_response(reordered) == result
    assert len(calls) == 1
    assert calls[0]["model"] == "gpt-6-luna"
    assert calls[0]["reasoning"] == {"effort": "none"}


@pytest.mark.parametrize("case", ["duplicate_key", "nested_duplicate", "extra_key", "missing_key", "wrong_type", "missing_slide_field", "empty_field", "truncated", "concatenated", "multiple_messages", "refusal", "refusal_with_valid_json", "incomplete", "missing_status"])
def test_bad_wire_output_never_gets_salvaged(case):
    data = payload()
    raw = json.dumps(data)
    status = "completed"
    refusal = False
    extra_text = None
    if case == "duplicate_key":
        raw = raw[:-1] + ', "LINKEDIN_POST": "Duplicate"}'
    elif case == "nested_duplicate":
        raw = raw.replace('"title": "Grow herbs"', '"title": "Grow herbs", "title": "Duplicate"')
    elif case == "extra_key":
        data["UNSUPPORTED"] = "Do not discard me"
        raw = json.dumps(data)
    elif case == "missing_key":
        del data["LINKEDIN_POST"]
        raw = json.dumps(data)
    elif case == "wrong_type":
        data["LINKEDIN_POST"] = ["Duplicate", "Duplicate"]
        raw = json.dumps(data)
    elif case == "missing_slide_field":
        del data["CAROUSEL_IDEA"][0]["subtitle"]
        raw = json.dumps(data)
    elif case == "empty_field":
        data["CAROUSEL_IDEA"][0]["subtitle"] = ""
        raw = json.dumps(data)
    elif case == "truncated":
        raw = raw[:-10]
    elif case == "concatenated":
        raw += raw
    elif case == "multiple_messages":
        extra_text = raw
    elif case.startswith("refusal"):
        refusal = True
        if case == "refusal":
            raw = ""
    elif case == "incomplete":
        status = "incomplete"
    elif case == "missing_status":
        status = None
    calls = []
    with provider(response_body(raw, status, refusal, extra_text), calls) as sdk:
        with pytest.raises(content.ContentPackGenerationError, match="invalid_output"):
            content.generate_content_pack(SOURCE, openai_api_key="test-key", openai_client=sdk)
    assert len(calls) == 1


@pytest.mark.parametrize("case", ["saved_duplicate", "refusal", "incomplete"])
def test_failed_structured_response_releases_real_local_credit_once(client, app, module, monkeypatch, case):
    user = create_user(module)
    login(client, user)
    raw = SAVED.read_text(encoding="utf-8") if case == "saved_duplicate" else json.dumps(payload())
    body = response_body(raw, "incomplete" if case == "incomplete" else "completed", case == "refusal")
    helpers = app.extensions["smu_content_pack_helpers"]
    releases = []
    release = helpers["release_content_pack_credits"]
    def release_once(current_user):
        releases.append(current_user.id)
        release(current_user)
    def forbidden(*args, **kwargs):
        pytest.fail("No image credits, workers or repair on a failed text response")
    monkeypatch.setitem(helpers, "release_content_pack_credits", release_once)
    monkeypatch.setitem(helpers, "reserve_ai_image_credits", forbidden)
    monkeypatch.setitem(helpers, "build_brand_context", lambda user_id: "")
    monkeypatch.setattr(carousel_generation, "generate_pending_carousel_images", forbidden)
    monkeypatch.setattr(content, "repair_carousel_structure", forbidden)
    calls = []
    with provider(body, calls) as sdk:
        monkeypatch.setitem(helpers, "generate_content_pack", lambda source, brand, **kwargs: content.generate_content_pack(source, brand, openai_api_key="test-key", openai_client=sdk, **kwargs))
        response = client.post("/content-pack", data={"source_type": "text", "source_input": SOURCE})
    assert response.status_code == 200
    assert len(calls) == 1
    assert releases == [user.id]
    usage = module.UserUsage.query.filter_by(user_id=user.id).one()
    assert usage.content_packs_used == usage.ai_images_used == 0
    assert module.Post.query.count() == 0
    page = response.get_data(as_text=True)
    assert SOURCE in page
    assert 'name="content_pack_result"' not in page
    assert "was incomplete or its carousel was not structured" in page
