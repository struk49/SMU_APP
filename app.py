import os
import time
import json
import uuid
import re
import base64
import logging
from logging.handlers import RotatingFileHandler
from io import BytesIO
from datetime import datetime
from urllib.parse import urlparse

import pytz
import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, abort
from flask_login import (
    UserMixin,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler
from PIL import Image

import cloudinary
import cloudinary.uploader
from openai import OpenAI
from yt_dlp import YoutubeDL
from smu_core import create_app
from smu_core.extensions import db, login_manager
from smu_core.models.user import User
from smu_core.models.beta_application import BetaApplication
from smu_core.models.brand_brief import BrandBrief
from smu_core.models.connected_account import ConnectedAccount
from smu_core.models.contact_message import ContactMessage
from smu_core.models.feedback import Feedback
from smu_core.models.post import Post
from smu_core.models.post_revision import PostRevision

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = create_app()
DATABASE_URL = app.config.get("DATABASE_URL", "")
print("DATABASE:", app.config["SQLALCHEMY_DATABASE_URI"][:50])


def configure_logging():
    log_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger("smu")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        handler = RotatingFileHandler(
            os.path.join(log_dir, "smu.log"),
            maxBytes=1024 * 1024,
            backupCount=3,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)

    return logger


smu_logger = configure_logging()


def log_event(event_name, **fields):
    safe_fields = {
        key: value
        for key, value in fields.items()
        if key not in {"password", "token", "api_key", "webhook_url", "caption", "payload"}
    }
    safe_fields["event"] = event_name
    safe_fields["timestamp"] = datetime.utcnow().isoformat() + "Z"
    smu_logger.info(json.dumps(safe_fields, default=str, sort_keys=True))


app.extensions["smu_log_event"] = log_event


MAKE_WEBHOOK_SINGLE = os.getenv("MAKE_WEBHOOK_SINGLE", "").strip()
MAKE_WEBHOOK_CAROUSEL = os.getenv("MAKE_WEBHOOK_CAROUSEL", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

openai_client = OpenAI(api_key=OPENAI_API_KEY)

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
)


@app.template_filter("uk_time")
def uk_time_filter(value, format_string="%d/%m/%Y %H:%M"):
    if not value:
        return ""

    uk_datetime = convert_utc_to_uk(value)
    return uk_datetime.strftime(format_string)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


with app.app_context():
    db.create_all()

    inspector = db.inspect(db.engine)

    if "brand_brief" not in inspector.get_table_names():
        db.create_all()

    columns = [col["name"] for col in inspector.get_columns("post")]

    with db.engine.connect() as conn:
        if "group_id" not in columns:
            conn.execute(db.text("ALTER TABLE post ADD COLUMN group_id VARCHAR(100)"))

        if "post_type" not in columns:
            conn.execute(
                db.text(
                    "ALTER TABLE post ADD COLUMN post_type VARCHAR(50) DEFAULT 'single'"
                )
            )

        if "grade_result" not in columns:
            conn.execute(db.text("ALTER TABLE post ADD COLUMN grade_result TEXT"))

        if "grade_score" not in columns:
            conn.execute(db.text("ALTER TABLE post ADD COLUMN grade_score FLOAT"))

        if "graded_at" not in columns:
            conn.execute(db.text("ALTER TABLE post ADD COLUMN graded_at TIMESTAMP"))

        if "improved_caption" not in columns:
            conn.execute(
                db.text("ALTER TABLE post ADD COLUMN improved_caption TEXT")
            )

        if "improved_at" not in columns:
            conn.execute(
                db.text("ALTER TABLE post ADD COLUMN improved_at TIMESTAMP")
            )
        
        if "brand_score" not in columns:
            conn.execute(db.text("ALTER TABLE post ADD COLUMN brand_score FLOAT"))

        if "brand_feedback" not in columns:
            conn.execute(db.text("ALTER TABLE post ADD COLUMN brand_feedback TEXT"))

        conn.commit()

def get_file_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[1].lower() if "." in filename else ""

    if ext in {"png", "jpg", "jpeg", "gif", "webp"}:
        return "image"

    if ext in {"mp4", "mov", "avi", "webm"}:
        return "video"

    raise Exception(f"Unsupported file type: {ext}")


def parse_platforms(platforms_string):
    if not platforms_string:
        return []

    return [
        platform.strip() for platform in platforms_string.split(",") if platform.strip()
    ]


def is_instagram_selected(platforms):
    return "instagram" in {
        platform.strip().lower()
        for platform in platforms
        if platform and platform.strip()
    }


def normalize_image_to_jpeg(file_or_bytes):
    if isinstance(file_or_bytes, bytes):
        source = BytesIO(file_or_bytes)
    else:
        source = file_or_bytes

    if hasattr(source, "seek"):
        source.seek(0)

    with Image.open(source) as image:
        source_format = image.format or "unknown"

        if image.mode in {"RGBA", "LA"} or (
            image.mode == "P" and "transparency" in image.info
        ):
            rgba_image = image.convert("RGBA")
            background = Image.new("RGBA", rgba_image.size, (255, 255, 255, 255))
            background.alpha_composite(rgba_image)
            final_image = background.convert("RGB")
        else:
            final_image = image.convert("RGB")

        output = BytesIO()
        final_image.save(
            output,
            format="JPEG",
            quality=92,
            progressive=False,
            optimize=True,
        )

    return {
        "bytes": output.getvalue(),
        "source_format": source_format,
        "final_format": "JPEG",
        "final_mode": "RGB",
    }


def log_image_normalization_diagnostics(result, upload_url=None):
    print(
        "Image normalization diagnostics:",
        {
            "source_format": result.get("source_format"),
            "final_format": result.get("final_format"),
            "final_color_mode": result.get("final_mode"),
            "final_url_extension": get_url_path_extension(upload_url),
        },
    )


def upload_jpeg_to_cloudinary(file_or_bytes):
    normalized = normalize_image_to_jpeg(file_or_bytes)
    upload_buffer = BytesIO(normalized["bytes"])
    upload_buffer.name = "instagram-safe.jpg"

    upload_result = cloudinary.uploader.upload(
        upload_buffer,
        folder="social_posts",
        resource_type="image",
        format="jpg",
    )

    log_image_normalization_diagnostics(
        normalized,
        upload_url=upload_result.get("secure_url"),
    )

    return upload_result


def upload_to_cloudinary(file_or_url, force_jpeg=False):
    if force_jpeg:
        return upload_jpeg_to_cloudinary(file_or_url)

    return cloudinary.uploader.upload(
        file_or_url, folder="social_posts", resource_type="auto"
    )


def get_placeholder_image_url():
    return "https://res.cloudinary.com/demo/image/upload/w_1080,h_1080,c_fill,b_rgb:111111/l_text:Arial_60_bold:Generating%20Image,co_rgb:ffffff/sample.jpg"


UK_TIMEZONE = pytz.timezone("Europe/London")
UTC_TIMEZONE = pytz.UTC


def convert_uk_time_to_utc(datetime_string):
    """
    Convert a datetime-local value entered in UK time into naive UTC
    for storage in the existing database.
    """
    local_naive = datetime.strptime(
        datetime_string,
        "%Y-%m-%dT%H:%M",
    )

    local_aware = UK_TIMEZONE.localize(
        local_naive,
        is_dst=None,
    )

    utc_aware = local_aware.astimezone(UTC_TIMEZONE)

    return utc_aware.replace(tzinfo=None)


def convert_utc_to_uk(utc_datetime):
    """
    Convert a naive UTC database datetime into UK local time.
    """
    if not utc_datetime:
        return None

    utc_aware = UTC_TIMEZONE.localize(utc_datetime)

    return utc_aware.astimezone(UK_TIMEZONE)


def make_instagram_safe_url(url):
    return url.replace("/upload/", "/upload/c_fill,w_1080,h_1080,q_auto,f_jpg/")


def get_url_path_extension(url):
    path = urlparse(url or "").path
    _, extension = os.path.splitext(path)
    return extension.lower().lstrip(".")


def log_scheduled_post_diagnostics(post, input_local_time=None):
    print(
        "Scheduled post saved:",
        {
            "post_id": post.id,
            "post_type": post.post_type,
            "group_id_present": bool(post.group_id),
            "user_id": post.user_id,
            "status": post.status,
            "stored_utc_scheduled_time": post.scheduled_time,
            "input_local_time_present": bool(input_local_time),
        },
    )


def log_single_image_diagnostics(post, enabled_platforms):
    print(
        "Single image publish diagnostics:",
        {
            "instagram_selected": "instagram" in enabled_platforms,
            "file_url_configured": bool(post.file_url),
            "file_type": post.file_type,
            "url_path_extension": get_url_path_extension(post.file_url),
        },
    )


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


def rewrite_caption_with_ai(caption, rewrite_type):
    if not OPENAI_API_KEY:
        raise Exception("OPENAI_API_KEY is missing from your .env file")

    instructions = {
        "viral": "Rewrite this caption to be more engaging, hook-driven, and viral for Instagram/Facebook.",
        "shorten": "Rewrite this caption to be shorter, sharper, and easier to read.",
        "expand": "Expand this caption with more detail, better flow, and stronger value.",
        "cta": "Rewrite this caption with a stronger call-to-action at the end.",
        "hashtags": "Improve this caption and add relevant hashtags at the end.",
        "professional": "Rewrite this caption to sound more professional, polished, and trustworthy.",
    }

    instruction = instructions.get(
        rewrite_type, "Improve this social media caption while keeping the meaning."
    )

    prompt = f"""
You are a social media copywriter.

Task:
{instruction}

Rules:
- Keep it suitable for Instagram and Facebook.
- Keep the meaning of the original.
- Do not add fake facts.
- Keep it natural and human.
- Return only the rewritten caption.
- Do not include labels or explanations.

Original caption:
{caption}
"""

    response = openai_client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
    )

    return response.output_text.strip()


