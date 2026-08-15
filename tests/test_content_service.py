import logging

import pytest

import app as smu_app
from smu_core.services import content


class FakeYoutubeDL:
    info = {}
    options = None
    called = {}

    def __init__(self, options):
        self.__class__.options = options

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, url, download=False):
        self.__class__.called = {"url": url, "download": download}
        return self.__class__.info


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class FakeOpenAIClient:
    def __init__(self):
        self.calls = []
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(kwargs)

        class Response:
            output_text = "PACK OUTPUT"

        return Response()


def caption_entry(text, ext="vtt"):
    return {"ext": ext, "data": text}


def extract_with_info(info, *, requests_get=None):
    FakeYoutubeDL.info = info
    FakeYoutubeDL.called = {}
    return content.extract_tiktok_transcript(
        "https://www.tiktok.com/@user/video/123?token=secret",
        youtube_dl_cls=FakeYoutubeDL,
        requests_get=requests_get or fail_caption_fetch,
    )


def fail_caption_fetch(url, timeout=10):
    raise AssertionError("real caption fetch should not occur in tests")


def test_content_service_exports_and_app_wrappers_remain_callable(module):
    assert callable(content.extract_tiktok_transcript)
    assert callable(content.generate_content_pack)
    assert callable(content.extract_content_pack_section)
    assert callable(content.apply_image_style)
    assert callable(content.get_placeholder_image_url)
    assert not hasattr(content, "build_brand_context")
    assert callable(module.extract_tiktok_transcript)
    assert callable(module.generate_content_pack)
    assert callable(module.extract_content_pack_section)
    assert callable(module.apply_image_style)
    assert callable(module.get_placeholder_image_url)


def test_tiktok_and_content_pack_bridges_remain_late_bound(module):
    assert "smu_tiktok_helpers" in module.app.extensions
    assert "smu_content_pack_helpers" in module.app.extensions
    assert callable(
        module.app.extensions["smu_tiktok_helpers"]["extract_tiktok_transcript"]
    )
    assert callable(
        module.app.extensions["smu_content_pack_helpers"][
            "extract_tiktok_transcript"
        ]
    )


def test_app_wrappers_delegate_with_existing_late_bound_dependencies(monkeypatch):
    calls = {}

    def fake_extract(url, **kwargs):
        calls["extract"] = {"url": url, **kwargs}
        return "transcript"

    def fake_generate(source_text, brand_context, **kwargs):
        calls["generate"] = {
            "source_text": source_text,
            "brand_context": brand_context,
            **kwargs,
        }
        return "pack"

    monkeypatch.setattr(smu_app.content_service, "extract_tiktok_transcript", fake_extract)
    monkeypatch.setattr(smu_app.content_service, "generate_content_pack", fake_generate)
    monkeypatch.setattr(
        smu_app.content_service,
        "extract_content_pack_section",
        lambda text, name: f"{name}:{text}",
    )
    monkeypatch.setattr(
        smu_app.content_service,
        "apply_image_style",
        lambda prompt, style: f"{style}:{prompt}",
    )
    monkeypatch.setattr(
        smu_app.content_service,
        "get_placeholder_image_url",
        lambda: "https://cdn.test/placeholder.jpg",
    )

    assert smu_app.extract_tiktok_transcript("https://tiktok.test/video") == "transcript"
    assert smu_app.generate_content_pack("source", "brand") == "pack"
    assert smu_app.extract_content_pack_section("text", "SECTION") == "SECTION:text"
    assert smu_app.apply_image_style("prompt", "style") == "style:prompt"
    assert smu_app.get_placeholder_image_url() == "https://cdn.test/placeholder.jpg"
    assert calls["extract"]["youtube_dl_cls"] is smu_app.YoutubeDL
    assert calls["extract"]["requests_get"] is smu_app.requests.get
    assert calls["generate"]["openai_api_key"] == smu_app.OPENAI_API_KEY
    assert calls["generate"]["openai_client"] is smu_app.openai_client


