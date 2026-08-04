from conftest import create_user, login


def test_transcript_helper_bridges_exist_after_app_start(module):
    extension_keys = {
        key for key in module.app.extensions if key.startswith("smu_")
    }

    assert {
        "smu_calendar_helpers",
        "smu_tiktok_helpers",
        "smu_content_pack_helpers",
    }.issubset(extension_keys)
    assert callable(
        module.app.extensions["smu_tiktok_helpers"]["extract_tiktok_transcript"]
    )
    assert callable(
        module.app.extensions["smu_content_pack_helpers"]["extract_tiktok_transcript"]
    )


def test_calendar_bridge_does_not_remove_transcript_bridges(module):
    assert "calendar" in module.app.blueprints
    assert module.app.extensions["smu_calendar_helpers"]
    assert "extract_tiktok_transcript" in module.app.extensions["smu_tiktok_helpers"]
    assert (
        "extract_tiktok_transcript"
        in module.app.extensions["smu_content_pack_helpers"]
    )


def test_tiktok_post_invokes_transcript_helper(client, app, module, monkeypatch):
    user = create_user(module)
    login(client, user)
    calls = {}

    def fake_extract_tiktok_transcript(url):
        calls["url"] = url
        return "Transcript from helper"

    monkeypatch.setitem(
        app.extensions["smu_tiktok_helpers"],
        "extract_tiktok_transcript",
        fake_extract_tiktok_transcript,
    )
    monkeypatch.setitem(
        app.extensions["smu_tiktok_helpers"],
        "build_brand_context",
        lambda user_id: "Brand context",
    )
    monkeypatch.setitem(
        app.extensions["smu_tiktok_helpers"],
        "repurpose_tiktok_content",
        lambda transcript, brand_context: "Generated content",
    )

    response = client.post(
        "/tiktok",
        data={"tiktok_url": "  https://www.tiktok.com/@user/video/123  "},
    )

    assert response.status_code == 200
    assert calls == {"url": "https://www.tiktok.com/@user/video/123"}


def test_content_pack_tiktok_flow_invokes_transcript_helper(
    client, app, module, monkeypatch
):
    user = create_user(module)
    login(client, user)
    calls = {}

    def fake_extract_tiktok_transcript(url):
        calls["url"] = url
        return "Transcript from helper"

    def fake_generate_content_pack(source_text, brand_context):
        calls["source_text"] = source_text
        calls["brand_context"] = brand_context
        return "Generated content pack"

    monkeypatch.setitem(
        app.extensions["smu_content_pack_helpers"],
        "extract_tiktok_transcript",
        fake_extract_tiktok_transcript,
    )
    monkeypatch.setitem(
        app.extensions["smu_content_pack_helpers"],
        "build_brand_context",
        lambda user_id: "Brand context",
    )
    monkeypatch.setitem(
        app.extensions["smu_content_pack_helpers"],
        "generate_content_pack",
        fake_generate_content_pack,
    )

    response = client.post(
        "/content-pack",
        data={
            "source_type": "tiktok",
            "source_input": "https://www.tiktok.com/@user/video/123",
        },
    )

    assert response.status_code == 200
    assert calls == {
        "url": "https://www.tiktok.com/@user/video/123",
        "source_text": "Transcript from helper",
        "brand_context": "Brand context",
    }