def evaluate_brand_match(post, brand_context=""):
    prompt = f"""
You are a senior brand strategist.

Evaluate how well this post matches the user's Brand Brief.

Brand Brief:
{brand_context}

Post Caption:
{post.caption or ""}

Platforms:
{post.platforms or ""}

Return ONLY valid JSON in this exact structure:

{{
  "overall_score": 8.5,
  "tone": true,
  "audience": true,
  "offer": false,
  "cta": true,
  "brand_voice": true,
  "recommendations": [
    "Mention the main offer earlier.",
    "Make the CTA more specific."
  ]
}}

Rules:
- overall_score must be a number from 0 to 10.
- tone, audience, offer, cta, and brand_voice must be true or false.
- recommendations must be short and specific.
- Do not include markdown.
- Do not include explanations outside JSON.
"""

    response = openai_client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
    )

    return response.output_text.strip()


def parse_brand_feedback(feedback):
    """
    Safely parses the JSON returned by evaluate_brand_match().

    Returns a dictionary with guaranteed keys, even if the AI
    returns invalid or incomplete JSON.
    """

    defaults = {
        "overall_score": 0.0,
        "tone": False,
        "audience": False,
        "offer": False,
        "cta": False,
        "brand_voice": False,
        "recommendations": [],
    }

    try:
        data = json.loads(feedback)

        # Ensure all expected keys exist
        for key, value in defaults.items():
            data.setdefault(key, value)

        # Convert score safely
        try:
            data["overall_score"] = float(data["overall_score"])
        except Exception:
            data["overall_score"] = 0.0

        # Ensure recommendations is always a list
        if not isinstance(data["recommendations"], list):
            data["recommendations"] = []

        return data

    except Exception as e:
        print("Brand Coach JSON parse error:", e)
        return defaults
    

def update_brand_coach(post, brand_context=""):
    try:
        feedback_json = evaluate_brand_match(post, brand_context)

        parsed = parse_brand_feedback(feedback_json)

        post.brand_score = parsed["overall_score"]
        post.brand_feedback = json.dumps(parsed)

        return parsed

    except Exception as e:
        print("Brand Coach update error:", e)
        return None


def generate_openai_image(prompt):
    if not OPENAI_API_KEY:
        raise Exception("OPENAI_API_KEY is missing from your .env file")

    result = openai_client.images.generate(
        model="gpt-image-1",
        prompt=prompt,
        size="1024x1024",
        quality="medium",
        output_format="jpeg",
    )

    image_base64 = result.data[0].b64_json
    image_bytes = base64.b64decode(image_base64)

    upload_result = upload_jpeg_to_cloudinary(image_bytes)

    return upload_result["secure_url"]


