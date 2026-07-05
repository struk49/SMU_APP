import os
import time
import json
import uuid
import re
import base64
from datetime import datetime

import pytz
import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler

import cloudinary
import cloudinary.uploader
from openai import OpenAI
from yt_dlp import YoutubeDL

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = (
    DATABASE_URL or f"sqlite:///{os.path.join(BASE_DIR, 'posts.db')}"
)
print("DATABASE:", app.config["SQLALCHEMY_DATABASE_URI"][:50])
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 280,
    "pool_size": 5,
    "max_overflow": 2,
}

db = SQLAlchemy(app)

import json


login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message_category = "warning"

MAKE_WEBHOOK_SINGLE = os.getenv("MAKE_WEBHOOK_SINGLE", "").strip()
MAKE_WEBHOOK_CAROUSEL = os.getenv("MAKE_WEBHOOK_CAROUSEL", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

openai_client = OpenAI(api_key=OPENAI_API_KEY)

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    brand_brief = db.relationship(
        "BrandBrief",
        backref="user",
        uselist=False,
        lazy=True
    )
    connected_account = db.relationship(
    "ConnectedAccount",
    backref="user",
    uselist=False,
    lazy=True
)

    posts = db.relationship("Post", backref="user", lazy=True)


class PostRevision(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    post_id = db.Column(db.Integer, db.ForeignKey("post.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    version_number = db.Column(db.Integer, nullable=False)
    caption = db.Column(db.Text, nullable=False)
    score = db.Column(db.Float, nullable=True)
    source = db.Column(db.String(50), default="manual")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    file_url = db.Column(db.String(500), nullable=False)
    file_type = db.Column(db.String(20), nullable=False)
    prompt = db.Column(db.Text)
    caption = db.Column(db.Text)
    status = db.Column(db.String(50), default="draft")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sent_at = db.Column(db.DateTime)
    scheduled_time = db.Column(db.DateTime, nullable=True)
    group_id = db.Column(db.String(100), nullable=True)
    post_type = db.Column(db.String(50), default="single")
    platforms = db.Column(db.String(200), default="instagram,facebook")
    sort_order = db.Column(db.Integer, default=0)
    is_cover = db.Column(db.Boolean, default=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    grade_result = db.Column(db.Text, nullable=True)
    grade_score = db.Column(db.Float, nullable=True)
    graded_at = db.Column(db.DateTime, nullable=True)
    # AI Improved Version
    improved_caption = db.Column(db.Text, nullable=True)
    improved_at = db.Column(db.DateTime, nullable=True)
    # Brand Coach
    brand_score = db.Column(db.Float, nullable=True)
    brand_feedback = db.Column(db.Text, nullable=True)

    revisions = db.relationship(
    "PostRevision",
    backref="post",
    lazy=True,
    cascade="all, delete-orphan"
)

class BrandBrief(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, unique=True)

    business_name = db.Column(db.String(200))
    niche = db.Column(db.String(200))
    target_audience = db.Column(db.Text)
    offer = db.Column(db.Text)
    tone_of_voice = db.Column(db.String(200))
    content_goals = db.Column(db.Text)
    main_platforms = db.Column(db.String(300))
    cta_style = db.Column(db.String(200))
    words_to_avoid = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ConnectedAccount(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, unique=True)

    instagram_connected = db.Column(db.Boolean, default=False)
    facebook_connected = db.Column(db.Boolean, default=False)
    linkedin_connected = db.Column(db.Boolean, default=False)
    pinterest_connected = db.Column(db.Boolean, default=False)
    reddit_connected = db.Column(db.Boolean, default=False)
    x_connected = db.Column(db.Boolean, default=False)

    make_webhook_single = db.Column(db.String(500))
    make_webhook_carousel = db.Column(db.String(500))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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


def convert_uk_time_to_utc(scheduled_time_str):
    uk = pytz.timezone("Europe/London")
    local_time = datetime.fromisoformat(scheduled_time_str)
    local_time = uk.localize(local_time)
    utc_time = local_time.astimezone(pytz.utc)
    return utc_time.replace(tzinfo=None)


def upload_to_cloudinary(file_or_url):
    return cloudinary.uploader.upload(
        file_or_url, folder="social_posts", resource_type="auto"
    )


def get_placeholder_image_url():
    return "https://res.cloudinary.com/demo/image/upload/w_1080,h_1080,c_fill,b_rgb:111111/l_text:Arial_60_bold:Generating%20Image,co_rgb:ffffff/sample.jpg"


def make_instagram_safe_url(url):
    return url.replace("/upload/", "/upload/c_fill,w_1080,h_1080,q_auto,f_jpg/")


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


import json


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

    upload_result = cloudinary.uploader.upload(
        image_bytes, folder="social_posts", resource_type="image"
    )

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
            webhook_url = os.getenv("MAKE_WEBHOOK_CAROUSEL")
        else:
            webhook_url = os.getenv("MAKE_WEBHOOK_SINGLE")

    if not webhook_url:
        raise Exception("No Make webhook configured.")

    response = requests.post(webhook_url, json=payload, timeout=30)

    if response.status_code >= 400:
        raise Exception(f"Make webhook failed: {response.status_code} {response.text}")

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


def get_ordered_carousel_posts(group_id):
    return (
        Post.query.filter_by(group_id=group_id)
        .order_by(Post.is_cover.desc(), Post.sort_order.asc(), Post.id.asc())
        .all()
    )


def build_carousel_payload(group_id):
    posts = get_ordered_carousel_posts(group_id)

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


def clean_transcript_text(text):
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_tiktok_transcript(tiktok_url):
    ydl_opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en"],
    }

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(tiktok_url, download=False)

    title = info.get("title", "")
    description = info.get("description", "")

    transcript_parts = []

    if description:
        transcript_parts.append(description)

    automatic_captions = info.get("automatic_captions", {})
    subtitles = info.get("subtitles", {})

    if subtitles or automatic_captions:
        transcript_parts.append(
            "Captions are available, but this version uses TikTok description and metadata."
        )

    transcript = clean_transcript_text(" ".join(transcript_parts))

    if not transcript:
        transcript = title

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
        now = datetime.utcnow()

        posts = (
            Post.query.filter(
                Post.scheduled_time != None,
                Post.status == "scheduled",
                Post.scheduled_time <= now,
            )
            .order_by(Post.sort_order.asc(), Post.id.asc())
            .all()
        )

        processed_groups = set()

        for post in posts:
            try:
                if post.post_type == "carousel" and post.group_id:
                    if post.group_id in processed_groups:
                        continue

                    payload = build_carousel_payload(post.group_id)

                    if not payload:
                        continue

                    send_payload_to_make(payload)

                    group_posts = Post.query.filter_by(group_id=post.group_id).all()

                    for group_post in group_posts:
                        group_post.status = "sent_to_make"
                        group_post.sent_at = datetime.utcnow()

                    processed_groups.add(post.group_id)

                    print(f"✅ Sent scheduled carousel {post.group_id}")

                else:
                    payload = build_single_payload(post)
                    send_payload_to_make(payload)

                    post.status = "sent_to_make"
                    post.sent_at = datetime.utcnow()

                    print(f"✅ Sent scheduled post {post.id}")

                db.session.commit()

            except Exception as e:
                print("Scheduler error:", e)


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


def extract_overall_score(grade_result):
    if not grade_result:
        return None

    match = re.search(r"OVERALL_SCORE:\s*([0-9]+(?:\.[0-9]+)?)\/10", grade_result)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None

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


@app.route("/")
@login_required
def index():
    status_filter = request.args.get("status", "all")
    type_filter = request.args.get("type", "all")
    platform_filter = request.args.get("platform", "all")
    search_query = request.args.get("q", "").strip()

    query = Post.query.filter_by(user_id=current_user.id)

    if status_filter != "all":
        query = query.filter(Post.status == status_filter)

    if type_filter != "all":
        query = query.filter(Post.post_type == type_filter)

    if platform_filter != "all":
        query = query.filter(Post.platforms.ilike(f"%{platform_filter}%"))

    if search_query:
        search_term = f"%{search_query}%"
        query = query.filter(
            db.or_(
                Post.caption.ilike(search_term),
                Post.prompt.ilike(search_term),
                Post.platforms.ilike(search_term),
                Post.status.ilike(search_term),
                Post.post_type.ilike(search_term),
            )
        )

    posts = query.order_by(
        Post.created_at.desc(),
        Post.is_cover.desc(),
        Post.sort_order.asc(),
        Post.id.asc(),
    ).all()

    stats = {
        "total": Post.query.filter_by(user_id=current_user.id).count(),
        "drafts": Post.query.filter_by(user_id=current_user.id, status="draft").count(),
        "scheduled": Post.query.filter_by(
            user_id=current_user.id, status="scheduled"
        ).count(),
        "sent": Post.query.filter_by(
            user_id=current_user.id, status="sent_to_make"
        ).count(),
        "carousels": Post.query.filter_by(
            user_id=current_user.id, post_type="carousel"
        ).count(),
    }

    return render_template(
        "index.html",
        posts=posts,
        status_filter=status_filter,
        type_filter=type_filter,
        platform_filter=platform_filter,
        search_query=search_query,
        stats=stats,
    )


@app.route("/create", methods=["GET", "POST"])
@login_required
def create_post():
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

                    upload_result = upload_to_cloudinary(file)

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

    return render_template("create_post.html")

@app.route("/post/<int:post_id>")
@login_required
def view_post(post_id):
    post = Post.query.get_or_404(post_id)

    if post.user_id != current_user.id:
        flash("You do not have access to this post.", "danger")
        return redirect(url_for("index"))

    carousel_posts = []

    if post.group_id:
        carousel_posts = get_ordered_carousel_posts(post.group_id)

    return render_template("view_post.html", post=post, carousel_posts=carousel_posts)


@app.route("/edit-post/<int:post_id>", methods=["GET", "POST"])
@login_required
def edit_post(post_id):
    post = Post.query.get_or_404(post_id)

    if post.user_id != current_user.id:
        flash("You do not have access to this post.", "danger")
        return redirect(url_for("index"))

    if post.post_type == "carousel":
        flash("Use Edit Carousel for carousel posts.", "warning")
        return redirect(url_for("view_post", post_id=post.id))

    if request.method == "POST":
        caption = request.form.get("caption", "").strip()
        prompt = request.form.get("prompt", "").strip()
        platforms = request.form.getlist("platforms")
        regenerate_image = request.form.get("regenerate_image") == "on"

        if not platforms:
            platforms = ["instagram", "facebook"]

        post.caption = caption
        post.prompt = prompt
        post.platforms = ",".join(platforms)

        try:
            if regenerate_image:
                if not prompt:
                    flash("Add a prompt before regenerating the image.", "danger")
                    return redirect(url_for("edit_post", post_id=post.id))

                image_url = generate_openai_image(prompt)
                post.file_url = image_url
                post.file_type = "image"

            db.session.commit()

            flash("Post updated successfully.", "success")
            return redirect(url_for("view_post", post_id=post.id))

        except Exception as e:
            print("Edit post error:", e)
            flash(f"Failed to update post: {e}", "danger")
            return redirect(url_for("edit_post", post_id=post.id))

    return render_template("edit_post.html", post=post)


@app.route("/rewrite-caption/<int:post_id>", methods=["POST"])
@login_required
def rewrite_caption(post_id):
    post = Post.query.get_or_404(post_id)

    if post.user_id != current_user.id:
        flash("You do not have access to this post.", "danger")
        return redirect(url_for("index"))

    rewrite_type = request.form.get("rewrite_type", "").strip()
    current_caption = request.form.get("caption", "").strip()

    if not current_caption:
        flash("Add a caption before using AI rewrite.", "danger")
        return redirect(url_for("edit_post", post_id=post.id))

    try:
        new_caption = rewrite_caption_with_ai(current_caption, rewrite_type)

        post.caption = new_caption
        db.session.commit()

        flash("Caption rewritten successfully.", "success")
        return redirect(url_for("edit_post", post_id=post.id))

    except Exception as e:
        print("Rewrite caption error:", e)
        flash(f"Failed to rewrite caption: {e}", "danger")
        return redirect(url_for("edit_post", post_id=post.id))


@app.route("/rewrite-carousel-caption/<group_id>", methods=["POST"])
@login_required
def rewrite_carousel_caption(group_id):
    posts = get_ordered_carousel_posts(group_id)

    if not posts:
        flash("Carousel not found.", "danger")
        return redirect(url_for("index"))

    rewrite_type = request.form.get("rewrite_type", "").strip()
    current_caption = request.form.get("caption", "").strip()

    if not current_caption:
        flash("Add a caption before using AI rewrite.", "danger")
        return redirect(url_for("edit_carousel", group_id=group_id))

    try:
        new_caption = rewrite_caption_with_ai(current_caption, rewrite_type)

        for post in posts:
            post.caption = new_caption

        db.session.commit()

        flash("Carousel caption rewritten successfully.", "success")
        return redirect(url_for("edit_carousel", group_id=group_id))

    except Exception as e:
        print("Rewrite carousel caption error:", e)
        flash(f"Failed to rewrite carousel caption: {e}", "danger")
        return redirect(url_for("edit_carousel", group_id=group_id))


@app.route("/duplicate-post/<int:post_id>", methods=["POST"])
@login_required
def duplicate_post(post_id):
    original = Post.query.get_or_404(post_id)

    if original.user_id != current_user.id:
        flash("You do not have access to this post.", "danger")
        return redirect(url_for("index"))

    if original.post_type == "carousel":
        flash("Use duplicate carousel for carousel posts.", "warning")
        return redirect(url_for("view_post", post_id=original.id))

    try:
        new_post = Post(
            file_url=original.file_url,
            file_type=original.file_type,
            prompt=original.prompt,
            caption=original.caption,
            status="draft",
            platforms=original.platforms,
            post_type="single",
            sort_order=0,
            is_cover=False,
            group_id=None,
            scheduled_time=None,
            sent_at=None,
            user_id=current_user.id,
        )

        db.session.add(new_post)
        db.session.commit()

        flash("Post duplicated successfully.", "success")
        return redirect(url_for("view_post", post_id=new_post.id))

    except Exception as e:
        print("Duplicate post error:", e)
        flash(f"Failed to duplicate post: {e}", "danger")
        return redirect(url_for("view_post", post_id=original.id))


@app.route("/duplicate-carousel/<group_id>", methods=["POST"])
@login_required
def duplicate_carousel(group_id):
    original_posts = get_ordered_carousel_posts(group_id)

    if not original_posts:
        flash("Carousel not found.", "danger")
        return redirect(url_for("index"))

    try:
        new_group_id = str(uuid.uuid4())
        first_new_post = None

        for post in original_posts:
            new_post = Post(
                file_url=post.file_url,
                file_type=post.file_type,
                prompt=post.prompt,
                caption=post.caption,
                status="draft",
                platforms=post.platforms,
                post_type="carousel",
                group_id=new_group_id,
                sort_order=post.sort_order,
                is_cover=post.is_cover,
                scheduled_time=None,
                sent_at=None,
                user_id=current_user.id,
            )

            db.session.add(new_post)

            if first_new_post is None:
                first_new_post = new_post

        db.session.commit()

        flash("Carousel duplicated successfully.", "success")
        return redirect(url_for("view_post", post_id=first_new_post.id))

    except Exception as e:
        print("Duplicate carousel error:", e)
        flash(f"Failed to duplicate carousel: {e}", "danger")
        return redirect(url_for("index"))

def get_user_connected_accounts():
    return ConnectedAccount.query.filter_by(user_id=current_user.id).first()


def get_enabled_platforms_for_user(selected_platforms):
    accounts = get_user_connected_accounts()

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

    return [
        platform for platform in selected_platforms
        if platform_map.get(platform, False)
    ]


def get_user_make_webhook(post_type):
    accounts = get_user_connected_accounts()

    if not accounts:
        return None

    if post_type == "carousel":
        return accounts.make_webhook_carousel or None

    return accounts.make_webhook_single or None


@app.route("/send/<int:post_id>", methods=["POST"])
@login_required
def send_to_make(post_id):
    post = Post.query.get_or_404(post_id)

    if post.user_id != current_user.id:
        flash("You do not have access to this post.", "danger")
        return redirect(url_for("index"))

    try:
        if post.group_id:
            payload = build_carousel_payload(post.group_id)

            if not payload:
                flash("Carousel payload could not be built.", "danger")
                return redirect(url_for("index"))
            selected_platforms = post.platforms.split(",") if post.platforms else []
            enabled_platforms = get_enabled_platforms_for_user(selected_platforms)

            if not enabled_platforms:
                flash("No connected platforms enabled for this post. Check Connected Accounts.", "danger")
                return redirect(url_for("view_post", post_id=post.id))

            send_payload_to_make(payload, webhook_url)

            group_posts = get_ordered_carousel_posts(post.group_id)

            for group_post in group_posts:
                group_post.status = "sent_to_make"
                group_post.sent_at = datetime.utcnow()

            db.session.commit()

            flash("Carousel sent to Make.com successfully.", "success")
            return redirect(url_for("index"))

        payload = build_single_payload(post)
        send_payload_to_make(payload)
        payload["platforms"] = enabled_platforms
        webhook_url = get_user_make_webhook("single")

        if not webhook_url:
            flash("No single post webhook configured. Add it in Connected Accounts.", "danger")
            return redirect(url_for("view_post", post_id=post.id))

        send_payload_to_make(payload, webhook_url)

        post.status = "sent_to_make"
        post.sent_at = datetime.utcnow()

        db.session.commit()

        flash("Sent to Make.com successfully.", "success")

    except Exception as e:
        print("Send to Make error:", e)
        flash(f"Failed: {e}", "danger")

    return redirect(url_for("view_post", post_id=post.id))


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
        update_brand_coach(post, brand_context)

        db.session.commit()

        flash("AI Studio action completed.", "success")

    except Exception as e:
        print("Studio action error:", e)
        flash(f"Failed to run studio action: {e}", "danger")

    return redirect(url_for("post_studio", post_id=post.id))


@app.route("/send-carousel/<group_id>", methods=["POST"])
@login_required
def send_carousel_to_make(group_id):
    posts = get_ordered_carousel_posts(group_id)

    if not posts:
        flash("Carousel not found.", "danger")
        return redirect(url_for("index"))

    try:
        selected_platforms = posts[0].platforms.split(",") if posts[0].platforms else []
        enabled_platforms = get_enabled_platforms_for_user(selected_platforms)

        if not enabled_platforms:
            flash(
                "No connected platforms enabled for this carousel. Check Connected Accounts.",
                "danger",
            )
            return redirect(url_for("view_post", post_id=posts[0].id))

        webhook_url = get_user_make_webhook("carousel")

        if not webhook_url:
            flash(
                "No carousel webhook configured. Add it in Connected Accounts.",
                "danger",
            )
            return redirect(url_for("view_post", post_id=posts[0].id))

        payload = build_carousel_payload(group_id)

        if not payload:
            flash("Carousel payload could not be built.", "danger")
            return redirect(url_for("index"))

        payload["platforms"] = enabled_platforms

        send_payload_to_make(payload, webhook_url)

        for post in posts:
            post.status = "sent_to_make"
            post.sent_at = datetime.utcnow()

        db.session.commit()

        flash("Carousel sent to Make.com successfully.", "success")
        return redirect(url_for("index"))

    except Exception as e:
        print("Send carousel error:", e)
        flash(f"Failed: {e}", "danger")
        return redirect(url_for("view_post", post_id=posts[0].id))


@app.route("/edit-carousel/<group_id>", methods=["GET", "POST"])
@login_required
def edit_carousel(group_id):
    posts = get_ordered_carousel_posts(group_id)

    if not posts:
        flash("Carousel not found.", "danger")
        return redirect(url_for("index"))

    if request.method == "POST":
        caption = request.form.get("caption", "").strip()
        platforms = request.form.getlist("platforms")
        carousel_order_raw = request.form.get("carousel_order", "")
        cover_post_id_raw = request.form.get("cover_post_id", "")

        if not platforms:
            platforms = ["instagram", "facebook"]

        platforms_string = ",".join(platforms)

        try:
            order = json.loads(carousel_order_raw) if carousel_order_raw else []
            cover_post_id = int(cover_post_id_raw) if cover_post_id_raw else None

            post_map = {post.id: post for post in posts}

            for index, post_id in enumerate(order):
                if post_id in post_map:
                    post_map[post_id].sort_order = index
                    post_map[post_id].caption = caption
                    post_map[post_id].platforms = platforms_string
                    post_map[post_id].is_cover = post_id == cover_post_id

            if cover_post_id is None and posts:
                posts[0].is_cover = True

            db.session.commit()

            flash("Carousel updated successfully.", "success")
            return redirect(url_for("view_post", post_id=posts[0].id))

        except Exception as e:
            print("Edit carousel error:", e)
            flash(f"Failed to update carousel: {e}", "danger")
            return redirect(url_for("edit_carousel", group_id=group_id))

    return render_template("edit_carousel.html", posts=posts, carousel=posts[0])


@app.route("/schedule/<int:post_id>", methods=["POST"])
@login_required
def schedule_post(post_id):
    post = Post.query.get_or_404(post_id)

    if post.user_id != current_user.id:
        flash("You do not have access to this post.", "danger")
        return redirect(url_for("index"))

    scheduled_time_str = request.form.get("scheduled_time")

    if not scheduled_time_str:
        flash("Please select a date and time.", "danger")
        return redirect(url_for("view_post", post_id=post.id))

    try:
        scheduled_time = convert_uk_time_to_utc(scheduled_time_str)

        if post.group_id:
            group_posts = get_ordered_carousel_posts(post.group_id)

            for group_post in group_posts:
                group_post.scheduled_time = scheduled_time
                group_post.status = "scheduled"

            db.session.commit()

            flash("Carousel scheduled successfully.", "success")
            return redirect(url_for("index"))

        post.scheduled_time = scheduled_time
        post.status = "scheduled"

        db.session.commit()

        flash("Post scheduled successfully.", "success")

    except Exception as e:
        print("Schedule error:", e)
        flash(f"Error scheduling post: {e}", "danger")

    return redirect(url_for("view_post", post_id=post.id))


@app.route("/delete/<int:post_id>", methods=["POST"])
@login_required
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)

    if post.user_id != current_user.id:
        flash("You do not have access to this post.", "danger")
        return redirect(url_for("index"))

    if post.group_id:
        group_posts = get_ordered_carousel_posts(post.group_id)

        for group_post in group_posts:
            db.session.delete(group_post)

        db.session.commit()

        flash("Carousel deleted.", "warning")
        return redirect(url_for("index"))

    db.session.delete(post)
    db.session.commit()

    flash("Post deleted.", "warning")
    return redirect(url_for("index"))


@app.route("/delete-carousel/<group_id>", methods=["POST"])
@login_required
def delete_carousel(group_id):
    posts = get_ordered_carousel_posts(group_id)

    if not posts:
        flash("Carousel not found.", "danger")
        return redirect(url_for("index"))

    for post in posts:
        db.session.delete(post)

    db.session.commit()

    flash("Carousel deleted.", "warning")
    return redirect(url_for("index"))


@app.route("/tiktok", methods=["GET", "POST"])
@login_required
def tiktok_repurpose():
    transcript = None
    generated_content = None
    tiktok_url = ""

    if request.method == "POST":
        tiktok_url = request.form.get("tiktok_url", "").strip()

        if not tiktok_url:
            flash("Please enter a TikTok URL.", "danger")
            return redirect(url_for("tiktok_repurpose"))

        try:
            transcript = extract_tiktok_transcript(tiktok_url)
            brand_context = build_brand_context(current_user.id)

            generated_content = repurpose_tiktok_content(
                transcript,
                brand_context
            )

        except Exception as e:
            print("TikTok repurpose error:", e)
            flash(f"Failed: {e}", "danger")

    return render_template(
        "tiktok.html",
        tiktok_url=tiktok_url,
        transcript=transcript,
        generated_content=generated_content,
    )


@app.route("/tiktok/create-draft", methods=["POST"])
@login_required
def create_tiktok_draft():
    caption = request.form.get("caption", "").strip()
    image_prompt = request.form.get("image_prompt", "").strip()
    image_style = request.form.get("image_style", "").strip()

    if not caption:
        flash("No caption found. Please generate TikTok content first.", "danger")
        return redirect(url_for("tiktok_repurpose"))

    if not image_prompt:
        flash("No image prompt found. Please generate TikTok content first.", "danger")
        return redirect(url_for("tiktok_repurpose"))

    try:
        styled_prompt = apply_image_style(image_prompt, image_style)
        image_url = generate_openai_image(styled_prompt)

        post = Post(
            file_url=image_url,
            file_type="image",
            prompt=styled_prompt,
            caption=caption,
            platforms="instagram,facebook",
            post_type="single",
            status="draft",
            sort_order=0,
            is_cover=False,
            user_id=current_user.id,
        )

        db.session.add(post)
        db.session.commit()

        flash("TikTok content image generated and saved as draft.", "success")
        return redirect(url_for("view_post", post_id=post.id))

    except Exception as e:
        print("Create TikTok draft error:", e)
        flash(f"Failed to create draft: {e}", "danger")
        return redirect(url_for("tiktok_repurpose"))


@app.route("/tiktok/create-carousel-draft", methods=["POST"])
@login_required
def create_tiktok_carousel_draft():
    caption = request.form.get("caption", "").strip()
    image_prompt = request.form.get("image_prompt", "").strip()
    image_style = request.form.get("image_style", "").strip()
    carousel_idea = request.form.get("carousel_idea", "").strip()

    if not caption:
        flash("No caption found. Please generate TikTok content first.", "danger")
        return redirect(url_for("tiktok_repurpose"))

    if not carousel_idea:
        flash("No carousel idea found. Please generate TikTok content first.", "danger")
        return redirect(url_for("tiktok_repurpose"))

    try:
        brand_context = build_brand_context(
    current_user.id
)

        image_prompt = f"""
        {brand_context}

        {image_prompt}
        """

        styled_image_prompt = apply_image_style(
            image_prompt,
            image_style
        )

        slides = []

        for line in carousel_idea.splitlines():
            line = line.strip()

            if line.lower().startswith("slide"):
                parts = line.split(":", 1)

                if len(parts) == 2 and parts[1].strip():
                    slides.append(parts[1].strip())

        if not slides:
            slides = [
                line.strip() for line in carousel_idea.splitlines() if line.strip()
            ]

        slides = slides[:6]

        if len(slides) < 2:
            flash("Carousel needs at least 2 slides.", "danger")
            return redirect(url_for("tiktok_repurpose"))

        group_id = str(uuid.uuid4())
        placeholder_url = get_placeholder_image_url()

        for index, slide_text in enumerate(slides):
            if index == 0:
                full_prompt = f"""
Create a HIGH-CONVERTING viral Instagram carousel COVER slide.

Main headline:
{slide_text}

Use this visual direction:
{styled_image_prompt}

Design style:
- dark background
- bold typography
- yellow accent blocks
- white headline text
- green and blue highlight colours
- square 1:1 format
"""
            else:
                full_prompt = f"""
Create a HIGH-CONVERTING Instagram carousel educational slide.

Slide content:
{slide_text}

Use this visual direction:
{styled_image_prompt}

Design style:
- dark background
- bold typography
- yellow highlight boxes
- white main text
- green accents
- square 1:1 format
"""

            post = Post(
                file_url=placeholder_url,
                file_type="image",
                prompt=full_prompt,
                caption=caption,
                platforms="instagram,facebook",
                post_type="carousel",
                status="generating",
                group_id=group_id,
                sort_order=index,
                is_cover=(index == 0),
                user_id=current_user.id,
            )

            db.session.add(post)

        db.session.commit()

        first_post = (
            Post.query.filter_by(group_id=group_id, user_id=current_user.id)
            .order_by(Post.sort_order.asc())
            .first()
        )

        flash(
            "TikTok carousel draft created. Images are generating in the background.",
            "success",
        )
        return redirect(url_for("view_post", post_id=first_post.id))

    except Exception as e:
        print("Create TikTok carousel draft error:", e)
        flash(f"Failed to create carousel draft: {e}", "danger")
        return redirect(url_for("tiktok_repurpose"))


@app.route("/content-pack", methods=["GET", "POST"])
@login_required
def content_pack():
    source_text = ""
    content_pack_result = None

    if request.method == "POST":
        source_type = request.form.get("source_type", "text")
        source_input = request.form.get("source_input", "").strip()

        if not source_input:
            flash("Please enter a TikTok URL or topic/text.", "danger")
            return redirect(url_for("content_pack"))

        try:
            if source_type == "tiktok":
                source_text = extract_tiktok_transcript(source_input)
            else:
                source_text = source_input

            brand_context = build_brand_context(current_user.id)
            content_pack_result = generate_content_pack(source_text, brand_context)

        except Exception as e:
            print("Content pack error:", e)
            flash(f"Failed: {e}", "danger")

    return render_template(
        "content_pack.html",
        source_text=source_text,
        content_pack_result=content_pack_result,
    )


@app.route("/brand-brief", methods=["GET", "POST"])
@login_required
def brand_brief():
    brief = BrandBrief.query.filter_by(user_id=current_user.id).first()

    if request.method == "POST":
        if not brief:
            brief = BrandBrief(user_id=current_user.id)
            db.session.add(brief)

        brief.business_name = request.form.get("business_name", "").strip()
        brief.niche = request.form.get("niche", "").strip()
        brief.target_audience = request.form.get("target_audience", "").strip()
        brief.offer = request.form.get("offer", "").strip()
        brief.tone_of_voice = request.form.get("tone_of_voice", "").strip()
        brief.content_goals = request.form.get("content_goals", "").strip()
        brief.main_platforms = ",".join(request.form.getlist("main_platforms"))
        brief.cta_style = request.form.get("cta_style", "").strip()
        brief.words_to_avoid = request.form.get("words_to_avoid", "").strip()

        db.session.commit()

        flash("Brand Brief saved successfully.", "success")
        return redirect(url_for("brand_brief"))

    return render_template("brand_brief.html", brief=brief)


@app.route("/calendar")
@login_required
def calendar_view():
    scheduled_posts = (
        Post.query.filter(
            Post.user_id == current_user.id,
            Post.scheduled_time != None,
            Post.status == "scheduled",
        )
        .order_by(Post.scheduled_time.asc(), Post.created_at.asc())
        .all()
    )

    grouped_posts = {}

    for post in scheduled_posts:
        date_key = post.scheduled_time.strftime("%A %d %B %Y")
        grouped_posts.setdefault(date_key, []).append(post)

    return render_template("calendar.html", grouped_posts=grouped_posts)


@app.route("/content-pack/create-carousel", methods=["POST"])
@login_required
def create_content_pack_carousel():
    content_pack_result = request.form.get("content_pack_result", "").strip()
    image_style = request.form.get("image_style", "").strip()

    if not content_pack_result:
        flash("No content pack found.", "danger")
        return redirect(url_for("content_pack"))

    caption = extract_content_pack_section(content_pack_result, "INSTAGRAM_CAPTION")
    carousel_idea = extract_content_pack_section(content_pack_result, "CAROUSEL_IDEA")
    image_prompt = extract_content_pack_section(content_pack_result, "IMAGE_PROMPT")
    hashtags = extract_content_pack_section(content_pack_result, "HASHTAGS")

    if hashtags:
        caption = caption + "\n\n" + hashtags

    if not carousel_idea:
        flash("No carousel idea found in the content pack.", "danger")
        return redirect(url_for("content_pack"))

    try:
        styled_image_prompt = apply_image_style(image_prompt, image_style)
        slides = []

        for line in carousel_idea.splitlines():
            line = line.strip()

            if line.lower().startswith("slide"):
                parts = line.split(":", 1)

                if len(parts) == 2 and parts[1].strip():
                    slides.append(parts[1].strip())

        if not slides:
            slides = [
                line.strip() for line in carousel_idea.splitlines() if line.strip()
            ]

        slides = slides[:6]

        if len(slides) < 2:
            flash("Carousel needs at least 2 slides.", "danger")
            return redirect(url_for("content_pack"))

        group_id = str(uuid.uuid4())
        placeholder_url = get_placeholder_image_url()

        for index, slide_text in enumerate(slides):
            full_prompt = f"""
Create an Instagram carousel slide.

Slide content:
{slide_text}

Visual direction:
{styled_image_prompt}

Design:
- dark background
- bold typography
- high contrast
- square 1:1 format
- premium social media style
"""

            post = Post(
                file_url=placeholder_url,
                file_type="image",
                prompt=full_prompt,
                caption=caption,
                platforms="instagram",
                post_type="carousel",
                status="generating",
                group_id=group_id,
                sort_order=index,
                is_cover=(index == 0),
                user_id=current_user.id,
            )

            db.session.add(post)

        db.session.commit()

        first_post = (
            Post.query.filter_by(group_id=group_id, user_id=current_user.id)
            .order_by(Post.sort_order.asc())
            .first()
        )

        flash(
            "Carousel draft created. Images are generating in the background.",
            "success",
        )
        return redirect(url_for("view_post", post_id=first_post.id))

    except Exception as e:
        print("Create content pack carousel error:", e)
        flash(f"Failed to create content pack carousel: {e}", "danger")
        return redirect(url_for("content_pack"))


@app.route("/content-pack/create-platform-draft", methods=["POST"])
@login_required
def create_content_pack_platform_draft():
    content_pack_result = request.form.get("content_pack_result", "").strip()
    platform = request.form.get("platform", "").strip()
    image_style = request.form.get("image_style", "").strip()

    allowed_platforms = [
        "instagram",
        "facebook",
        "linkedin",
        "pinterest",
        "reddit",
        "x",
    ]

    if not content_pack_result:
        flash("No content pack found.", "danger")
        return redirect(url_for("content_pack"))

    if platform not in allowed_platforms:
        flash("Invalid platform selected.", "danger")
        return redirect(url_for("content_pack"))

    image_prompt = extract_content_pack_section(content_pack_result, "IMAGE_PROMPT")
    hashtags = extract_content_pack_section(content_pack_result, "HASHTAGS")

    if platform == "instagram":
        caption = extract_content_pack_section(content_pack_result, "INSTAGRAM_CAPTION")

        if hashtags:
            caption = caption + "\n\n" + hashtags

    elif platform == "facebook":
        caption = extract_content_pack_section(content_pack_result, "FACEBOOK_POST")

    elif platform == "linkedin":
        caption = extract_content_pack_section(content_pack_result, "LINKEDIN_POST")

    elif platform == "pinterest":
        title = extract_content_pack_section(content_pack_result, "PINTEREST_PIN_TITLE")
        description = extract_content_pack_section(
            content_pack_result,
            "PINTEREST_PIN_DESCRIPTION"
        )
        caption = f"{title}\n\n{description}".strip()

    elif platform == "reddit":
        caption = extract_content_pack_section(content_pack_result, "REDDIT_POST")

    elif platform == "x":
        caption = extract_content_pack_section(content_pack_result, "X_POST")

    if not caption:
        flash(f"No {platform} content found in the content pack.", "danger")
        return redirect(url_for("content_pack"))

    try:
        brand_context = build_brand_context(current_user.id)

        enhanced_prompt = f"""
Brand Brief:
{brand_context}

Create a social media image for this {platform} post.

Post Caption:
{caption}

Extra Visual Direction:
{image_prompt}

Requirements:
- Match the meaning and mood of the post
- Avoid random unrelated objects
- Avoid generic stock image style
- Square 1:1 format
- High quality
- Suitable for {platform}
"""

        styled_prompt = apply_image_style(enhanced_prompt, image_style)
        placeholder_url = get_placeholder_image_url()

        post = Post(
            file_url=placeholder_url,
            file_type="image",
            prompt=styled_prompt,
            caption=caption,
            platforms=platform,
            post_type="single",
            status="generating",
            sort_order=0,
            is_cover=False,
            user_id=current_user.id,
        )

        db.session.add(post)
        db.session.commit()

        flash(
            f"{platform.title()} draft created. Image is generating in the background.",
            "success",
        )
        return redirect(url_for("view_post", post_id=post.id))

    except Exception as e:
        print("Create platform draft error:", e)
        flash(f"Failed to create {platform} draft: {e}", "danger")
        return redirect(url_for("content_pack"))


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


@app.route("/post/<int:post_id>/improve", methods=["POST"])
@login_required
def improve_post(post_id):
    post = Post.query.filter_by(
        id=post_id,
        user_id=current_user.id
    ).first_or_404()

    try:
        brand_context = build_brand_context(current_user.id)
        improved_caption = improve_post_with_ai(post, brand_context)

        post.improved_caption = improved_caption
        post.improved_at = datetime.utcnow()
        update_brand_coach(post, brand_context)

        db.session.commit()

        flash("Improved caption created successfully.", "success")

    except Exception as e:
        print("Improve post error:", e)
        flash(f"Failed to improve post: {e}", "danger")

    return redirect(url_for("view_post", post_id=post.id))
    


def grade_post_with_ai(post, brand_context=""):
    prompt = f"""
You are an expert social media strategist and content reviewer.

Brand Brief:
{brand_context}

Post Caption:
{post.caption or ""}

Platform:
{post.platforms or ""}

Post Type:
{post.post_type or "single"}

Score the post out of 10 for:
1. Hook
2. Clarity
3. Engagement
4. Call To Action
5. Platform Fit
6. Brand Fit

Return exactly:

HOOK_SCORE: X/10
CLARITY_SCORE: X/10
ENGAGEMENT_SCORE: X/10
CTA_SCORE: X/10
PLATFORM_FIT_SCORE: X/10
BRAND_FIT_SCORE: X/10
OVERALL_SCORE: X/10

STRENGTHS:
- ...

IMPROVEMENTS:
- ...
"""

    response = openai_client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
    )

    return response.output_text.strip()


def extract_overall_score(grade_result):
    match = re.search(r"OVERALL_SCORE:\s*([0-9]+(?:\.[0-9]+)?)\/10", grade_result or "")
    return float(match.group(1)) if match else None


@app.route("/post/<int:post_id>/grade", methods=["POST"])
@login_required
def grade_post(post_id):
    post = Post.query.filter_by(
        id=post_id,
        user_id=current_user.id
    ).first_or_404()

    try:
        brand_context = build_brand_context(current_user.id)
        grade_result = grade_post_with_ai(post, brand_context)
        overall_score = extract_overall_score(grade_result)

        post.grade_result = grade_result
        post.grade_score = overall_score
        post.graded_at = datetime.utcnow()

        db.session.commit()

        flash("Post graded successfully.", "success")

    except Exception as e:
        print("Post grading error:", e)
        flash(f"Failed to grade post: {e}", "danger")

    return redirect(url_for("view_post", post_id=post.id))


@app.route("/post/<int:post_id>/use-improved", methods=["POST"])
@login_required
def use_improved_caption(post_id):
    post = Post.query.filter_by(
        id=post_id,
        user_id=current_user.id
    ).first_or_404()

    if not post.improved_caption:
        flash("No improved caption found.", "warning")
        return redirect(url_for("view_post", post_id=post.id))

    save_post_revision(post, source="before_ai_improved")

    post.caption = post.improved_caption
    post.improved_caption = None
    post.improved_at = None
    update_brand_coach(post, brand_context)

    db.session.commit()

    flash("Improved caption is now the main caption.", "success")
    return redirect(url_for("view_post", post_id=post.id))


@app.route("/post/<int:post_id>/custom-caption", methods=["POST"])
@login_required
def use_custom_caption(post_id):
    post = Post.query.filter_by(
        id=post_id,
        user_id=current_user.id
    ).first_or_404()

    custom_caption = request.form.get("custom_caption", "").strip()

    if not custom_caption:
        flash("Custom caption cannot be empty.", "danger")
        return redirect(url_for("view_post", post_id=post.id))

    save_post_revision(post, source="before_custom_caption")

    post.caption = custom_caption
    post.improved_caption = None
    post.improved_at = None

    db.session.commit()

    flash("Custom caption saved as the main caption.", "success")
    return redirect(url_for("view_post", post_id=post.id))


@app.route("/post/<int:post_id>/discard-improved", methods=["POST"])
@login_required
def discard_improved_caption(post_id):
    post = Post.query.filter_by(
        id=post_id,
        user_id=current_user.id
    ).first_or_404()

    post.improved_caption = None
    post.improved_at = None

    db.session.commit()

    flash("Improved caption discarded.", "success")
    return redirect(url_for("view_post", post_id=post.id))


@app.route("/post/<int:post_id>/ai-editor", methods=["GET", "POST"])
@login_required
def ai_editor(post_id):
    post = Post.query.filter_by(
        id=post_id,
        user_id=current_user.id
    ).first_or_404()

    if request.method == "POST":
        final_caption = request.form.get("final_caption", "").strip()

        if not final_caption:
            flash("Final caption cannot be empty.", "danger")
            return redirect(url_for("ai_editor", post_id=post.id))

        save_post_revision(post, source="before_ai_editor")

        post.caption = final_caption
        post.improved_caption = None
        post.improved_at = None
        update_brand_coach(post, brand_context)

        db.session.commit()

        flash("Final caption saved successfully.", "success")
        return redirect(url_for("view_post", post_id=post.id))

    return render_template("ai_editor.html", post=post)


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

    final_caption = request.form.get("final_caption", "").strip()

    if final_caption:
        post.caption = final_caption

    try:
        brand_context = build_brand_context(current_user.id)
        grade_result = grade_post_with_ai(post, brand_context)
        overall_score = extract_overall_score(grade_result)

        post.grade_result = grade_result
        post.grade_score = overall_score
        post.graded_at = datetime.utcnow
        update_brand_coach(post, brand_context)
        

        db.session.commit()

        flash("Studio caption regraded successfully.", "success")

    except Exception as e:
        print("Studio regrade error:", e)
        flash(f"Failed to regrade caption: {e}", "danger")

    return redirect(url_for("post_studio", post_id=post.id))


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not email or not password:
            flash("Please enter an email and password.", "danger")
            return redirect(url_for("register"))

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return redirect(url_for("register"))

        existing_user = User.query.filter_by(email=email).first()

        if existing_user:
            flash("An account with that email already exists.", "danger")
            return redirect(url_for("register"))

        user = User(
            email=email,
            password_hash=generate_password_hash(password)
        )

        db.session.add(user)
        db.session.commit()

        login_user(user)

        flash("Account created successfully.", "success")
        return redirect(url_for("index"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        user = User.query.filter_by(email=email).first()

        if not user or not check_password_hash(user.password_hash, password):
            flash("Invalid email or password.", "danger")
            return redirect(url_for("login"))

        login_user(user)

        flash("Logged in successfully.", "success")
        return redirect(url_for("index"))

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out successfully.", "success")
    return redirect(url_for("login"))




print("Starting background scheduler...")

scheduler = BackgroundScheduler()
scheduler.add_job(
    generate_pending_carousel_images,
    "interval",
    seconds=20,
    max_instances=1
)
scheduler.start()

print("Background scheduler started.")


@app.route("/settings/accounts", methods=["GET", "POST"])
@login_required
def connected_accounts():
    accounts = ConnectedAccount.query.filter_by(user_id=current_user.id).first()

    if not accounts:
        accounts = ConnectedAccount(user_id=current_user.id)
        db.session.add(accounts)
        db.session.commit()

    if request.method == "POST":
        accounts.instagram_connected = request.form.get("instagram_connected") == "on"
        accounts.facebook_connected = request.form.get("facebook_connected") == "on"
        accounts.linkedin_connected = request.form.get("linkedin_connected") == "on"
        accounts.pinterest_connected = request.form.get("pinterest_connected") == "on"
        accounts.reddit_connected = request.form.get("reddit_connected") == "on"
        accounts.x_connected = request.form.get("x_connected") == "on"

        accounts.make_webhook_single = request.form.get("make_webhook_single", "").strip()
        accounts.make_webhook_carousel = request.form.get("make_webhook_carousel", "").strip()

        db.session.commit()

        flash("Connected accounts updated.", "success")
        return redirect(url_for("connected_accounts"))

    enabled_count = sum([
        bool(accounts.instagram_connected),
        bool(accounts.facebook_connected),
        bool(accounts.linkedin_connected),
        bool(accounts.pinterest_connected),
        bool(accounts.reddit_connected),
        bool(accounts.x_connected),
    ])

    webhooks_ready = (
        bool(accounts.make_webhook_single),
        bool(accounts.make_webhook_carousel),
    )

    return render_template(
        "connected_accounts.html",
        accounts=accounts,
        enabled_count=enabled_count,
        webhooks_ready=webhooks_ready,
    )






if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
