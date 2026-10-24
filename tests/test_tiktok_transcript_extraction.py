import logging
import json
import re
from pathlib import Path

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
    def __init__(self, text, url=None):
        self.text = text
        self.url = url

    def raise_for_status(self):
        return None

    def close(self):
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


class FakeTranscriptionResponse:
    text = "Transcribed TikTok audio"


class FakeOpenAIClient:
    def __init__(self, response=None, error=None):
        self.audio = self
        self.transcriptions = self
        self.calls = []
        self.response = response or FakeTranscriptionResponse()
        self.error = error

    def create(self, **kwargs):
        self.calls.append(kwargs)

        if self.error:
            raise self.error

        return self.response


class DownloadingYoutubeDL:
    calls = []
    downloaded_path = None

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, url, download=False):
        self.__class__.calls.append(
            {"url": url, "download": download, "options": self.options}
        )

        if not download:
            return {"title": "", "description": ""}

        output_template = self.options["outtmpl"]
        file_path = Path(
            output_template.replace("%(id)s", "123").replace("%(ext)s", "mp4")
        )
        file_path.write_bytes(b"fake tiktok media")
        self.__class__.downloaded_path = file_path
        return {"id": "123", "ext": "mp4"}


class MetadataOnlyYoutubeDL:
    calls = []

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, url, download=False):
        self.__class__.calls.append({"url": url, "download": download})

        if download:
            raise AssertionError("fallback download should not occur")

        return {"title": "", "description": "Metadata transcript"}


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


def test_transcript_extraction_logs_safe_fallback_metadata(
    module, monkeypatch, caplog
):
    set_tiktok_info(
        module,
        monkeypatch,
        {
            "title": "Title fallback",
            "description": "Description fallback",
        },
    )
    block_real_caption_fetch(module, monkeypatch)
    caplog.set_level(logging.INFO, logger="smu_core.services.content")

    assert extract(module) == "Description fallback"

    assert "Description fallback" not in caplog.text
    assert "Title fallback" not in caplog.text
    assert "https://www.tiktok.com/@user/video/123" not in caplog.text
    contexts = [
        getattr(record, "smu_context", {})
        for record in caplog.records
        if record.message == "tiktok_transcript_final_diagnostics"
    ]
    assert contexts
    assert contexts[0]["fallback_source"] == "description"
    assert contexts[0]["final_transcript_length"] == len("Description fallback")


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


def test_short_tiktok_url_is_resolved_before_metadata_extraction(module):
    MetadataOnlyYoutubeDL.calls = []
    response = FakeResponse("", url="https://www.tiktok.com/@user/video/123")
    calls = {}

    def fake_get(url, timeout=10, allow_redirects=False):
        calls["url"] = url
        calls["timeout"] = timeout
        calls["allow_redirects"] = allow_redirects
        return response

    transcript = module.content_service.extract_tiktok_transcript(
        "https://vm.tiktok.com/ZMtest/",
        youtube_dl_cls=MetadataOnlyYoutubeDL,
        requests_get=fake_get,
    )

    assert transcript == "Metadata transcript"
    assert calls == {
        "url": "https://vm.tiktok.com/ZMtest/",
        "timeout": 10,
        "allow_redirects": True,
    }
    assert MetadataOnlyYoutubeDL.calls[0]["url"] == "https://www.tiktok.com/@user/video/123"
    assert MetadataOnlyYoutubeDL.calls[0]["download"] is False


def test_metadata_empty_triggers_transcription_fallback(module):
    DownloadingYoutubeDL.calls = []
    DownloadingYoutubeDL.downloaded_path = None
    client = FakeOpenAIClient()

    transcript = module.content_service.extract_tiktok_transcript(
        "https://www.tiktok.com/@user/video/123",
        youtube_dl_cls=DownloadingYoutubeDL,
        requests_get=lambda url, timeout=10: FakeResponse(""),
        openai_api_key="test-key",
        openai_client=client,
    )

    assert transcript == "Transcribed TikTok audio"
    assert [call["download"] for call in DownloadingYoutubeDL.calls] == [False, True]
    assert client.calls[0]["model"] == module.content_service.TIKTOK_TRANSCRIPTION_MODEL


