import pytest


class FakeYoutubeDL:
    info = {}

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, url, download=False):
        return self.info


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


def caption_entry(text, ext="vtt"):
    return {"ext": ext, "data": text}


def set_tiktok_info(module, monkeypatch, info):
    FakeYoutubeDL.info = info
    monkeypatch.setattr(module, "YoutubeDL", FakeYoutubeDL)


def block_real_caption_fetch(module, monkeypatch):
    def fail_get(url, timeout=10):
        raise AssertionError("real caption fetch should not occur in tests")

    monkeypatch.setattr(module.requests, "get", fail_get)


def extract(module):
    return module.extract_tiktok_transcript("https://www.tiktok.com/@user/video/123")


def test_requested_subtitles_are_preferred(module, monkeypatch):
    set_tiktok_info(
        module,
        monkeypatch,
        {
            "title": "Title fallback",
            "description": "Description fallback",
            "requested_subtitles": {
                "en": caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nRequested")
            },
            "subtitles": {
                "en": [caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nSubtitle")]
            },
        },
    )
    block_real_caption_fetch(module, monkeypatch)

    assert extract(module) == "Requested"


def test_subtitles_are_used_when_requested_subtitles_are_absent(module, monkeypatch):
    set_tiktok_info(
        module,
        monkeypatch,
        {
            "title": "Title fallback",
            "description": "Description fallback",
            "subtitles": {
                "en": [caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nSubtitle")]
            },
            "automatic_captions": {
                "en": [caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nAuto")]
            },
        },
    )
    block_real_caption_fetch(module, monkeypatch)

    assert extract(module) == "Subtitle"


def test_automatic_captions_are_used_as_next_fallback(module, monkeypatch):
    set_tiktok_info(
        module,
        monkeypatch,
        {
            "title": "Title fallback",
            "description": "Description fallback",
            "automatic_captions": {
                "en": [caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nAuto")]
            },
        },
    )
    block_real_caption_fetch(module, monkeypatch)

    assert extract(module) == "Auto"


def test_english_caption_language_is_preferred(module, monkeypatch):
    set_tiktok_info(
        module,
        monkeypatch,
        {
            "subtitles": {
                "fr": [caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nBonjour")],
                "en-US": [caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello")],
            },
        },
    )
    block_real_caption_fetch(module, monkeypatch)

    assert extract(module) == "Hello"


def test_first_usable_language_is_selected_when_english_is_absent(module, monkeypatch):
    set_tiktok_info(
        module,
        monkeypatch,
        {
            "subtitles": {
                "fr": [caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nBonjour")]
            },
        },
    )
    block_real_caption_fetch(module, monkeypatch)

    assert extract(module) == "Bonjour"


def test_vtt_parsing_removes_headers_timestamps_and_duplicate_fragments(
    module, monkeypatch
):
    set_tiktok_info(
        module,
        monkeypatch,
        {
            "subtitles": {
                "en": [
                    caption_entry(
                        "\n".join(
                            [
                                "WEBVTT",
                                "",
                                "00:00:00.000 --> 00:00:01.000",
                                "Hello <b>there</b>",
                                "Hello <b>there</b>",
                                "Second &amp; line",
                            ]
                        )
                    )
                ]
            },
        },
    )
    block_real_caption_fetch(module, monkeypatch)

    assert extract(module) == "Hello there Second & line"


def test_srt_parsing_removes_sequence_numbers_and_timestamps(module, monkeypatch):
    set_tiktok_info(
        module,
        monkeypatch,
        {
            "subtitles": {
                "en": [
                    caption_entry(
                        "\n".join(
                            [
                                "1",
                                "00:00:00,000 --> 00:00:01,000",
                                "First line",
                                "",
                                "2",
                                "00:00:01,000 --> 00:00:02,000",
                                "Second line",
                            ]
                        ),
                        ext="srt",
                    )
                ]
            },
        },
    )
    block_real_caption_fetch(module, monkeypatch)

    assert extract(module) == "First line Second line"


def test_json3_parsing_extracts_text(module, monkeypatch):
    set_tiktok_info(
        module,
        monkeypatch,
        {
            "subtitles": {
                "en": [
                    caption_entry(
                        '{"events":[{"segs":[{"utf8":"Hello"},{"utf8":" "},{"utf8":"world"}]}]}',
                        ext="json3",
                    )
                ]
            },
        },
    )
    block_real_caption_fetch(module, monkeypatch)

    assert extract(module) == "Hello world"


def test_duplicate_consecutive_caption_fragments_are_removed(module, monkeypatch):
    set_tiktok_info(
        module,
        monkeypatch,
        {
            "subtitles": {
                "en": [
                    caption_entry(
                        '{"events":[{"segs":[{"utf8":"Again"},{"utf8":"Again"},{"utf8":"Done"}]}]}',
                        ext="json3",
                    )
                ]
            },
        },
    )
    block_real_caption_fetch(module, monkeypatch)

    assert extract(module) == "Again Done"


def test_caption_fetch_failure_falls_back_to_description(module, monkeypatch):
    set_tiktok_info(
        module,
        monkeypatch,
        {
            "title": "Title fallback",
            "description": "Description fallback",
            "subtitles": {"en": [{"ext": "vtt", "url": "https://caption.test/file.vtt"}]},
        },
    )

    def fail_get(url, timeout=10):
        raise RuntimeError("caption unavailable")

    monkeypatch.setattr(module.requests, "get", fail_get)

    assert extract(module) == "Description fallback"


def test_empty_captions_fall_back_to_title(module, monkeypatch):
    set_tiktok_info(
        module,
        monkeypatch,
        {
            "title": "Title fallback",
            "description": "",
            "subtitles": {"en": [caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000")]},
        },
    )
    block_real_caption_fetch(module, monkeypatch)

    assert extract(module) == "Title fallback"


def test_existing_exception_remains_when_all_sources_are_empty(module, monkeypatch):
    set_tiktok_info(
        module,
        monkeypatch,
        {
            "title": "",
            "description": "",
            "subtitles": {"en": [caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000")]},
        },
    )
    block_real_caption_fetch(module, monkeypatch)

    with pytest.raises(Exception, match="No transcript or usable text found for this TikTok."):
        extract(module)


def test_caption_url_is_fetched_with_timeout_and_without_real_network(
    module, monkeypatch
):
    set_tiktok_info(
        module,
        monkeypatch,
        {
            "subtitles": {"en": [{"ext": "vtt", "url": "https://caption.test/file.vtt"}]},
        },
    )
    calls = {}

    def fake_get(url, timeout=10):
        calls["url"] = url
        calls["timeout"] = timeout
        return FakeResponse("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nFetched")

    monkeypatch.setattr(module.requests, "get", fake_get)

    assert extract(module) == "Fetched"
    assert calls == {"url": "https://caption.test/file.vtt", "timeout": 10}


def test_multiple_english_caption_entries_choose_longest_usable_transcript(
    module, monkeypatch
):
    set_tiktok_info(
        module,
        monkeypatch,
        {
            "subtitles": {
                "en": [
                    caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nShort"),
                    caption_entry(
                        "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n"
                        "A much longer complete caption"
                    ),
                ]
            },
        },
    )
    block_real_caption_fetch(module, monkeypatch)

    assert extract(module) == "A much longer complete caption"


def test_short_first_vtt_does_not_hide_longer_second_entry(module, monkeypatch):
    set_tiktok_info(
        module,
        monkeypatch,
        {
            "subtitles": {
                "eng-US": [
                    caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nTiny"),
                    caption_entry(
                        "\n".join(
                            [
                                "WEBVTT",
                                "",
                                "00:00:00.000 --> 00:00:01.000",
                                "This is the fuller subtitle track",
                                "with additional useful context",
                            ]
                        )
                    ),
                ]
            },
        },
    )
    block_real_caption_fetch(module, monkeypatch)

    assert extract(module) == (
        "This is the fuller subtitle track with additional useful context"
    )


def test_malformed_caption_candidates_are_skipped(module, monkeypatch):
    set_tiktok_info(
        module,
        monkeypatch,
        {
            "subtitles": {
                "en": [
                    caption_entry("{not valid json", ext="json3"),
                    caption_entry("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nRecovered"),
                ]
            },
        },
    )
    block_real_caption_fetch(module, monkeypatch)

    assert extract(module) == "Recovered"


def test_fallback_still_works_if_all_caption_candidates_fail(module, monkeypatch):
    set_tiktok_info(
        module,
        monkeypatch,
        {
            "title": "Title fallback",
            "description": "Description fallback",
            "subtitles": {
                "en": [
                    caption_entry("{not valid json", ext="json3"),
                    {"ext": "vtt", "url": "https://caption.test/broken.vtt"},
                ]
            },
        },
    )

    def fail_get(url, timeout=10):
        raise RuntimeError("caption unavailable")

    monkeypatch.setattr(module.requests, "get", fail_get)

    assert extract(module) == "Description fallback"
