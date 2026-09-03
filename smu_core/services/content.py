import html as html_parser
import json
import logging
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests
from yt_dlp import YoutubeDL


logger = logging.getLogger(__name__)
NO_TIKTOK_TRANSCRIPT_ERROR = "No transcript or usable text found for this TikTok."
PLACEHOLDER_IMAGE_URL = "https://res.cloudinary.com/demo/image/upload/w_1080,h_1080,c_fill,b_rgb:111111/l_text:Arial_60_bold:Generating%20Image,co_rgb:ffffff/sample.jpg"
TIKTOK_HOST_SUFFIX = "tiktok.com"
TIKTOK_SHORTLINK_HOSTS = {"vm.tiktok.com", "vt.tiktok.com"}
TIKTOK_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"


def get_placeholder_image_url():
    return PLACEHOLDER_IMAGE_URL


def apply_image_style(prompt, style):
    style_presets = {
        "realistic": """
Style: realistic social media image, high-quality photography, natural lighting, sharp details, professional composition.
""",
        "viral_carousel": """
Style: viral Instagram business carousel, dark background, bold typography, yellow highlight blocks, white headline text, green and blue accents, premium creator aesthetic, high contrast, clean infographic layout.
""",
        "luxury": """
Style: luxury brand aesthetic, premium editorial design, elegant lighting, rich contrast, high-end visual style, polished social media advert.
""",
        "minimal": """
Style: minimalist modern design, clean layout, soft neutral colours, lots of whitespace, premium simple composition.
""",
        "corporate": """
Style: professional corporate social media design, clean layout, trustworthy business aesthetic, polished presentation, modern branding.
""",
        "pixar": """
Style: charming 3D animated film look, colourful, soft cinematic lighting, expressive, polished family-friendly animation style.
""",
    }

    style_text = style_presets.get(style, "")

    if not style_text:
        return prompt

    return f"""
{prompt}

{style_text}

Important:
- square 1:1 format
- high quality
- visually clear
- suitable for Instagram and Facebook
"""


def clean_transcript_text(text):
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _safe_exception_message(error, *, max_length=180):
    message = re.sub(r"https?://\S+", "[url]", str(error or ""))
    message = re.sub(r"(?i)(api[_-]?key|token|authorization|cookie)=\S+", r"\1=[redacted]", message)
    message = re.sub(r"\s+", " ", message).strip()
    return message[:max_length]


def _hostname(value):
    return (urlparse(value or "").hostname or "").lower()


def _is_tiktok_hostname(hostname):
    return hostname == TIKTOK_HOST_SUFFIX or hostname.endswith(f".{TIKTOK_HOST_SUFFIX}")


def _is_tiktok_shortlink(tiktok_url):
    return _hostname(tiktok_url) in TIKTOK_SHORTLINK_HOSTS


def _resolve_tiktok_url(tiktok_url, requests_get):
    if not _is_tiktok_shortlink(tiktok_url):
        return tiktok_url

    response = requests_get(tiktok_url, timeout=10, allow_redirects=True)
    resolved_url = getattr(response, "url", tiktok_url)
    parsed = urlparse(resolved_url)

    close_response = getattr(response, "close", None)
    if callable(close_response):
        close_response()

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("TikTok shortlink resolved to an unsupported URL scheme.")

    if not _is_tiktok_hostname((parsed.hostname or "").lower()):
        raise ValueError("TikTok shortlink resolved outside TikTok.")

    return resolved_url


def _media_file_candidates(directory):
    return [
        path for path in Path(directory).glob("**/*")
        if path.is_file() and path.stat().st_size > 0
    ]


def _extract_transcription_text(response):
    if hasattr(response, "text"):
        return response.text

    if isinstance(response, dict):
        return response.get("text", "")

    return ""


