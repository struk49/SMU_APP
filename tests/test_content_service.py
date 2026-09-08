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
    assert calls["extract"]["openai_api_key"] == smu_app.OPENAI_API_KEY
    assert calls["extract"]["openai_client"] is smu_app.openai_client
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
        "socket_timeout": 20,
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
    assert call["input"].count("/human") == 1
    assert "Brand Brief:\nBrand context" in call["input"]
    assert "Source content:\nTranscript text" in call["input"]
    assert "INSTAGRAM_CAPTION:" in call["input"]
    assert "REDDIT_POST:" in call["input"]
    assert "ONLY when the source genuinely teaches vocabulary" in call["input"]
    assert "rather than combining both into one field" in " ".join(
        call["input"].split()
    )
    assert "do not use emoji" in call["input"]
    assert "text-free scene or composition" in call["input"]
    assert len(client.calls) == 1

    with pytest.raises(Exception, match="OPENAI_API_KEY is missing"):
        content.generate_content_pack(
            "Transcript text",
            "Brand context",
            openai_api_key="",
            openai_client=client,
        )


def test_content_pack_prompt_is_source_faithful_and_platform_native():
    client = FakeOpenAIClient()
    content.generate_content_pack(
        "Representative source",
        "Natural brand voice",
        openai_api_key="key",
        openai_client=client,
    )
    prompt = client.calls[0]["input"]

    assert "Never invent facts, statistics, testimonials" in prompt
    assert "Never turn uncertainty into a factual claim" in prompt
    assert "Preserve important names, terminology, and supplied facts" in prompt
    assert "Instagram: use a strong first-line hook" in prompt
    assert "Facebook: provide more context or story" in prompt
    assert "Do not copy the Instagram caption verbatim" in prompt
    assert "LinkedIn: be professional but human" in prompt
    assert "Pinterest: provide a concise discovery/search-oriented" in prompt
    assert "Reddit: lead with context and genuine discussion" in prompt
    assert "X: focus on one strong supported idea" in prompt
    assert "Threads:" not in prompt


def test_content_pack_prompt_requires_variety_and_natural_writing():
    client = FakeOpenAIClient()
    content.generate_content_pack(
        "Representative source",
        openai_api_key="key",
        openai_client=client,
    )
    prompt = client.calls[0]["input"]
    normalized_prompt = " ".join(prompt.split())

    assert "distinct content opportunity, not a resized rewrite" in prompt
    assert "Do not repeat the same opening or default CTA everywhere" in prompt
    assert "generic AI openings" in prompt
    assert "In today's fast-paced world" in prompt
    assert "Unlock the power of" in prompt
    assert "A CTA is optional" in prompt
    assert "Never invent an offer" in normalized_prompt


def test_content_pack_prompt_separates_carousel_copy_from_caption_copy():
    client = FakeOpenAIClient()
    content.generate_content_pack(
        "Representative source",
        openai_api_key="key",
        openai_client=client,
    )
    prompt = client.calls[0]["input"]
    normalized_prompt = " ".join(prompt.split())

    assert "ONE PRIMARY IDEA PER SLIDE" in prompt
    assert "Use 2 to 6 `Slide N:` structural blocks" in prompt
    assert "without filler" in prompt
    assert "Image copy must be fast to understand, minimal, swipeable" in prompt
    assert "Caption copy carries context, explanation, story" in prompt
    assert "must complement rather than duplicate the carousel" in normalized_prompt
    assert "cover uses Title and optional Subtitle" in prompt
    assert "use Phrase," in prompt
    assert "Translation, optional Tip" in prompt
    assert "use Title, optional Body, optional CTA, and Visual" in prompt
    assert "A closing CTA is one short action" in prompt
    assert "Visual describes only a simple, relevant, text-free scene" in prompt
    assert "Never put exact overlay copy in Visual" in prompt
    assert "do not use emoji, decorative symbols, or icon glyphs" in prompt


def test_content_pack_prompt_classifies_source_without_exposing_classification():
    client = FakeOpenAIClient()
    content.generate_content_pack(
        "SMU is improving its content generator.",
        openai_api_key="key",
        openai_client=client,
    )
    prompt = client.calls[0]["input"]
    normalized_prompt = " ".join(prompt.split())

    for category in (
        "Product / SaaS",
        "Educational",
        "Tutorial / How-to",
        "Build in Public",
        "Story",
        "Opinion",
        "Announcement",
        "List / Tips",
        "Vocabulary / Language Learning",
        "Community / Engagement",
    ):
        assert category in prompt
    assert "Silently choose exactly one category" in prompt
    assert "classification is internal only" in normalized_prompt
    assert "Never name or expose it in the output" in prompt


def test_content_pack_prompt_gates_vocabulary_fields_by_semantic_category():
    client = FakeOpenAIClient()
    content.generate_content_pack(
        "Representative source",
        openai_api_key="key",
        openai_client=client,
    )
    prompt = " ".join(client.calls[0]["input"].split())

    assert "ONLY when the source genuinely teaches vocabulary" in prompt
    assert "use Phrase, Translation, optional Tip, and Visual" in prompt
    assert "For every other category" in prompt
    assert "use Title, optional Body, optional CTA, and Visual" in prompt
    assert "Never use Phrase or Translation for these categories" in prompt
    assert "SMU topics all use this general Title/Body structure" in prompt