def generate_multiple_openai_images(prompt, count=1):
    image_urls = []

    for _ in range(count):
        image_url = generate_openai_image(prompt)
        image_urls.append(image_url)

    return image_urls


def send_payload_to_make(payload, webhook_url=None):
    if not webhook_url:
        if payload.get("post_type") == "carousel":
            webhook_url = MAKE_WEBHOOK_CAROUSEL
        else:
            webhook_url = MAKE_WEBHOOK_SINGLE

    if not webhook_url:
        raise Exception(
            f"No Make webhook configured for "
            f"{payload.get('post_type', 'unknown')} posts."
        )

    print("\n========== MAKE REQUEST ==========")
    print("Post type:", payload.get("post_type"))
    print("Webhook configured:", bool(webhook_url))
    print("Platforms:", payload.get("platforms"))
    print("Media count:", len(payload.get("media", [])))
    print("==================================")

    response = requests.post(
        webhook_url,
        json=payload,
        timeout=30,
    )

    print("Make status:", response.status_code)

    response.raise_for_status()

    return response

def build_single_payload(post):
    return {
        "post_type": "single",
        "post_id": post.id,
        "caption": post.caption,
        "prompt": post.prompt,
        "file_url": post.file_url,
        "file_type": post.file_type,
        "platforms": parse_platforms(post.platforms),
    }


def get_ordered_carousel_posts(group_id, user_id=None):
    query = Post.query.filter_by(group_id=group_id)

    if user_id is not None:
        query = query.filter_by(user_id=user_id)

    return query.order_by(
        Post.is_cover.desc(),
        Post.sort_order.asc(),
        Post.id.asc(),
    ).all()


def build_carousel_payload(group_id, user_id=None):
    posts = get_ordered_carousel_posts(group_id, user_id=user_id)

    if not posts:
        return None

    first_post = posts[0]

    return {
        "post_type": "carousel",
        "group_id": group_id,
        "caption": first_post.caption,
        "prompt": first_post.prompt,
        "platforms": parse_platforms(first_post.platforms),
        "media": [
            {
                "post_id": post.id,
                "file_url": make_instagram_safe_url(post.file_url),
                "file_type": post.file_type,
                "sort_order": post.sort_order,
                "is_cover": post.is_cover,
            }
            for post in posts
        ],
    }


app.extensions.setdefault("smu_calendar_helpers", {}).update({
    "parse_platforms": lambda *args, **kwargs: parse_platforms(*args, **kwargs),
    "convert_utc_to_uk": lambda *args, **kwargs: convert_utc_to_uk(
        *args,
        **kwargs,
    ),
    "get_ordered_carousel_posts": (
        lambda *args, **kwargs: get_ordered_carousel_posts(*args, **kwargs)
    ),
})


app.extensions.setdefault("smu_post_detail_helpers", {}).update({
    "get_ordered_carousel_posts": (
        lambda *args, **kwargs: get_ordered_carousel_posts(*args, **kwargs)
    ),
})


app.extensions.setdefault("smu_post_edit_helpers", {}).update({
    "get_ordered_carousel_posts": (
        lambda *args, **kwargs: get_ordered_carousel_posts(*args, **kwargs)
    ),
    "generate_openai_image": lambda *args, **kwargs: generate_openai_image(
        *args,
        **kwargs,
    ),
})


app.extensions.setdefault("smu_post_delete_duplicate_helpers", {}).update({
    "get_ordered_carousel_posts": (
        lambda *args, **kwargs: get_ordered_carousel_posts(*args, **kwargs)
    ),
})


app.extensions.setdefault("smu_post_create_helpers", {}).update({
    "convert_uk_time_to_utc": (
        lambda *args, **kwargs: convert_uk_time_to_utc(*args, **kwargs)
    ),
    "build_brand_context": lambda *args, **kwargs: build_brand_context(
        *args,
        **kwargs,
    ),
    "apply_image_style": lambda *args, **kwargs: apply_image_style(
        *args,
        **kwargs,
    ),
    "generate_multiple_openai_images": (
        lambda *args, **kwargs: generate_multiple_openai_images(*args, **kwargs)
    ),
    "get_file_type": lambda *args, **kwargs: get_file_type(*args, **kwargs),
    "upload_to_cloudinary": lambda *args, **kwargs: upload_to_cloudinary(
        *args,
        **kwargs,
    ),
    "is_instagram_selected": (
        lambda *args, **kwargs: is_instagram_selected(*args, **kwargs)
    ),
})


app.extensions.setdefault("smu_post_schedule_helpers", {}).update({
    "convert_uk_time_to_utc": (
        lambda *args, **kwargs: convert_uk_time_to_utc(*args, **kwargs)
    ),
    "get_ordered_carousel_posts": (
        lambda *args, **kwargs: get_ordered_carousel_posts(*args, **kwargs)
    ),
    "log_scheduled_post_diagnostics": (
        lambda *args, **kwargs: log_scheduled_post_diagnostics(*args, **kwargs)
    ),
})


app.extensions.setdefault("smu_manual_publish_helpers", {}).update({
    "publish_post_to_make": (
        lambda *args, **kwargs: publish_post_to_make(*args, **kwargs)
    ),
    "get_ordered_carousel_posts": (
        lambda *args, **kwargs: get_ordered_carousel_posts(*args, **kwargs)
    ),
    "log_event": lambda *args, **kwargs: log_event(*args, **kwargs),
})


app.extensions.setdefault("smu_caption_helpers", {}).update({
    "rewrite_caption_with_ai": (
        lambda *args, **kwargs: rewrite_caption_with_ai(*args, **kwargs)
    ),
    "get_ordered_carousel_posts": (
        lambda *args, **kwargs: get_ordered_carousel_posts(*args, **kwargs)
    ),
    "build_brand_context": lambda *args, **kwargs: build_brand_context(
        *args,
        **kwargs,
    ),
    "improve_post_with_ai": (
        lambda *args, **kwargs: improve_post_with_ai(*args, **kwargs)
    ),
    "update_brand_coach": (
        lambda *args, **kwargs: update_brand_coach(*args, **kwargs)
    ),
    "save_post_revision": (
        lambda *args, **kwargs: save_post_revision(*args, **kwargs)
    ),
})