def test_transcription_fallback_cleans_up_temp_files(module):
    DownloadingYoutubeDL.calls = []
    DownloadingYoutubeDL.downloaded_path = None

    assert module.content_service.extract_tiktok_transcript(
        "https://www.tiktok.com/@user/video/123",
        youtube_dl_cls=DownloadingYoutubeDL,
        requests_get=lambda url, timeout=10: FakeResponse(""),
        openai_api_key="test-key",
        openai_client=FakeOpenAIClient(),
    ) == "Transcribed TikTok audio"

    assert DownloadingYoutubeDL.downloaded_path is not None
    assert not DownloadingYoutubeDL.downloaded_path.exists()


def test_transcription_fallback_failure_preserves_existing_exception(module):
    with pytest.raises(Exception, match="No transcript or usable text found for this TikTok."):
        module.content_service.extract_tiktok_transcript(
            "https://www.tiktok.com/@user/video/123",
            youtube_dl_cls=DownloadingYoutubeDL,
            requests_get=lambda url, timeout=10: FakeResponse(""),
            openai_api_key="test-key",
            openai_client=FakeOpenAIClient(error=RuntimeError("transcription failed")),
        )


def test_missing_openai_key_does_not_download_media(module):
    MetadataOnlyYoutubeDL.calls = []

    transcript = module.content_service.extract_tiktok_transcript(
        "https://www.tiktok.com/@user/video/123",
        youtube_dl_cls=MetadataOnlyYoutubeDL,
        requests_get=lambda url, timeout=10: FakeResponse(""),
        openai_api_key="",
        openai_client=FakeOpenAIClient(),
    )

    assert transcript == "Metadata transcript"
    assert [call["download"] for call in MetadataOnlyYoutubeDL.calls] == [False]


def test_no_fallback_download_when_metadata_succeeds(module):
    MetadataOnlyYoutubeDL.calls = []

    assert module.content_service.extract_tiktok_transcript(
        "https://www.tiktok.com/@user/video/123",
        youtube_dl_cls=MetadataOnlyYoutubeDL,
        requests_get=lambda url, timeout=10: FakeResponse(""),
        openai_api_key="test-key",
        openai_client=FakeOpenAIClient(),
    ) == "Metadata transcript"

    assert [call["download"] for call in MetadataOnlyYoutubeDL.calls] == [False]


def extraction_events(caplog):
    return [json.loads(record.getMessage()) for record in caplog.records
            if record.name == "smu.tiktok_extraction"]


@pytest.mark.parametrize("failure,stage,outcome", [
    ("url", "url_validation", "failed"),
    ("redirect", "shortlink_resolution", "failed"),
    ("metadata", "metadata_extraction", "failed"),
    ("captions", "caption_selection", "failed"),
    ("configuration", "transcription_configuration", "failed"),
    ("download", "media_download", "failed"),
    ("missing_file", "media_download", "failed"),
    ("transcription", "transcription", "failed"),
    ("empty", "transcription", "empty_transcription"),
])
def test_extraction_diagnostics_are_correlated_and_secret_safe(
    module, caplog, capsys, failure, stage, outcome,
):
    caplog.set_level(logging.INFO)
    secret = "PRIVATE cookie=private-cookie Authorization: private-key private-transcript"
    signed_url = "https://www.tiktok.com/@private/video/123?token=private-token"
    calls = []

    class Downloader(DownloadingYoutubeDL):
        def extract_info(self, url, download=False):
            calls.append(download)
            self.options["logger"].debug(secret)
            self.options["logger"].warning(signed_url)
            self.options["logger"].error(secret)
            if not download:
                if failure == "metadata":
                    return {"title": None}
                if failure == "captions":
                    return {"subtitles": ["invalid container"]}
                return {"title": "", "description": ""}
            if failure == "download":
                raise RuntimeError(secret + signed_url)
            if failure == "missing_file":
                return {}
            return super().extract_info(url, download=True)

    client = FakeOpenAIClient(error=RuntimeError(secret + signed_url)
                             if failure == "transcription" else None)
    if failure == "empty":
        client.response = {"text": " \n\t"}
    url = ("http://[invalid" if failure == "url" else
           "https://vm.tiktok.com/private-token" if failure == "redirect" else signed_url)
    with pytest.raises(Exception):
        module.content_service.extract_tiktok_transcript(
            url, youtube_dl_cls=Downloader,
            requests_get=lambda *a, **k: FakeResponse("", url="https://outside.test/private-token"),
            openai_api_key="" if failure == "configuration" else "private-key",
            openai_client=client,
        )
    events = extraction_events(caplog)
    assert len(events) == 2
    assert events[0]["outcome"] == "started"
    assert events[-1]["stage"] == stage
    assert events[-1]["outcome"] == outcome
    assert events[0]["correlation_id"] == events[-1]["correlation_id"]
    assert re.fullmatch(r"[0-9a-f]{32}", events[0]["correlation_id"])
    assert all(set(event) == {"event", "stage", "outcome", "correlation_id"}
               for event in events)
    emitted = caplog.text + capsys.readouterr().err
    for forbidden in (secret, signed_url, "private-token", "private-cookie", "private-key", "private-transcript"):
        assert forbidden not in emitted
        assert forbidden not in str([getattr(r, "smu_context", {}) for r in caplog.records])
    assert len(client.calls) == (1 if failure in {"transcription", "empty"} else 0)
    assert calls.count(True) <= 1


