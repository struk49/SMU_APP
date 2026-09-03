import os
import time
import json
import uuid
import re
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

import pytz
import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, abort, has_app_context
from flask_login import (
    UserMixin,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler

import cloudinary
import cloudinary.uploader
from openai import OpenAI
from yt_dlp import YoutubeDL
from smu_core import create_app
from smu_core.extensions import csrf, db, login_manager
from smu_core.models.user import User
from smu_core.models.beta_application import BetaApplication
from smu_core.models.brand_brief import BrandBrief
from smu_core.models.connected_account import ConnectedAccount
from smu_core.models.contact_message import ContactMessage
from smu_core.models.feedback import Feedback
from smu_core.models.post import Post
from smu_core.models.post_revision import PostRevision
from smu_core.models.scheduler_lease import SchedulerLease
from smu_core.models.user_usage import UserUsage
from smu_core.services import captions as captions_service
from smu_core.services import carousel_generation as carousel_generation_service
from smu_core.services import content as content_service
from smu_core.services import images as images_service
from smu_core.services import linkedin_publishing as linkedin_publishing_service
from smu_core.services import media as media_service
from smu_core.services import publishing as publishing_service
from smu_core.services import scheduler as scheduler_service
from smu_core.services.scheduler_lease import SchedulerLeaseCoordinator
from smu_core.services import tiktok as tiktok_service
from smu_core.services import time_utils
from smu_core.services import usage as usage_service

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
    safe_fields["timestamp"] = time_utils.utc_now_iso_z()
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
    return db.session.get(User, int(user_id))


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

        post_column_sql = {
            "zernio_post_id": "ALTER TABLE post ADD COLUMN zernio_post_id VARCHAR(255)",
            "zernio_status": "ALTER TABLE post ADD COLUMN zernio_status VARCHAR(50)",
            "zernio_platforms": "ALTER TABLE post ADD COLUMN zernio_platforms VARCHAR(200)",
            "zernio_published_url": "ALTER TABLE post ADD COLUMN zernio_published_url VARCHAR(500)",
            "zernio_error": "ALTER TABLE post ADD COLUMN zernio_error TEXT",
        }

        for column_name, alter_sql in post_column_sql.items():
            if column_name not in columns:
                conn.execute(db.text(alter_sql))

        account_columns = [
            col["name"] for col in inspector.get_columns("connected_account")
        ]
        connected_account_column_sql = {
            "linkedin_access_token": "ALTER TABLE connected_account ADD COLUMN linkedin_access_token VARCHAR(1000)",
            "linkedin_access_token_expires_at": "ALTER TABLE connected_account ADD COLUMN linkedin_access_token_expires_at TIMESTAMP",
            "linkedin_scopes": "ALTER TABLE connected_account ADD COLUMN linkedin_scopes VARCHAR(500)",
            "linkedin_member_id": "ALTER TABLE connected_account ADD COLUMN linkedin_member_id VARCHAR(255)",
            "linkedin_member_urn": "ALTER TABLE connected_account ADD COLUMN linkedin_member_urn VARCHAR(255)",
            "linkedin_display_name": "ALTER TABLE connected_account ADD COLUMN linkedin_display_name VARCHAR(255)",
            "linkedin_refresh_token": "ALTER TABLE connected_account ADD COLUMN linkedin_refresh_token VARCHAR(1000)",
            "linkedin_refresh_token_expires_at": "ALTER TABLE connected_account ADD COLUMN linkedin_refresh_token_expires_at TIMESTAMP",
            "zernio_profile_id": "ALTER TABLE connected_account ADD COLUMN zernio_profile_id VARCHAR(255)",
            "zernio_instagram_account_id": "ALTER TABLE connected_account ADD COLUMN zernio_instagram_account_id VARCHAR(255)",
            "zernio_facebook_account_id": "ALTER TABLE connected_account ADD COLUMN zernio_facebook_account_id VARCHAR(255)",
        }

        for column_name, alter_sql in connected_account_column_sql.items():
            if column_name not in account_columns:
                conn.execute(db.text(alter_sql))

        user_columns = [
            col["name"] for col in inspector.get_columns("user")
        ]
        user_column_sql = {
            "stripe_customer_id": 'ALTER TABLE "user" ADD COLUMN stripe_customer_id VARCHAR(255)',
            "stripe_subscription_id": 'ALTER TABLE "user" ADD COLUMN stripe_subscription_id VARCHAR(255)',
            "subscription_status": 'ALTER TABLE "user" ADD COLUMN subscription_status VARCHAR(50)',
            "subscription_current_period_end": 'ALTER TABLE "user" ADD COLUMN subscription_current_period_end TIMESTAMP',
            "subscription_cancel_at_period_end": 'ALTER TABLE "user" ADD COLUMN subscription_cancel_at_period_end BOOLEAN DEFAULT FALSE NOT NULL',
        }

        for column_name, alter_sql in user_column_sql.items():
            if column_name not in user_columns:
                conn.execute(db.text(alter_sql))

        if "user_usage" in inspector.get_table_names():
            usage_columns = [
                col["name"] for col in inspector.get_columns("user_usage")
            ]
            usage_column_sql = {
                "user_id": "ALTER TABLE user_usage ADD COLUMN user_id INTEGER",
                "plan": "ALTER TABLE user_usage ADD COLUMN plan VARCHAR(50) DEFAULT 'pro' NOT NULL",
                "ai_images_used": "ALTER TABLE user_usage ADD COLUMN ai_images_used INTEGER DEFAULT 0 NOT NULL",
                "content_packs_used": "ALTER TABLE user_usage ADD COLUMN content_packs_used INTEGER DEFAULT 0 NOT NULL",
                "usage_period_start": "ALTER TABLE user_usage ADD COLUMN usage_period_start TIMESTAMP",
                "usage_period_end": "ALTER TABLE user_usage ADD COLUMN usage_period_end TIMESTAMP",
                "created_at": "ALTER TABLE user_usage ADD COLUMN created_at TIMESTAMP",
                "updated_at": "ALTER TABLE user_usage ADD COLUMN updated_at TIMESTAMP",
            }

            for column_name, alter_sql in usage_column_sql.items():
                if column_name not in usage_columns:
                    conn.execute(db.text(alter_sql))

        conn.commit()

def get_file_type(filename: str) -> str:
    return media_service.get_file_type(filename)


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
    return media_service.normalize_image_to_jpeg(file_or_bytes)


def log_image_normalization_diagnostics(result, upload_url=None):
    return media_service.log_image_normalization_diagnostics(
        result,
        upload_url=upload_url,
        get_url_path_extension_func=get_url_path_extension,
    )


def upload_jpeg_to_cloudinary(file_or_bytes):
    return media_service.upload_jpeg_to_cloudinary(
        file_or_bytes,
        normalize_image_func=normalize_image_to_jpeg,
        upload_func=cloudinary.uploader.upload,
        log_diagnostics_func=log_image_normalization_diagnostics,
    )


def upload_to_cloudinary(file_or_url, force_jpeg=False):
    return media_service.upload_to_cloudinary(
        file_or_url,
        force_jpeg=force_jpeg,
        upload_jpeg_func=upload_jpeg_to_cloudinary,
        upload_func=cloudinary.uploader.upload,
    )


def get_placeholder_image_url():
    return content_service.get_placeholder_image_url()


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
    return media_service.make_instagram_safe_url(url)


def get_url_path_extension(url):
    return media_service.get_url_path_extension(url)


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
    return content_service.apply_image_style(prompt, style)


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

/human

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
    return captions_service.update_brand_coach(
        post,
        brand_context,
        evaluate_brand_match_func=evaluate_brand_match,
        parse_brand_feedback_func=parse_brand_feedback,
    )


def generate_openai_image(prompt):
    return images_service.generate_openai_image(
        prompt,
        openai_api_key=OPENAI_API_KEY,
        openai_client=openai_client,
        upload_jpeg_to_cloudinary_func=upload_jpeg_to_cloudinary,
    )


def generate_multiple_openai_images(prompt, count=1):
    return images_service.generate_multiple_openai_images(
        prompt,
        count=count,
        generate_openai_image_func=generate_openai_image,
    )


def send_payload_to_make(payload, webhook_url=None):
    return publishing_service.send_payload_to_make(
        payload,
        webhook_url,
        make_webhook_single=MAKE_WEBHOOK_SINGLE,
        make_webhook_carousel=MAKE_WEBHOOK_CAROUSEL,
    )


def build_single_payload(post):
    return publishing_service.build_single_payload(
        post,
        parse_platforms_func=parse_platforms,
    )


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
    return publishing_service.build_carousel_payload(
        group_id,
        user_id=user_id,
        get_ordered_carousel_posts_func=get_ordered_carousel_posts,
        parse_platforms_func=parse_platforms,
        make_instagram_safe_url_func=make_instagram_safe_url,
    )


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
    "get_usage_summary": lambda *args, **kwargs: get_usage_summary(
        *args,
        **kwargs,
    ),
    "reserve_ai_image_credits": (
        lambda *args, **kwargs: reserve_ai_image_credits(*args, **kwargs)
    ),
    "release_ai_image_credits": (
        lambda *args, **kwargs: release_ai_image_credits(*args, **kwargs)
    ),
    "usage_limit_message": lambda *args, **kwargs: usage_limit_message(
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
    "generate_openai_image": lambda *args, **kwargs: generate_openai_image(
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
    "get_usage_summary": lambda *args, **kwargs: get_usage_summary(
        *args,
        **kwargs,
    ),
    "reserve_ai_image_credits": (
        lambda *args, **kwargs: reserve_ai_image_credits(*args, **kwargs)
    ),
    "release_ai_image_credits": (
        lambda *args, **kwargs: release_ai_image_credits(*args, **kwargs)
    ),
    "usage_limit_message": lambda *args, **kwargs: usage_limit_message(
        *args,
        **kwargs,
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


app.extensions.setdefault("smu_studio_helpers", {}).update({
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
    "rewrite_caption_with_action": (
        lambda *args, **kwargs: rewrite_caption_with_action(*args, **kwargs)
    ),
    "grade_post_with_ai": (
        lambda *args, **kwargs: grade_post_with_ai(*args, **kwargs)
    ),
    "extract_overall_score": (
        lambda *args, **kwargs: extract_overall_score(*args, **kwargs)
    ),
})


def clean_transcript_text(text):
    return content_service.clean_transcript_text(text)


def extract_tiktok_transcript(tiktok_url):
    return content_service.extract_tiktok_transcript(
        tiktok_url,
        youtube_dl_cls=YoutubeDL,
        requests_get=requests.get,
        openai_api_key=OPENAI_API_KEY,
        openai_client=openai_client,
    )


def repurpose_tiktok_content(
    transcript,
    brand_context=""
):
    return tiktok_service.repurpose_tiktok_content(
        transcript,
        brand_context,
        openai_api_key=OPENAI_API_KEY,
        openai_client=openai_client,
    )


def check_scheduled_posts():
    with app.app_context():
        return scheduler_service.check_scheduled_posts(
            publish_post=publish_post,
            log_event=log_event,
            now_provider=time_utils.utc_now,
            post_model=Post,
            db_session=db.session,
        )



def generate_pending_carousel_images():
    def user_for_post(post):
        if not post.user_id:
            return None

        return db.session.get(User, post.user_id)

    def reserve_for_post(post, count=1):
        user = user_for_post(post)
        if not user:
            return False

        return reserve_ai_image_credits(user, count)

    def release_for_post(post, count=1):
        user = user_for_post(post)
        if user:
            release_ai_image_credits(user, count)

    def run_worker():
        return carousel_generation_service.generate_pending_carousel_images(
            post_model=Post,
            db_session=db.session,
            image_generator=generate_openai_image,
            reserve_image_credits=reserve_for_post,
            release_image_credits=release_for_post,
        )

    if has_app_context():
        return run_worker()

    with app.app_context():
        return run_worker()


def generate_content_pack(source_text, brand_context=""):
    return content_service.generate_content_pack(
        source_text,
        brand_context,
        openai_api_key=OPENAI_API_KEY,
        openai_client=openai_client,
    )


def extract_content_pack_section(text, section_name):
    return content_service.extract_content_pack_section(text, section_name)


def build_brand_context(user_id):
    return captions_service.build_brand_context(user_id)


def get_usage_summary(user):
    return usage_service.usage_summary(
        user,
        usage_model=UserUsage,
        db_session=db.session,
    )


def reserve_ai_image_credits(user, count=1):
    return usage_service.reserve_image_credits(
        user,
        count=count,
        usage_model=UserUsage,
        db_session=db.session,
    )


def release_ai_image_credits(user, count=1):
    return usage_service.release_image_credits(
        user,
        count=count,
        usage_model=UserUsage,
        db_session=db.session,
    )


def record_successful_image_usage(user, count=1):
    return usage_service.record_successful_image_usage(
        user,
        count=count,
        usage_model=UserUsage,
        db_session=db.session,
    )


def can_generate_content_pack(user):
    return usage_service.can_generate_content_pack(
        user,
        usage_model=UserUsage,
        db_session=db.session,
    )


def record_successful_content_pack_usage(user):
    return usage_service.record_successful_content_pack_usage(
        user,
        usage_model=UserUsage,
        db_session=db.session,
    )


def reserve_content_pack_credits(user):
    return usage_service.reserve_content_pack_credits(
        user,
        usage_model=UserUsage,
        db_session=db.session,
    )


def release_content_pack_credits(user):
    return usage_service.release_content_pack_credits(
        user,
        usage_model=UserUsage,
        db_session=db.session,
    )


def usage_limit_message(summary, credit_type):
    limit_key = "ai_images" if credit_type == "ai_images" else "content_packs"
    label = "AI image" if credit_type == "ai_images" else "Content Pack"
    limit = summary["limits"][limit_key]
    message = (
        f"You've used all {limit} {label} credits for this billing period."
    )

    if summary.get("usage_period_end"):
        message += f" Resets {summary['usage_period_end'].strftime('%d %B %Y')}."

    return message


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
    "get_usage_summary": lambda *args, **kwargs: get_usage_summary(
        *args,
        **kwargs,
    ),
    "can_generate_content_pack": (
        lambda *args, **kwargs: can_generate_content_pack(*args, **kwargs)
    ),
    "reserve_content_pack_credits": (
        lambda *args, **kwargs: reserve_content_pack_credits(*args, **kwargs)
    ),
    "release_content_pack_credits": (
        lambda *args, **kwargs: release_content_pack_credits(*args, **kwargs)
    ),
    "record_successful_content_pack_usage": (
        lambda *args, **kwargs: record_successful_content_pack_usage(
            *args,
            **kwargs,
        )
    ),
    "usage_limit_message": lambda *args, **kwargs: usage_limit_message(
        *args,
        **kwargs,
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
    "get_usage_summary": lambda *args, **kwargs: get_usage_summary(
        *args,
        **kwargs,
    ),
    "reserve_ai_image_credits": (
        lambda *args, **kwargs: reserve_ai_image_credits(*args, **kwargs)
    ),
    "release_ai_image_credits": (
        lambda *args, **kwargs: release_ai_image_credits(*args, **kwargs)
    ),
    "usage_limit_message": lambda *args, **kwargs: usage_limit_message(
        *args,
        **kwargs,
    ),
})

logging.getLogger(__name__).info(
    "SMU TikTok generation: structured-v1 loaded",
    extra={
        "smu_context": {
            "generation_version": tiktok_service.REPURPOSE_GENERATION_VERSION,
            "service_file": getattr(tiktok_service, "__file__", ""),
        },
    },
)


def rewrite_caption_with_action(post, brand_context="", action="improve"):
    return captions_service.rewrite_caption_with_action(
        post,
        brand_context,
        action,
        openai_client=openai_client,
    )


def grade_post_with_ai(post, brand_context=""):
    return captions_service.grade_post_with_ai(
        post,
        brand_context,
        openai_client=openai_client,
    )


def extract_overall_score(grade_result):
    return captions_service.extract_overall_score(grade_result)



def save_post_revision(post, source="manual"):
    return captions_service.save_post_revision(post, source=source)


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
            "label": "Set up Brand Brief",
            "complete": has_brand_brief,
            "url": url_for("brand_brief"),
        },
        {
            "label": "Create a Content Pack",
            "complete": bool(session.get("content_pack_started")),
            "url": url_for("content_pack"),
        },
        {
            "label": "Create your first post",
            "complete": has_first_post,
            "url": url_for("create_post"),
        },
        {
            "label": "Schedule a post",
            "complete": has_scheduled_post,
            "url": url_for("calendar_view"),
        },
        {
            "label": "View your calendar",
            "complete": bool(session.get("calendar_viewed")),
            "url": url_for("calendar_view"),
        },
        {
            "label": "Send your first post",
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
    "get_usage_summary": lambda *args, **kwargs: get_usage_summary(
        *args,
        **kwargs,
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
    return publishing_service.get_user_connected_accounts(user_id)


def get_enabled_platforms_for_user(
    selected_platforms,
    user_id=None,
):
    return publishing_service.get_enabled_platforms_for_user(
        selected_platforms,
        user_id=user_id,
        get_user_connected_accounts_func=get_user_connected_accounts,
    )


def get_user_make_webhook(post_type, user_id=None):
    return publishing_service.get_user_make_webhook(
        post_type,
        user_id=user_id,
        get_user_connected_accounts_func=get_user_connected_accounts,
        make_webhook_single=MAKE_WEBHOOK_SINGLE,
        make_webhook_carousel=MAKE_WEBHOOK_CAROUSEL,
    )


def publish_post_to_make(post, user_id):
    return publishing_service.publish_post_to_make(
        post,
        user_id,
        get_enabled_platforms_func=get_enabled_platforms_for_user,
        build_carousel_payload_func=build_carousel_payload,
        get_user_make_webhook_func=get_user_make_webhook,
        send_payload_func=send_payload_to_make,
        get_ordered_carousel_posts_func=get_ordered_carousel_posts,
        build_single_payload_func=build_single_payload,
        log_single_image_diagnostics_func=log_single_image_diagnostics,
        get_user_connected_accounts_func=get_user_connected_accounts,
        prepare_linkedin_post_func=prepare_linkedin_post,
        publish_prepared_linkedin_post_func=publish_prepared_linkedin_post,
    )


def publish_post(post, user_id):
    if publishing_service.should_use_direct_zernio_publish(
        post,
        user_id,
        get_user_connected_accounts_func=get_user_connected_accounts,
        get_enabled_platforms_func=get_enabled_platforms_for_user,
        get_user_make_webhook_func=get_user_make_webhook,
    ):
        return publishing_service.publish_post_direct(
            post,
            get_user_connected_accounts(user_id),
            api_key=app.config.get("ZERNIO_API_KEY", ""),
            base_url=app.config.get("ZERNIO_BASE_URL"),
        )

    return publish_post_to_make(post, user_id)


def prepare_linkedin_post(post, connected_account):
    return linkedin_publishing_service.prepare_post_for_publish(
        post,
        connected_account,
    )


def publish_prepared_linkedin_post(prepared_post):
    return linkedin_publishing_service.publish_prepared_post(prepared_post)


def publish_linkedin_post(post, connected_account):
    return linkedin_publishing_service.publish_post(
        post,
        connected_account,
    )


def publish_linkedin_text_post(post, connected_account):
    return linkedin_publishing_service.publish_text_only_post(
        post,
        connected_account,
    )



@app.template_filter("from_json")
def from_json_filter(value):
    try:
        return json.loads(value)
    except Exception:
        return {}


def improve_post_with_ai(post, brand_context=""):
    prompt = f"""
You are an expert social media copywriter.

/human

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

scheduler = BackgroundScheduler(
    timezone="UTC"
)
scheduler_lease_coordinator = None
_scheduler_has_lease = None


def _scheduler_owner_id():
    instance_id = (
        os.getenv("RENDER_INSTANCE_ID")
        or os.getenv("HOSTNAME")
        or "local"
    )
    return f"{instance_id}:{os.getpid()}:{uuid.uuid4()}"


def _default_scheduler_lease_coordinator():
    return SchedulerLeaseCoordinator(
        lease_model=SchedulerLease,
        db_session=db.session,
        owner_id=_scheduler_owner_id(),
        lease_seconds=app.config["SMU_SCHEDULER_LEASE_SECONDS"],
    )


def confirm_scheduler_ownership(lease_coordinator=None):
    global _scheduler_has_lease

    lease_coordinator = lease_coordinator or scheduler_lease_coordinator
    if lease_coordinator is None:
        return False

    with app.app_context():
        owns_lease = lease_coordinator.acquire_or_renew()

    if owns_lease != _scheduler_has_lease:
        status = "acquired" if owns_lease else "standby"
        print(f"Scheduler lease {status}.")
        log_event("scheduler_lease", status=status)
    _scheduler_has_lease = owns_lease
    return owns_lease


def _run_scheduler_job_if_owner(job_func, lease_coordinator=None):
    if not confirm_scheduler_ownership(lease_coordinator):
        return None
    return job_func()


def register_scheduler_jobs(scheduler_instance=None, lease_coordinator=None):
    scheduler_instance = scheduler_instance or scheduler
    scheduler_instance.add_job(
        lambda: _run_scheduler_job_if_owner(
            generate_pending_carousel_images,
            lease_coordinator,
        ),
        trigger="interval",
        seconds=20,
        id="generate_pending_images",
        max_instances=1,
        replace_existing=True,
    )
    scheduler_instance.add_job(
        lambda: _run_scheduler_job_if_owner(
            check_scheduled_posts,
            lease_coordinator,
        ),
        trigger="interval",
        seconds=30,
        id="check_scheduled_posts",
        max_instances=1,
        replace_existing=True,
    )
    scheduler_instance.add_job(
        lambda: confirm_scheduler_ownership(lease_coordinator),
        trigger="interval",
        seconds=app.config["SMU_SCHEDULER_RENEW_SECONDS"],
        id="scheduler_lease_heartbeat",
        max_instances=1,
        replace_existing=True,
    )


def start_background_scheduler(scheduler_instance=None, lease_coordinator=None):
    global scheduler_lease_coordinator

    scheduler_instance = scheduler_instance or scheduler
    if not app.config.get("SMU_SCHEDULER_ENABLED", True):
        log_event("scheduler_startup", status="disabled")
        return False
    if scheduler_instance.running:
        return False

    if lease_coordinator is None:
        lease_coordinator = _default_scheduler_lease_coordinator()
    scheduler_lease_coordinator = lease_coordinator

    print("Starting background scheduler...")
    log_event("scheduler_startup", status="starting")
    confirm_scheduler_ownership(lease_coordinator)
    register_scheduler_jobs(scheduler_instance, lease_coordinator)
    scheduler_instance.start()
    print("Background scheduler started.")
    print("Registered jobs:", scheduler_instance.get_jobs())
    log_event(
        "scheduler_startup",
        status="started",
        job_count=len(scheduler_instance.get_jobs()),
    )
    return True


def stop_background_scheduler(scheduler_instance=None):
    global _scheduler_has_lease

    scheduler_instance = scheduler_instance or scheduler
    if not scheduler_instance.running:
        return False
    scheduler_instance.shutdown(wait=False)
    if scheduler_lease_coordinator is not None:
        with app.app_context():
            scheduler_lease_coordinator.release()
    _scheduler_has_lease = False
    log_event("scheduler_startup", status="stopped")
    return True


if __name__ == "__main__":
    start_background_scheduler()
    app.run(
        debug=True,
        use_reloader=False,
    )