def test_requested_subtitles_are_preferred_over_other_caption_sources():
    transcript = extract_with_info(
        {
            "title": "Title fallback",
            "description": "Description fallback",
            "requested_subtitles": {
                "en": caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nRequested")
            },
            "subtitles": {
                "en": [caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nSubtitle")]
            },
            "automatic_captions": {
                "en": [caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nAuto")]
            },
        }
    )

    assert transcript == "Requested"
    assert FakeYoutubeDL.options == {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en"],
    }
    assert FakeYoutubeDL.called["download"] is False


def test_subtitles_and_automatic_captions_fallback_order():
    assert extract_with_info(
        {
            "subtitles": {
                "en": [caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nSubtitle")]
            },
            "automatic_captions": {
                "en": [caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nAuto")]
            },
        }
    ) == "Subtitle"
    assert extract_with_info(
        {
            "automatic_captions": {
                "en": [caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nAuto")]
            },
        }
    ) == "Auto"


def test_english_preference_and_first_usable_non_english_language():
    assert extract_with_info(
        {
            "subtitles": {
                "fr": [caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nBonjour")],
                "eng-US": [caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello")],
            }
        }
    ) == "Hello"
    assert extract_with_info(
        {
            "subtitles": {
                "fr": [caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nBonjour")]
            }
        }
    ) == "Bonjour"


def test_longest_usable_candidate_is_selected_and_malformed_candidates_are_skipped():
    transcript = extract_with_info(
        {
            "subtitles": {
                "en": [
                    caption_entry("{not json", ext="json3"),
                    caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nShort"),
                    caption_entry(
                        "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nA much longer usable caption"
                    ),
                ]
            }
        }
    )

    assert transcript == "A much longer usable caption"


def test_caption_parsers_preserve_existing_cleanup_rules():
    assert extract_with_info(
        {
            "subtitles": {
                "en": [
                    caption_entry(
                        "\n".join(
                            [
                                "WEBVTT",
                                "NOTE metadata",
                                "00:00:00.000 --> 00:00:01.000",
                                "Hello <b>there</b>",
                                "Hello <b>there</b>",
                                "Second &amp; line",
                            ]
                        )
                    )
                ]
            }
        }
    ) == "Hello there Second & line"
    assert extract_with_info(
        {
            "subtitles": {
                "en": [
                    caption_entry(
                        "1\n00:00:00,000 --> 00:00:01,000\nFirst\n\n2\n00:00:01,000 --> 00:00:02,000\nSecond",
                        ext="srt",
                    )
                ]
            }
        }
    ) == "First Second"
    assert extract_with_info(
        {
            "subtitles": {
                "en": [
                    caption_entry(
                        '{"events":[{"segs":[{"utf8":"Again"},{"utf8":"Again"},{"utf8":" done"}]}]}',
                        ext="json3",
                    )
                ]
            }
        }
    ) == "Again done"
    assert extract_with_info(
        {
            "subtitles": {
                "en": [
                    caption_entry("00:00:00.000 --> 00:00:01.000\nSrv line", ext="srv3")
                ]
            }
        }
    ) == "Srv line"


def test_caption_fetch_failure_description_title_and_empty_exception_fallbacks():
    def broken_get(url, timeout=10):
        raise RuntimeError("caption unavailable")

    assert extract_with_info(
        {
            "title": "Title fallback",
            "description": "Description fallback",
            "subtitles": {"en": [{"ext": "vtt", "url": "https://caption.test/file.vtt"}]},
        },
        requests_get=broken_get,
    ) == "Description fallback"
    assert extract_with_info(
        {
            "title": "Title fallback",
            "description": "",
            "subtitles": {"en": [caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000")]},
        }
    ) == "Title fallback"
    with pytest.raises(Exception, match=content.NO_TIKTOK_TRANSCRIPT_ERROR):
        extract_with_info(
            {
                "title": "",
                "description": "",
                "subtitles": {"en": [caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000")]},
            }
        )


def test_caption_url_fetch_uses_existing_timeout_without_real_network():
    calls = {}

    def fake_get(url, timeout=10):
        calls["url"] = url
        calls["timeout"] = timeout
        return FakeResponse("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nFetched")

    assert extract_with_info(
        {"subtitles": {"en": [{"ext": "vtt", "url": "https://caption.test/file.vtt"}]}},
        requests_get=fake_get,
    ) == "Fetched"
    assert calls == {"url": "https://caption.test/file.vtt", "timeout": 10}


def test_tiktok_diagnostics_are_safe(caplog):
    caplog.set_level(logging.INFO, logger="smu_core.services.content")

    extract_with_info(
        {
            "title": "SECRET TITLE",
            "description": "SECRET DESCRIPTION",
            "subtitles": {
                "en": [caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nSECRET TRANSCRIPT")]
            },
        }
    )

    output = caplog.text
    reached_contexts = [
        getattr(record, "smu_context", {})
        for record in caplog.records
        if record.message == "tiktok_transcript_helper_reached"
    ]
    final_contexts = [
        getattr(record, "smu_context", {})
        for record in caplog.records
        if record.message == "tiktok_transcript_final_diagnostics"
    ]

    assert "https://www.tiktok.com/@user/video/123" not in output
    assert "token=secret" not in output
    assert "SECRET TITLE" not in output
    assert "SECRET DESCRIPTION" not in output
    assert "SECRET TRANSCRIPT" not in output
    assert reached_contexts
    assert reached_contexts[0]["url_hostname"] == "www.tiktok.com"
    assert final_contexts
    assert final_contexts[0]["final_transcript_length"] == len("SECRET TRANSCRIPT")


def test_generate_content_pack_prompt_model_and_missing_key_behaviour():
    client = FakeOpenAIClient()

    assert content.generate_content_pack(
        "Transcript text",
        "Brand context",
        openai_api_key="key",
        openai_client=client,
    ) == "PACK OUTPUT"
    call = client.calls[0]

    assert call["model"] == "gpt-4.1-mini"
    assert set(call.keys()) == {"model", "input"}
    assert "Brand Brief:\nBrand context" in call["input"]
    assert "Source content:\nTranscript text" in call["input"]
    assert "INSTAGRAM_CAPTION:" in call["input"]
    assert "REDDIT_POST:" in call["input"]

    with pytest.raises(Exception, match="OPENAI_API_KEY is missing"):
        content.generate_content_pack(
            "Transcript text",
            "Brand context",
            openai_api_key="",
            openai_client=client,
        )


def test_content_pack_section_extraction_image_style_and_placeholder_behaviour():
    pack = """INSTAGRAM_CAPTION:
Instagram caption

FACEBOOK_POST:
Facebook caption

REDDIT_POST:
Reddit caption

X_POST:
X caption

HASHTAGS:
#one
"""

    assert content.extract_content_pack_section(pack, "INSTAGRAM_CAPTION") == "Instagram caption"
    assert content.extract_content_pack_section(pack, "REDDIT_POST") == "Reddit caption"
    assert content.extract_content_pack_section(pack, "MISSING") == ""
    assert content.apply_image_style("Prompt", "unknown") == "Prompt"
    styled = content.apply_image_style("Prompt", "viral_carousel")
    assert "Style: viral Instagram business carousel" in styled
    assert "- square 1:1 format" in styled
    assert content.get_placeholder_image_url() == content.PLACEHOLDER_IMAGE_URL