def test_metadata_failure_and_transcription_success_share_correlation(module, caplog):
    caplog.set_level(logging.INFO)

    class Downloader(DownloadingYoutubeDL):
        def extract_info(self, url, download=False):
            if not download:
                raise RuntimeError("private raw downloader error")
            return super().extract_info(url, download=True)

    client = FakeOpenAIClient()
    result = module.content_service.extract_tiktok_transcript(
        "https://www.tiktok.com/@user/video/123", youtube_dl_cls=Downloader,
        openai_api_key="dummy", openai_client=client,
    )
    assert result == FakeTranscriptionResponse.text
    events = extraction_events(caplog)
    assert [(event["stage"], event["outcome"]) for event in events] == [
        ("url_validation", "started"), ("metadata_extraction", "failed"),
        ("transcription", "succeeded"),
    ]
    assert len({event["correlation_id"] for event in events}) == 1
    assert "private raw downloader error" not in caplog.text
    assert len(client.calls) == 1


def test_metadata_success_uses_unique_correlation_per_call(module, caplog):
    caplog.set_level(logging.INFO)
    for _ in range(2):
        assert module.content_service.extract_tiktok_transcript(
            "https://www.tiktok.com/@user/video/123", youtube_dl_cls=MetadataOnlyYoutubeDL,
        ) == "Metadata transcript"
    events = extraction_events(caplog)
    assert [event["outcome"] for event in events] == ["started", "succeeded"] * 2
    assert events[0]["correlation_id"] == events[1]["correlation_id"]
    assert events[2]["correlation_id"] == events[3]["correlation_id"]
    assert events[0]["correlation_id"] != events[2]["correlation_id"]


@pytest.mark.parametrize("correlation,stage,outcome", [
    ("private URL", "transcription", "failed"),
    ("a" * 32, "private cookie", "failed"),
    ("a" * 32, "transcription", "private transcript"),
])
def test_diagnostic_rejects_non_allowlisted_fields(module, caplog, correlation, stage, outcome):
    with pytest.raises(ValueError):
        module.content_service._log_tiktok_extraction(correlation, stage, outcome)
    assert not extraction_events(caplog)


def test_extraction_json_reaches_application_file_and_stderr(module, monkeypatch, tmp_path, capsys):
    smu_logger = logging.getLogger("smu")
    diagnostic_logger = logging.getLogger("smu.tiktok_extraction")
    monkeypatch.setattr(module, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(smu_logger, "handlers", [])
    monkeypatch.setattr(diagnostic_logger, "handlers", [])
    module.configure_logging()
    module.configure_logging()
    try:
        assert len(smu_logger.handlers) == len(diagnostic_logger.handlers) == 1
        module.content_service.extract_tiktok_transcript(
            "https://www.tiktok.com/@user/video/123", youtube_dl_cls=MetadataOnlyYoutubeDL,
        )
        with pytest.raises(Exception):
            module.content_service.extract_tiktok_transcript(
                "http://[invalid", youtube_dl_cls=MetadataOnlyYoutubeDL,
            )
        file_events = [json.loads(line) for line in (tmp_path / "logs/smu.log").read_text().splitlines()]
        stderr_events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
        assert file_events == stderr_events
        assert [event["outcome"] for event in file_events] == ["started", "succeeded", "started", "failed"]
        assert file_events[0]["correlation_id"] == file_events[1]["correlation_id"]
        assert file_events[2]["correlation_id"] == file_events[3]["correlation_id"]
        assert file_events[3]["stage"] == "url_validation"
    finally:
        for handler in smu_logger.handlers + diagnostic_logger.handlers:
            handler.close()