def extract_tiktok_transcript(
    tiktok_url,
    *,
    youtube_dl_cls=YoutubeDL,
    requests_get=None,
    openai_api_key=None,
    openai_client=None,
):
    requests_get = requests_get or requests.get
    hostname = urlparse(tiktok_url).hostname or ""
    is_tiktok_url = _is_tiktok_hostname(hostname.lower())
    is_shortlink = _is_tiktok_shortlink(tiktok_url)
    logger.info(
        "tiktok_transcript_helper_reached",
        extra={
            "smu_context": {
                "helper_reached": True,
                "url_hostname": hostname,
                "appears_tiktok_url": is_tiktok_url,
                "is_shortlink": is_shortlink,
                "stage": "transcript_extraction",
            },
        },
    )

    try:
        extraction_url = _resolve_tiktok_url(tiktok_url, requests_get)
    except Exception as e:
        logger.warning(
            "tiktok_shortlink_resolution_failed",
            extra={
                "smu_context": {
                    "url_hostname": hostname,
                    "appears_tiktok_url": is_tiktok_url,
                    "is_shortlink": is_shortlink,
                    "exception_class": e.__class__.__name__,
                    "exception_message": _safe_exception_message(e),
                    "stage": "shortlink_resolution",
                },
            },
        )
        raise Exception(NO_TIKTOK_TRANSCRIPT_ERROR)

    def normalize_caption_fragment(value):
        value = html_parser.unescape(str(value or ""))
        value = re.sub(r"<[^>]+>", " ", value)
        value = re.sub(r"\s+", " ", value)
        return value.strip()

    def append_unique_fragment(fragments, value):
        fragment = normalize_caption_fragment(value)

        if fragment and (not fragments or fragments[-1] != fragment):
            fragments.append(fragment)

    def parse_json3_caption(caption_text):
        data = json.loads(caption_text)
        fragments = []

        for event in data.get("events", []):
            for segment in event.get("segs", []):
                append_unique_fragment(fragments, segment.get("utf8", ""))

        return clean_transcript_text(" ".join(fragments)), len(fragments)

    def parse_text_caption(caption_text):
        fragments = []

        for raw_line in caption_text.splitlines():
            line = raw_line.strip()

            if not line:
                continue

            if line.upper() == "WEBVTT":
                continue

            if line.upper().startswith(("NOTE", "STYLE", "REGION", "KIND:", "LANGUAGE:")):
                continue

            if re.match(r"^\d+$", line):
                continue

            if "-->" in line:
                continue

            append_unique_fragment(fragments, line)

        return clean_transcript_text(" ".join(fragments)), len(fragments)

    def parse_caption_text(caption_text, caption_format):
        normalized_format = (caption_format or "").lower()

        if normalized_format == "json3":
            return parse_json3_caption(caption_text)

        return parse_text_caption(caption_text)

    def caption_entry_format(entry):
        return (
            entry.get("ext")
            or entry.get("format")
            or entry.get("format_id")
            or ""
        )

    def caption_entry_is_supported(entry):
        caption_format = caption_entry_format(entry).lower()
        return (
            bool(entry.get("url") or entry.get("data"))
            and (
                caption_format in {"json3", "vtt", "srt"}
                or caption_format.startswith("srv")
            )
        )

    def caption_entries_for_language(container, language):
        value = container.get(language)

        if not value:
            return []

        if isinstance(value, dict):
            return [value]

        return [entry for entry in value if isinstance(entry, dict)]

    def caption_language_is_english(language):
        normalized = (language or "").lower().replace("_", "-")
        return normalized in {"en", "eng"} or normalized.startswith(("en-", "eng-"))

    def caption_source_diagnostics(source_name, container):
        available_languages = list(container.keys()) if container else []
        entry_counts = {}
        available_formats = {}

        for language in available_languages:
            entries = caption_entries_for_language(container, language)
            entry_counts[language] = len(entries)
            available_formats[language] = [
                caption_entry_format(entry) for entry in entries
            ]

        logger.info(
            "tiktok_caption_source_diagnostics",
            extra={
                "smu_context": {
                    "caption_source": source_name,
                    "available_languages": available_languages,
                    "caption_entries_per_language": entry_counts,
                    "available_formats_per_language": available_formats,
                    "stage": "caption_source_inspection",
                },
            },
        )

    def ordered_caption_languages(container):
        preferred_languages = ["en", "en-US", "en-GB"]
        available_languages = list(container.keys())
        language_lookup = {
            language.lower().replace("_", "-"): language
            for language in available_languages
        }
        ordered_languages = []

        for preferred in preferred_languages:
            found = language_lookup.get(preferred.lower())

            if found and found not in ordered_languages:
                ordered_languages.append(found)

        for language in available_languages:
            if (
                caption_language_is_english(language)
                and language not in ordered_languages
            ):
                ordered_languages.append(language)

        for language in available_languages:
            if language not in ordered_languages:
                ordered_languages.append(language)

        return ordered_languages

    def fetch_caption_text(entry):
        if entry.get("data") is not None:
            return entry.get("data", "")

        response = requests_get(entry["url"], timeout=10)

        if hasattr(response, "raise_for_status"):
            response.raise_for_status()

        return response.text

    def caption_candidate_result(source_name, language, index, entry):
        caption_format = caption_entry_format(entry)

        try:
            caption_text = fetch_caption_text(entry)
            transcript, fragment_count = parse_caption_text(caption_text, caption_format)
            byte_length = len(str(caption_text or "").encode("utf-8"))
            parsed_length = len(transcript)
            exception_class = None
        except Exception as e:
            transcript = ""
            fragment_count = 0
            byte_length = 0
            parsed_length = 0
            exception_class = e.__class__.__name__
            logger.warning(
                "tiktok_caption_candidate_parse_failed",
                extra={
                    "smu_context": {
                        "caption_parse_exception_class": exception_class,
                        "caption_format": caption_format,
                        "stage": "caption_parsing",
                    },
                },
            )

        logger.info(
            "tiktok_caption_candidate_diagnostics",
            extra={
                "smu_context": {
                    "caption_candidate_source": source_name,
                    "caption_candidate_language": language,
                    "caption_candidate_index": index,
                    "caption_candidate_format": caption_format,
                    "downloaded_caption_byte_length": byte_length,
                    "parsed_caption_fragment_count": fragment_count,
                    "parsed_caption_length": parsed_length,
                    "caption_candidate_exception_class": exception_class,
                    "stage": "caption_candidate",
                },
            },
        )

        return {
            "source": source_name,
            "language": language,
            "index": index,
            "entry": entry,
            "format": caption_format,
            "transcript": transcript,
            "parsed_length": parsed_length,
            "fragment_count": fragment_count,
            "byte_length": byte_length,
            "exception_class": exception_class,
        }

    def select_caption_from_container(source_name, container):
        caption_source_diagnostics(source_name, container)

        if not container:
            return None

        ordered_languages = ordered_caption_languages(container)
        english_languages = [
            language for language in ordered_languages
            if caption_language_is_english(language)
        ]
        candidate_languages = english_languages or ordered_languages
        candidates = []

        for language in candidate_languages:
            entries = caption_entries_for_language(container, language)

            for index, entry in enumerate(entries):
                if caption_entry_is_supported(entry):
                    candidates.append(
                        caption_candidate_result(source_name, language, index, entry)
                    )

        usable_candidates = [
            candidate for candidate in candidates if candidate["parsed_length"] > 0
        ]

        if not usable_candidates:
            return None

        return max(usable_candidates, key=lambda candidate: candidate["parsed_length"])

    def select_caption(info):
        caption_sources = [
            ("requested_subtitles", info.get("requested_subtitles", {})),
            ("subtitles", info.get("subtitles", {})),
            ("automatic_captions", info.get("automatic_captions", {})),
        ]

        for source_name, container in caption_sources:
            selection = select_caption_from_container(source_name, container)

            if selection:
                return selection

        return None

    def transcribe_downloaded_media():
        if not openai_api_key or not openai_client:
            logger.warning(
                "tiktok_transcription_unavailable",
                extra={
                    "smu_context": {
                        "url_hostname": hostname,
                        "appears_tiktok_url": is_tiktok_url,
                        "is_shortlink": is_shortlink,
                        "stage": "transcription_config",
                        "failure_category": "openai_configuration_missing",
                    },
                },
            )
            raise Exception(NO_TIKTOK_TRANSCRIPT_ERROR)

        with tempfile.TemporaryDirectory(prefix="smu-tiktok-") as temp_dir:
            output_template = str(Path(temp_dir) / "tiktok-%(id)s.%(ext)s")
            download_opts = {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "overwrites": True,
                "format": "bestaudio/best[filesize<50M]/best",
                "outtmpl": output_template,
                "socket_timeout": 20,
            }

            try:
                with youtube_dl_cls(download_opts) as ydl:
                    download_info = ydl.extract_info(extraction_url, download=True)
            except Exception as e:
                logger.error(
                    "tiktok_media_download_failed",
                    extra={
                        "smu_context": {
                            "url_hostname": hostname,
                            "appears_tiktok_url": is_tiktok_url,
                            "is_shortlink": is_shortlink,
                            "exception_class": e.__class__.__name__,
                            "exception_message": _safe_exception_message(e),
                            "stage": "media_download",
                        },
                    },
                )
                raise Exception(NO_TIKTOK_TRANSCRIPT_ERROR)

            media_files = _media_file_candidates(temp_dir)
            media_file = max(media_files, key=lambda path: path.stat().st_size) if media_files else None

            if not media_file:
                logger.error(
                    "tiktok_media_download_missing_file",
                    extra={
                        "smu_context": {
                            "url_hostname": hostname,
                            "appears_tiktok_url": is_tiktok_url,
                            "is_shortlink": is_shortlink,
                            "download_returned_info": download_info is not None,
                            "stage": "media_download",
                        },
                    },
                )
                raise Exception(NO_TIKTOK_TRANSCRIPT_ERROR)

            try:
                with media_file.open("rb") as audio_file:
                    response = openai_client.audio.transcriptions.create(
                        model=TIKTOK_TRANSCRIPTION_MODEL,
                        file=audio_file,
                    )
            except Exception as e:
                logger.error(
                    "tiktok_transcription_failed",
                    extra={
                        "smu_context": {
                            "url_hostname": hostname,
                            "appears_tiktok_url": is_tiktok_url,
                            "is_shortlink": is_shortlink,
                            "exception_class": e.__class__.__name__,
                            "exception_message": _safe_exception_message(e),
                            "downloaded_media_extension": media_file.suffix.lower(),
                            "downloaded_media_size_bytes": media_file.stat().st_size,
                            "transcription_model": TIKTOK_TRANSCRIPTION_MODEL,
                            "stage": "openai_transcription",
                        },
                    },
                )
                raise Exception(NO_TIKTOK_TRANSCRIPT_ERROR)

            transcript = clean_transcript_text(_extract_transcription_text(response))

            logger.info(
                "tiktok_transcription_completed",
                extra={
                    "smu_context": {
                        "url_hostname": hostname,
                        "appears_tiktok_url": is_tiktok_url,
                        "is_shortlink": is_shortlink,
                        "downloaded_media_extension": media_file.suffix.lower(),
                        "downloaded_media_size_bytes": media_file.stat().st_size,
                        "transcription_model": TIKTOK_TRANSCRIPTION_MODEL,
                        "transcript_length": len(transcript),
                        "stage": "openai_transcription",
                    },
                },
            )

            if not transcript:
                raise Exception(NO_TIKTOK_TRANSCRIPT_ERROR)

            return transcript

    ydl_opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en"],
        "socket_timeout": 20,
    }

    try:
        with youtube_dl_cls(ydl_opts) as ydl:
            info = ydl.extract_info(extraction_url, download=False)
    except Exception as e:
        logger.error(
            "tiktok_extract_info_failed",
            extra={
                "smu_context": {
                    "extract_info_exception_class": e.__class__.__name__,
                    "exception_message": _safe_exception_message(e),
                    "appears_tiktok_url": is_tiktok_url,
                    "is_shortlink": is_shortlink,
                    "stage": "extract_info",
                    "url_hostname": hostname,
                },
            },
        )
        return transcribe_downloaded_media()

    logger.info(
        "tiktok_extract_info_completed",
        extra={
            "smu_context": {
                "extract_info_returned_info": info is not None,
                "stage": "extract_info",
            },
        },
    )
    info = info or {}

    title = info.get("title", "")
    description = info.get("description", "")
    cleaned_title = clean_transcript_text(title)
    cleaned_description = clean_transcript_text(description)
    requested_subtitles = info.get("requested_subtitles", {})

    automatic_captions = info.get("automatic_captions", {})
    subtitles = info.get("subtitles", {})
    has_caption_metadata = bool(requested_subtitles or subtitles or automatic_captions)

    logger.info(
        "tiktok_metadata_diagnostics",
        extra={
            "smu_context": {
                "title_present": bool(title),
                "description_present": bool(description),
                "caption_metadata_present": has_caption_metadata,
                "cleaned_title_length": len(cleaned_title),
                "cleaned_description_length": len(cleaned_description),
                "stage": "metadata_inspection",
            },
        },
    )

    selected_caption = select_caption(info)
    transcript = ""
    fallback_source = "none"
    caption_source = None
    caption_language = None
    caption_format = None
    parsed_caption_length = 0
    fallback_used = True

    if selected_caption:
        caption_source = selected_caption["source"]
        caption_language = selected_caption["language"]
        caption_format = selected_caption["format"]
        transcript = selected_caption["transcript"]
        parsed_caption_length = selected_caption["parsed_length"]
        fallback_used = parsed_caption_length == 0

        logger.info(
            "tiktok_caption_candidate_selected",
            extra={
                "smu_context": {
                    "caption_candidate_chosen_index": selected_caption["index"],
                    "caption_candidate_chosen_reason": (
                        "longest_usable_parsed_caption"
                    ),
                    "stage": "caption_selection",
                },
            },
        )

    logger.info(
        "tiktok_caption_selection_diagnostics",
        extra={
            "smu_context": {
                "caption_source_selected": caption_source,
                "caption_language": caption_language,
                "caption_format": caption_format,
                "parsed_caption_length": parsed_caption_length,
                "fallback_used": fallback_used,
                "stage": "caption_selection",
            },
        },
    )

    if fallback_used:
        if description:
            transcript = cleaned_description

            if cleaned_description:
                fallback_source = "description"

        if not transcript:
            transcript = cleaned_title

            if cleaned_title:
                fallback_source = "title"

    final_transcript_length = len(clean_transcript_text(transcript))
    logger.info(
        "tiktok_transcript_final_diagnostics",
        extra={
            "smu_context": {
                "final_transcript_length": final_transcript_length,
                "fallback_source": fallback_source,
                "stage": "transcript_result",
            },
        },
    )

    if not transcript:
        return transcribe_downloaded_media()

    return transcript