app.extensions.setdefault("smu_ai_editor_helpers", {}).update({
    "save_post_revision": (
        lambda *args, **kwargs: save_post_revision(*args, **kwargs)
    ),
    "build_brand_context": lambda *args, **kwargs: build_brand_context(
        *args,
        **kwargs,
    ),
    "update_brand_coach": (
        lambda *args, **kwargs: update_brand_coach(*args, **kwargs)
    ),
})


def clean_transcript_text(text):
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_tiktok_transcript(tiktok_url):
    import html as html_parser

    hostname = urlparse(tiktok_url).hostname or ""
    print(
        "TikTok transcript diagnostics:",
        {
            "helper_reached": True,
            "url_hostname": hostname,
        },
    )

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

        print(
            "TikTok transcript diagnostics:",
            {
                "caption_source": source_name,
                "available_languages": available_languages,
                "caption_entries_per_language": entry_counts,
                "available_formats_per_language": available_formats,
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

        response = requests.get(entry["url"], timeout=10)

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
            print(
                "TikTok transcript diagnostics:",
                {
                    "caption_parse_exception_class": exception_class,
                    "caption_format": caption_format,
                },
            )

        print(
            "TikTok transcript diagnostics:",
            {
                "caption_candidate_source": source_name,
                "caption_candidate_language": language,
                "caption_candidate_index": index,
                "caption_candidate_format": caption_format,
                "downloaded_caption_byte_length": byte_length,
                "parsed_caption_fragment_count": fragment_count,
                "parsed_caption_length": parsed_length,
                "caption_candidate_exception_class": exception_class,
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

    ydl_opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en"],
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(tiktok_url, download=False)
    except Exception as e:
        print(
            "TikTok transcript diagnostics:",
            {
                "extract_info_exception_class": e.__class__.__name__,
            },
        )
        raise

    print(
        "TikTok transcript diagnostics:",
        {
            "extract_info_returned_info": info is not None,
        },
    )

    title = info.get("title", "")
    description = info.get("description", "")
    cleaned_title = clean_transcript_text(title)
    cleaned_description = clean_transcript_text(description)
    requested_subtitles = info.get("requested_subtitles", {})

    automatic_captions = info.get("automatic_captions", {})
    subtitles = info.get("subtitles", {})
    has_caption_metadata = bool(requested_subtitles or subtitles or automatic_captions)

    print(
        "TikTok transcript diagnostics:",
        {
            "title_present": bool(title),
            "description_present": bool(description),
            "caption_metadata_present": has_caption_metadata,
            "cleaned_title_length": len(cleaned_title),
            "cleaned_description_length": len(cleaned_description),
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

        print(
            "TikTok transcript diagnostics:",
            {
                "caption_candidate_chosen_index": selected_caption["index"],
                "caption_candidate_chosen_reason": "longest_usable_parsed_caption",
            },
        )

    print(
        "TikTok transcript diagnostics:",
        {
            "caption_source_selected": caption_source,
            "caption_language": caption_language,
            "caption_format": caption_format,
            "parsed_caption_length": parsed_caption_length,
            "fallback_used": fallback_used,
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
    print(
        "TikTok transcript diagnostics:",
        {
            "final_transcript_length": final_transcript_length,
            "fallback_source": fallback_source,
        },
    )

    if not transcript:
        raise Exception("No transcript or usable text found for this TikTok.")

    return transcript


def repurpose_tiktok_content(
    transcript,
    brand_context=""
):
    if not OPENAI_API_KEY:
        raise Exception("OPENAI_API_KEY is missing from your .env file")

    prompt = f"""
You are a social media content repurposing assistant.

Turn this TikTok transcript into content for Instagram and Facebook.

Brand Brief:
{brand_context}

Return the result in this exact format:

INSTAGRAM_CAPTION:
...

FACEBOOK_CAPTION:
...

CAROUSEL_IDEA:
Slide 1:
Slide 2:
Slide 3:
Slide 4:
Slide 5:
Slide 6:

IMAGE_PROMPT:
...

HASHTAGS:
...

Transcript:
{transcript}
"""

    response = openai_client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
    )

    return response.output_text


def check_scheduled_posts():
    with app.app_context():
        try:
            now_utc = datetime.utcnow()

            print(
                "Scheduler check:",
                now_utc.strftime("%Y-%m-%d %H:%M:%S"),
                "UTC",
            )

            scheduled_query = Post.query.filter(
                Post.scheduled_time.isnot(None),
                Post.status == "scheduled",
            )
            scheduled_count = scheduled_query.count()
            earliest_scheduled = (
                scheduled_query.order_by(Post.scheduled_time.asc())
                .with_entities(Post.scheduled_time)
                .first()
            )

            due_posts = (
                Post.query.filter(
                    Post.scheduled_time.isnot(None),
                    Post.status == "scheduled",
                    Post.scheduled_time <= now_utc,
                )
                .order_by(
                    Post.scheduled_time.asc(),
                    Post.sort_order.asc(),
                    Post.id.asc(),
                )
                .all()
            )

            print(
                "Scheduler diagnostics:",
                {
                    "current_utc_time": now_utc,
                    "scheduled_row_count": scheduled_count,
                    "earliest_scheduled_time": (
                        earliest_scheduled[0] if earliest_scheduled else None
                    ),
                    "due_row_count": len(due_posts),
                },
            )

            processed_groups = set()

            for post in due_posts:
                try:
                    print(
                        f"Processing scheduled post {post.id}: "
                        f"scheduled={post.scheduled_time}, "
                        f"status={post.status}, "
                        f"user_id={post.user_id}"
                    )

                    if post.group_id:
                        if post.group_id in processed_groups:
                            continue

                    publish_post_to_make(post, post.user_id)

                    if post.group_id:
                        processed_groups.add(post.group_id)

                        print(
                            f"✅ Sent scheduled carousel "
                            f"{post.group_id}"
                        )
                        log_event(
                            "publishing_success",
                            post_id=post.id,
                            post_type="carousel",
                            user_id=post.user_id,
                            source="scheduler",
                        )

                    else:
                        print(
                            f"✅ Sent scheduled post {post.id}"
                        )
                        log_event(
                            "publishing_success",
                            post_id=post.id,
                            post_type="single",
                            user_id=post.user_id,
                            source="scheduler",
                        )

                    db.session.commit()

                except Exception as post_error:
                    db.session.rollback()
                    log_event(
                        "publishing_failure",
                        post_id=post.id,
                        post_type=post.post_type,
                        user_id=post.user_id,
                        source="scheduler",
                        error_type=type(post_error).__name__,
                    )

                    print(
                        f"❌ Scheduled post {post.id} failed:",
                        repr(post_error),
                    )

                    try:
                        post.status = "schedule_failed"
                        db.session.commit()
                    except Exception as status_error:
                        db.session.rollback()
                        print(
                            f"Failed to mark scheduled post {post.id} "
                            "as failed:",
                            repr(status_error),
                        )

        except Exception as worker_error:
            db.session.rollback()

            print(
                "❌ Scheduled-post worker error:",
                repr(worker_error),
            )

                        

def generate_pending_carousel_images():
    with app.app_context():
        post = None

        try:
            post = (
                Post.query.filter_by(status="generating", file_type="image")
                .order_by(Post.created_at.asc(), Post.sort_order.asc())
                .first()
            )

            if not post:
                return

            print(f"Generating image for post {post.id}")

            image_url = generate_openai_image(post.prompt)

            post.file_url = image_url
            post.status = "draft"

            db.session.commit()

            print(f"✅ Generated image for post {post.id}")

        except Exception as e:
            db.session.rollback()

            print("Background image generation error:", e)

            if post:
                try:
                    post.status = "generation_failed"
                    db.session.commit()
                    print(f"Marked post {post.id} as generation_failed")
                except Exception as inner_error:
                    db.session.rollback()
                    print("Failed to mark post as failed:", inner_error)


def generate_content_pack(source_text, brand_context=""):
    if not OPENAI_API_KEY:
        raise Exception("OPENAI_API_KEY is missing from your .env file")

    prompt = f"""
You are a social media content repurposing assistant.

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


def build_brand_context(user_id):
    brief = BrandBrief.query.filter_by(user_id=user_id).first()

    if not brief:
        return ""

    return f"""
BRAND BRIEF

Business Name:
{brief.business_name}

Niche:
{brief.niche}

Target Audience:
{brief.target_audience}

Offer:
{brief.offer}

Tone Of Voice:
{brief.tone_of_voice}

Content Goals:
{brief.content_goals}

Platforms:
{brief.main_platforms}

CTA Style:
{brief.cta_style}

Words To Avoid:
{brief.words_to_avoid}

IMPORTANT:
All content must match this brand brief.
Do not create generic content.
Use the tone, audience and offer above.
"""


app.extensions.setdefault("smu_content_pack_helpers", {}).update({
    "extract_tiktok_transcript": lambda *args, **kwargs: extract_tiktok_transcript(
        *args,
        **kwargs,
    ),
    "build_brand_context": lambda *args, **kwargs: build_brand_context(
        *args,
        **kwargs,
    ),
    "generate_content_pack": lambda *args, **kwargs: generate_content_pack(
        *args,
        **kwargs,
    ),
    "extract_content_pack_section": (
        lambda *args, **kwargs: extract_content_pack_section(*args, **kwargs)
    ),
    "apply_image_style": lambda *args, **kwargs: apply_image_style(
        *args,
        **kwargs,
    ),
    "get_placeholder_image_url": (
        lambda *args, **kwargs: get_placeholder_image_url(*args, **kwargs)
    ),
})


app.extensions.setdefault("smu_tiktok_helpers", {}).update({
    "extract_tiktok_transcript": lambda *args, **kwargs: extract_tiktok_transcript(
        *args,
        **kwargs,
    ),
    "build_brand_context": lambda *args, **kwargs: build_brand_context(
        *args,
        **kwargs,
    ),
    "repurpose_tiktok_content": lambda *args, **kwargs: repurpose_tiktok_content(
        *args,
        **kwargs,
    ),
    "apply_image_style": lambda *args, **kwargs: apply_image_style(
        *args,
        **kwargs,
    ),
    "generate_openai_image": lambda *args, **kwargs: generate_openai_image(
        *args,
        **kwargs,
    ),
    "get_placeholder_image_url": (
        lambda *args, **kwargs: get_placeholder_image_url(*args, **kwargs)
    ),
})


def rewrite_caption_with_action(post, brand_context="", action="improve"):
    action_instructions = {
        "hook": "Improve the opening hook. Make the first line more attention-grabbing.",
        "cta": "Improve the call to action. Make the next step clearer and more persuasive.",
        "shorten": "Shorten the caption while keeping the main message.",
        "professional": "Rewrite the caption in a more professional tone.",
        "friendly": "Rewrite the caption in a warmer, friendlier tone.",
        "alternatives": "Create 3 alternative versions of this caption."
    }

    instruction = action_instructions.get(action, action_instructions["hook"])

    prompt = f"""
You are an expert social media copywriter.

Brand Brief:
{brand_context}

Current Caption:
{post.caption or ""}

Platform:
{post.platforms or ""}

Task:
{instruction}

Rules:
- Keep the message aligned with the Brand Brief.
- Keep the content suitable for the selected platform.
- Do not explain your changes.
- Return only the rewritten caption.
"""

    response = openai_client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
    )

    return response.output_text.strip()


def grade_post_with_ai(post, brand_context=""):
    prompt = f"""
You are an expert social media strategist and content reviewer.

Your job is to grade this social media post for effectiveness.

Brand Brief:
{brand_context}

Post Caption:
{post.caption or ""}

Platform:
{post.platforms or ""}

Post Type:
{post.post_type or "single"}

Please score the post out of 10 for each category:

1. Hook
2. Clarity
3. Engagement
4. Call To Action
5. Platform Fit
6. Brand Fit

Then provide:
- OVERALL_SCORE: a single score out of 10
- STRENGTHS: short bullet points
- IMPROVEMENTS: short bullet points with specific advice

Return the result in this exact format:

HOOK_SCORE: X/10
CLARITY_SCORE: X/10
ENGAGEMENT_SCORE: X/10
CTA_SCORE: X/10
PLATFORM_FIT_SCORE: X/10
BRAND_FIT_SCORE: X/10
OVERALL_SCORE: X/10

STRENGTHS:
- ...
- ...

IMPROVEMENTS:
- ...
- ...
"""

    response = openai_client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
    )

    return response.output_text.strip()


import re


def extract_overall_score(grade_result):
    """
    Extracts the OVERALL_SCORE from the AI grading response.
    """

    if not grade_result:
        return None

    match = re.search(
        r"OVERALL_SCORE:\s*([0-9]+(?:\.[0-9]+)?)\/10",
        grade_result,
    )

    if not match:
        return None

    try:
        return float(match.group(1))
    except ValueError:
        return None



def save_post_revision(post, source="manual"):
    latest_revision = PostRevision.query.filter_by(
        post_id=post.id,
        user_id=post.user_id
    ).order_by(PostRevision.version_number.desc()).first()

    next_version = 1

    if latest_revision:
        next_version = latest_revision.version_number + 1

    revision = PostRevision(
        post_id=post.id,
        user_id=post.user_id,
        version_number=next_version,
        caption=post.caption or "",
        score=post.grade_score,
        source=source
    )

    db.session.add(revision)
    return revision


def build_connected_platform_cards(user_id):
    accounts = ConnectedAccount.query.filter_by(user_id=user_id).first()

    return [
        {
            "name": "Instagram",
            "connected": bool(accounts and accounts.instagram_connected),
        },
        {
            "name": "Facebook",
            "connected": bool(accounts and accounts.facebook_connected),
        },
        {
            "name": "Pinterest",
            "connected": bool(accounts and accounts.pinterest_connected),
        },
        {
            "name": "LinkedIn",
            "connected": bool(accounts and accounts.linkedin_connected),
        },
    ]


def build_onboarding_progress(user_id):
    brief = BrandBrief.query.filter_by(user_id=user_id).first()
    has_brand_brief = bool(
        brief
        and (
            brief.business_name
            or brief.niche
            or brief.target_audience
            or brief.offer
        )
    )
    has_first_post = Post.query.filter_by(user_id=user_id).first() is not None
    has_scheduled_post = (
        Post.query.filter(
            Post.user_id == user_id,
            Post.scheduled_time.isnot(None),
        ).first()
        is not None
    )
    has_published_post = (
        Post.query.filter_by(user_id=user_id, status="sent_to_make").first()
        is not None
    )

    items = [
        {
            "label": "Brand Brief",
            "complete": has_brand_brief,
            "url": url_for("brand_brief"),
        },
        {
            "label": "Content Pack",
            "complete": bool(session.get("content_pack_started")),
            "url": url_for("content_pack"),
        },
        {
            "label": "First Post",
            "complete": has_first_post,
            "url": url_for("create_post"),
        },
        {
            "label": "Scheduled Post",
            "complete": has_scheduled_post,
            "url": url_for("calendar_view"),
        },
        {
            "label": "Calendar Viewed",
            "complete": bool(session.get("calendar_viewed")),
            "url": url_for("calendar_view"),
        },
        {
            "label": "First Published Post",
            "complete": has_published_post,
            "url": url_for("index", status="sent_to_make"),
        },
    ]
    completed_count = sum(1 for item in items if item["complete"])

    return {
        "items": items,
        "completed_count": completed_count,
        "total_count": len(items),
        "percentage": int(round((completed_count / len(items)) * 100)),
        "complete": completed_count == len(items),
    }


app.extensions.setdefault("smu_dashboard_helpers", {}).update({
    "build_onboarding_progress": (
        lambda *args, **kwargs: build_onboarding_progress(*args, **kwargs)
    ),
    "build_connected_platform_cards": (
        lambda *args, **kwargs: build_connected_platform_cards(*args, **kwargs)
    ),
})


def is_valid_email(email):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email or ""))


def field_too_long(value, max_length):
    return len(value or "") > max_length


def is_current_user_admin():
    admin_emails = app.config.get("SMU_ADMIN_EMAILS", set())
    return (
        current_user.is_authenticated
        and current_user.email.lower() in admin_emails
    )


def create_post():
    default_scheduled_time = ""

    if request.method == "GET":
        scheduled_date = request.args.get("scheduled_date", "").strip()

        if scheduled_date:
            try:
                parsed_date = datetime.strptime(scheduled_date, "%Y-%m-%d")
                default_scheduled_time = parsed_date.strftime("%Y-%m-%dT09:00")
            except ValueError:
                default_scheduled_time = ""

    if request.method == "POST":
        files = request.files.getlist("media")

        prompt = request.form.get("prompt", "").strip()
        caption = request.form.get("caption", "").strip()
        scheduled_time_str = request.form.get("scheduled_time")
        platforms = request.form.getlist("platforms")
        make_carousel = request.form.get("make_carousel") == "on"
        carousel_order_raw = request.form.get("carousel_order", "")
        cover_index_raw = request.form.get("cover_index", "0")
        image_style = request.form.get("image_style", "").strip()

        if not platforms:
            platforms = ["instagram", "facebook"]

        platforms_string = ",".join(platforms)

        scheduled_time = None
        if scheduled_time_str:
            scheduled_time = convert_uk_time_to_utc(scheduled_time_str)

        original_files = [
            (index, file)
            for index, file in enumerate(files)
            if file.filename != ""
        ]

        ordered_items = original_files

        if carousel_order_raw:
            try:
                order = json.loads(carousel_order_raw)
                file_map = {index: file for index, file in original_files}

                ordered_items = [
                    (index, file_map[index])
                    for index in order
                    if isinstance(index, int) and index in file_map
                ]

            except Exception as e:
                print("Carousel order error:", e)

        try:
            cover_index = int(cover_index_raw)
        except ValueError:
            cover_index = 0

        if make_carousel and ordered_items:
            cover_item = None
            remaining_items = []

            for index, file in ordered_items:
                if index == cover_index:
                    cover_item = (index, file)
                else:
                    remaining_items.append((index, file))

            if cover_item:
                ordered_items = [cover_item] + remaining_items

        uploaded_files = [file for _, file in ordered_items]
        has_files = len(uploaded_files) > 0

        if not prompt and not has_files:
            flash("Upload a file or enter a prompt.", "danger")
            return redirect(url_for("create_post"))

        try:
            if prompt and not has_files:
                image_count = 3 if make_carousel else 1

                brand_context = build_brand_context(current_user.id)

                branded_prompt = f"""
{brand_context}

Create a branded social media image.

User Request:
{prompt}
"""

                styled_prompt = apply_image_style(
                    branded_prompt,
                    image_style
                )

                image_urls = generate_multiple_openai_images(
                    styled_prompt,
                    image_count
                )

                group_id = str(uuid.uuid4()) if make_carousel else None
                created_posts = []

                for index, image_url in enumerate(image_urls):
                    post = Post(
                        file_url=image_url,
                        file_type="image",
                        prompt=styled_prompt,
                        caption=caption,
                        platforms=platforms_string,
                        post_type="carousel" if make_carousel else "single",
                        status="scheduled" if scheduled_time else "draft",
                        scheduled_time=scheduled_time,
                        group_id=group_id,
                        sort_order=index,
                        is_cover=(index == 0),
                        user_id=current_user.id,
                    )

                    db.session.add(post)
                    created_posts.append(post)

                db.session.commit()

                if make_carousel:
                    flash("AI carousel created successfully.", "success")
                    return redirect(url_for("index"))

                flash("AI image created successfully.", "success")
                return redirect(url_for("view_post", post_id=created_posts[0].id))

            if has_files:
                if make_carousel and len(uploaded_files) > 10:
                    flash(
                        "Instagram carousel posts can only contain up to 10 images.",
                        "danger",
                    )
                    return redirect(url_for("create_post"))

                is_carousel = make_carousel and len(uploaded_files) > 1
                group_id = str(uuid.uuid4()) if is_carousel else None
                created_posts = []

                for index, file in enumerate(uploaded_files):
                    file_type = get_file_type(file.filename)

                    if make_carousel and file_type != "image":
                        raise Exception("Carousel posts currently support images only.")

                    upload_result = upload_to_cloudinary(
                        file,
                        force_jpeg=(
                            file_type == "image"
                            and is_instagram_selected(platforms)
                        ),
                    )

                    post = Post(
                        file_url=upload_result["secure_url"],
                        file_type=file_type,
                        prompt=prompt,
                        caption=caption,
                        platforms=platforms_string,
                        post_type="carousel" if is_carousel else "single",
                        status="scheduled" if scheduled_time else "draft",
                        scheduled_time=scheduled_time,
                        group_id=group_id,
                        sort_order=index,
                        is_cover=(is_carousel and index == 0),
                        user_id=current_user.id,
                    )

                    db.session.add(post)
                    created_posts.append(post)

                db.session.commit()

                if is_carousel:
                    flash("Carousel created successfully.", "success")
                    return redirect(url_for("index"))

                flash("Post created successfully.", "success")
                return redirect(url_for("view_post", post_id=created_posts[0].id))

        except Exception as e:
            print("Create post error:", e)
            flash(f"Failed: {e}", "danger")
            return redirect(url_for("create_post"))

    return render_template(
        "create_post.html",
        default_scheduled_time=default_scheduled_time,
    )

def get_user_connected_accounts(user_id=None):
    if user_id is None:
        if not current_user.is_authenticated:
            return None

        user_id = current_user.id

    return ConnectedAccount.query.filter_by(
        user_id=user_id
    ).first()


def get_enabled_platforms_for_user(
    selected_platforms,
    user_id=None,
):
    accounts = get_user_connected_accounts(user_id)

    if not accounts:
        return []

    platform_map = {
        "instagram": accounts.instagram_connected,
        "facebook": accounts.facebook_connected,
        "linkedin": accounts.linkedin_connected,
        "pinterest": accounts.pinterest_connected,
        "reddit": accounts.reddit_connected,
        "x": accounts.x_connected,
    }

    enabled_platforms = []

    for platform in selected_platforms:
        clean_platform = platform.strip().lower()

        if platform_map.get(clean_platform, False):
            enabled_platforms.append(clean_platform)

    return enabled_platforms


def get_user_make_webhook(post_type, user_id=None):
    accounts = get_user_connected_accounts(user_id)

    if post_type == "carousel":
        if accounts and accounts.make_webhook_carousel:
            return accounts.make_webhook_carousel

        return MAKE_WEBHOOK_CAROUSEL or None

    if accounts and accounts.make_webhook_single:
        return accounts.make_webhook_single

    return MAKE_WEBHOOK_SINGLE or None


def publish_post_to_make(post, user_id):
    if user_id is None:
        raise ValueError("user_id is required for publishing")

    selected_platforms = [
        platform.strip().lower()
        for platform in (post.platforms or "").split(",")
        if platform.strip()
    ]

    enabled_platforms = get_enabled_platforms_for_user(
        selected_platforms,
        user_id=user_id,
    )

    if not enabled_platforms:
        raise Exception(
            "No connected platforms are enabled for this post. "
            "Check Connected Accounts."
        )

    if post.group_id:
        payload = build_carousel_payload(
            post.group_id,
            user_id=user_id,
        )

        if not payload:
            raise Exception("Carousel payload could not be built.")

        webhook_url = get_user_make_webhook(
            "carousel",
            user_id=user_id,
        )

        if not webhook_url:
            raise Exception(
                "No carousel webhook is configured. "
                "Add it in Connected Accounts."
            )

        payload["platforms"] = enabled_platforms
        response = send_payload_to_make(payload, webhook_url)

        group_posts = get_ordered_carousel_posts(
            post.group_id,
            user_id=user_id,
        )

        for group_post in group_posts:
            group_post.status = "sent_to_make"
            group_post.sent_at = datetime.utcnow()

        return response

    payload = build_single_payload(post)
    payload["platforms"] = enabled_platforms
    log_single_image_diagnostics(post, enabled_platforms)

    if "instagram" in enabled_platforms and not post.file_url:
        raise Exception("Instagram single-image posts require an image URL.")

    webhook_url = get_user_make_webhook(
        "single",
        user_id=user_id,
    )

    if not webhook_url:
        raise Exception(
            "No single-post webhook is configured. "
            "Add it in Connected Accounts."
        )

    response = send_payload_to_make(payload, webhook_url)

    post.status = "sent_to_make"
    post.sent_at = datetime.utcnow()

    return response



@app.template_filter("from_json")
def from_json_filter(value):
    try:
        return json.loads(value)
    except Exception:
        return {}


@app.route("/post/<int:post_id>/studio/action/<action>", methods=["POST"])
@login_required
def studio_action(post_id, action):
    post = Post.query.filter_by(
        id=post_id,
        user_id=current_user.id
    ).first_or_404()

    allowed_actions = [
        "hook",
        "cta",
        "shorten",
        "professional",
        "friendly",
        "alternatives",
    ]

    if action not in allowed_actions:
        flash("Invalid studio action.", "danger")
        return redirect(url_for("post_studio", post_id=post.id))

    try:
        final_caption = request.form.get("final_caption", "").strip()

        if final_caption:
            post.caption = final_caption

        brand_context = build_brand_context(current_user.id)

        rewritten_caption = rewrite_caption_with_action(
            post,
            brand_context,
            action
        )

        post.improved_caption = rewritten_caption
        post.improved_at = datetime.utcnow()


        brand_context = build_brand_context(current_user.id)

        update_brand_coach(post, brand_context)

        db.session.commit()

        flash("AI Studio action completed.", "success")

    except Exception as e:
        print("Studio action error:", e)
        flash(f"Failed to run studio action: {e}", "danger")

    return redirect(url_for("post_studio", post_id=post.id))


def improve_post_with_ai(post, brand_context=""):
    prompt = f"""
You are an expert social media copywriter.

Improve this post using the brand brief and grading feedback.

Brand Brief:
{brand_context}

Platform:
{post.platforms or ""}

Current Caption:
{post.caption or ""}

Post Grader Feedback:
{post.grade_result or "No grading feedback available."}

Rules:
- Keep the same core meaning.
- Strengthen the hook.
- Improve clarity and engagement.
- Improve the call to action.
- Match the brand brief.
- Keep it suitable for the selected platform.
- Do not explain your changes.
- Return only the improved caption.
"""

    response = openai_client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
    )

    return response.output_text.strip()



@app.route("/post/<int:post_id>/studio", methods=["GET", "POST"])
@login_required
def post_studio(post_id):
    post = Post.query.filter_by(
        id=post_id,
        user_id=current_user.id
    ).first_or_404()

    brief = BrandBrief.query.filter_by(
        user_id=current_user.id
    ).first()

    revisions = PostRevision.query.filter_by(
        post_id=post.id,
        user_id=current_user.id
    ).order_by(
        PostRevision.version_number.desc()
    ).all()

    if request.method == "POST":
        final_caption = request.form.get("final_caption", "").strip()

        if not final_caption:
            flash("Final caption cannot be empty.", "danger")
            return redirect(url_for("post_studio", post_id=post.id))

        save_post_revision(post, source="before_studio_save")

        post.caption = final_caption
        post.improved_caption = None
        post.improved_at = None


        brand_context = build_brand_context(current_user.id)
        update_brand_coach(post, brand_context)

        db.session.commit()

        flash("Studio caption saved successfully.", "success")
        return redirect(url_for("post_studio", post_id=post.id))

    return render_template(
        "post_studio.html",
        post=post,
        brief=brief,
        revisions=revisions
    )


@app.route("/post/<int:post_id>/studio/regrade", methods=["POST"])
@login_required
def studio_regrade(post_id):
    post = Post.query.filter_by(
        id=post_id,
        user_id=current_user.id
    ).first_or_404()

    try:
        final_caption = request.form.get("final_caption", "").strip()

        if final_caption:
            post.caption = final_caption

        brand_context = build_brand_context(current_user.id)

        grade_result = grade_post_with_ai(post, brand_context)
        overall_score = extract_overall_score(grade_result)

        post.grade_result = grade_result
        post.grade_score = overall_score
        post.graded_at = datetime.utcnow()

        update_brand_coach(post, brand_context)

        db.session.commit()

        flash("Studio caption regraded successfully.", "success")

    except Exception as e:
        db.session.rollback()
        print("Studio regrade error:", e)
        flash(f"Failed to regrade caption: {e}", "danger")

    return redirect(url_for("post_studio", post_id=post.id))


@app.route("/post/<int:post_id>/revision/<int:revision_id>/restore", methods=["POST"])
@login_required
def restore_revision(post_id, revision_id):
    post = Post.query.filter_by(
        id=post_id,
        user_id=current_user.id
    ).first_or_404()

    revision = PostRevision.query.filter_by(
        id=revision_id,
        post_id=post.id,
        user_id=current_user.id
    ).first_or_404()

    save_post_revision(post, source="before_revision_restore")

    post.caption = revision.caption
    post.improved_caption = None
    post.improved_at = None

    brand_context = build_brand_context(current_user.id)
    update_brand_coach(post, brand_context)

    db.session.commit()

    flash(f"Version {revision.version_number} restored.", "success")
    return redirect(url_for("post_studio", post_id=post.id))


@app.errorhandler(404)
def not_found_error(error):
    return render_template("404.html"), 404


@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template("500.html"), 500


log_event(
    "application_startup",
    database_configured=bool(DATABASE_URL),
)

print("Starting background scheduler...")
log_event("scheduler_startup", status="starting")

scheduler = BackgroundScheduler(
    timezone="UTC"
)

scheduler.add_job(
    generate_pending_carousel_images,
    trigger="interval",
    seconds=20,
    id="generate_pending_images",
    max_instances=1,
    replace_existing=True,
)

scheduler.add_job(
    check_scheduled_posts,
    trigger="interval",
    seconds=30,
    id="check_scheduled_posts",
    max_instances=1,
    replace_existing=True,
)

scheduler.start()

print("Background scheduler started.")
print("Registered jobs:", scheduler.get_jobs())
log_event(
    "scheduler_startup",
    status="started",
    job_count=len(scheduler.get_jobs()),
)


if __name__ == "__main__":
    app.run(
        debug=True,
        use_reloader=False,
    )