def test_content_pack_prompt_preserves_tense_and_rejects_generic_claims():
    client = FakeOpenAIClient()
    content.generate_content_pack(
        "I'm improving SMU so it understands source text.",
        openai_api_key="key",
        openai_client=client,
    )
    prompt = " ".join(client.calls[0]["input"].split())

    assert "Preserve the source tense" in prompt
    assert "must not be rewritten as completed or proven" in prompt
    for banned_phrase in (
        "One-size-fits-all",
        "Work smarter, not harder",
        "Game-changer",
        "Unlock the power of",
        "Take your content to the next level",
        "In today's world",
        "Revolutionary",
        "Amazing",
    ):
        assert banned_phrase in prompt
    assert "Replace generic claims with concrete source observations" in prompt


def test_content_pack_prompt_enforces_semantic_flow_and_copy_limits():
    client = FakeOpenAIClient()
    content.generate_content_pack(
        "Representative source",
        openai_api_key="key",
        openai_client=client,
    )
    prompt = " ".join(client.calls[0]["input"].split())

    assert "Use 2 to 6 `Slide N:` structural blocks" in prompt
    assert "Body is normally one concise sentence" in prompt
    assert "A closing CTA is one short action, never a paragraph" in prompt
    assert "educational content moves from hook to lesson" in prompt
    assert "products move from problem to solution" in prompt
    assert "stories move from situation to challenge" in prompt
    assert "vocabulary moves from cover through distinct terms" in prompt
    assert "Never reuse the same hook across platforms" in prompt
    assert "six outputs differ in supported hook, structure, CTA, tone, length" in prompt
    assert "specific claims grounded in the source" in prompt
    assert "concrete source-backed observations" in prompt
    assert "Start creating smarter" in prompt
    assert "Get started today" in prompt
    assert "Refer to a concrete next action" in prompt


@pytest.mark.parametrize(
    "source_text",
    [
        (
            "I've been building SMU to make social media content creation easier. "
            "The idea is that you can start with one topic or piece of source content "
            "and turn it into posts for different social platforms. One thing I've "
            "learned while building it is that simply rewriting the same post for every "
            "platform isn't good enough. Instagram, Facebook, LinkedIn, Pinterest, "
            "Reddit and X all need different types of content. I'm now improving SMU so "
            "it understands the source first, finds the strongest ideas, and creates "
            "content specifically for each platform."
        ),
        "Teach the Polish phrase 'Miłego dnia' and explain that it means 'Have a nice day'.",
        "Explain why retrieval practice helps learners remember factual material.",
        "A scheduling product groups campaign tasks so a team can review work together.",
        "The museum opened in 1982 and its archive contains regional transport records.",
    ],
)
def test_content_pack_creative_director_rules_cover_representative_sources(source_text):
    client = FakeOpenAIClient()
    content.generate_content_pack(
        source_text,
        "Clear, evidence-led voice",
        openai_api_key="key",
        openai_client=client,
    )

    assert len(client.calls) == 1
    prompt = client.calls[0]["input"]
    normalized = " ".join(prompt.split())
    assert f"Source content:\n{source_text}" in prompt
    assert "Creative-director planning (internal only)" in prompt
    assert "one visual story with a deliberate beginning, progression" in normalized
    assert "Every slide must advance the idea" in prompt
    assert "3-6 word cover title" in prompt
    assert "2-6 word internal headline" in prompt
    assert "0-12 supporting words" in prompt
    assert "parser metadata, not customer-visible copy" in normalized
    assert "only for genuine steps, rankings, defined lists" in normalized
    assert "Vary adjacent Visual concepts meaningfully" in prompt
    assert "one shared, text-free carousel art direction" in prompt
    assert "Never put exact overlay copy in Visual" in prompt
    assert "Do not output the planning" in prompt


def test_creative_director_preserves_build_in_public_tense_and_avoids_hardcoded_example():
    client = FakeOpenAIClient()
    source = "I'm improving SMU so it can understand source content before writing posts."
    content.generate_content_pack(
        source,
        openai_api_key="key",
        openai_client=client,
    )
    prompt = client.calls[0]["input"]

    assert "build-in-public content moves from observed problem to learning" in prompt
    assert "honest forward-looking close" in prompt
    assert "must not be rewritten as completed or proven" in prompt
    assert "STOP COPYING YOUR POSTS" not in prompt


def test_creative_director_requires_scene_variety_and_campaign_consistency():
    client = FakeOpenAIClient()
    content.generate_content_pack(
        "A supported educational source.",
        openai_api_key="key",
        openai_client=client,
    )
    prompt = " ".join(client.calls[0]["input"].split())

    assert "adjacent slides repeat substantially the same claim" in prompt
    assert "person at a laptop, desk, generic phone, meeting" in prompt
    assert "same desk/laptop scene" in prompt
    assert "coherent visual medium, controlled palette, lighting" in prompt
    assert "without unexpectedly changing visual medium" in prompt


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