def generate_content_pack(
    source_text,
    brand_context="",
    *,
    openai_api_key=None,
    openai_client=None,
):
    if not openai_api_key:
        raise Exception("OPENAI_API_KEY is missing from your .env file")

    prompt = f"""
You are a social media content repurposing assistant.

/human

Brand Brief:
{brand_context}

Turn this source content into a full social media content pack.

Return in this exact format:

INSTAGRAM_CAPTION:
...

FACEBOOK_POST:
...

CAROUSEL_IDEA:
Slide 1:
Slide 2:
Slide 3:
Slide 4:
Slide 5:
Slide 6:

PINTEREST_PIN_TITLE:
...

PINTEREST_PIN_DESCRIPTION:
...

REDDIT_POST:
...

X_POST:
...

LINKEDIN_POST:
...

IMAGE_PROMPT:
...

HASHTAGS:
...

Source content:
{source_text}
"""

    response = openai_client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
    )

    return response.output_text


def extract_content_pack_section(text, section_name):
    sections = [
        "INSTAGRAM_CAPTION:",
        "FACEBOOK_POST:",
        "CAROUSEL_IDEA:",
        "PINTEREST_PIN_TITLE:",
        "PINTEREST_PIN_DESCRIPTION:",
        "X_POST:",
        "LINKEDIN_POST:",
        "IMAGE_PROMPT:",
        "HASHTAGS:",
    ]

    start_label = section_name + ":"
    start_index = text.find(start_label)

    if start_index == -1:
        return ""

    content_start = start_index + len(start_label)
    content_end = len(text)

    for label in sections:
        index = text.find(label, content_start)

        if index != -1 and index < content_end:
            content_end = index

    return text[content_start:content_end].strip()
