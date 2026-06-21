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

db = SQLAlchemy(app)
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

    posts = db.relationship("Post", backref="user", lazy=True)


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


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


with app.app_context():
    db.create_all()

    inspector = db.inspect(db.engine)

    # Create brand_brief table if missing
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

        # keep the rest of your existing column checks here...

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


def send_payload_to_make(payload):
    if payload.get("post_type") == "carousel":
        webhook_url = MAKE_WEBHOOK_CAROUSEL
        webhook_name = "CAROUSEL"
    else:
        webhook_url = MAKE_WEBHOOK_SINGLE
        webhook_name = "SINGLE"

    if not webhook_url:
        raise Exception(f"{webhook_name} webhook URL is missing from your .env file")

    print(f"Sending to {webhook_name} webhook:")
    print(json.dumps(payload, indent=2))

    response = requests.post(webhook_url, json=payload, timeout=30)

    print("Make status:", response.status_code)
    print("Make response:", response.text)

    if response.status_code >= 400:
        raise Exception(f"Make.com error {response.status_code}: {response.text}")

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

            send_payload_to_make(payload)

            group_posts = get_ordered_carousel_posts(post.group_id)

            for group_post in group_posts:
                group_post.status = "sent_to_make"
                group_post.sent_at = datetime.utcnow()

            db.session.commit()

            flash("Carousel sent to Make.com successfully.", "success")
            return redirect(url_for("index"))

        payload = build_single_payload(post)
        send_payload_to_make(payload)

        post.status = "sent_to_make"
        post.sent_at = datetime.utcnow()

        db.session.commit()

        flash("Sent to Make.com successfully.", "success")

    except Exception as e:
        print("Send to Make error:", e)
        flash(f"Failed: {e}", "danger")

    return redirect(url_for("view_post", post_id=post.id))


@app.route("/send-carousel/<group_id>", methods=["POST"])
@login_required
def send_carousel_to_make(group_id):
    posts = get_ordered_carousel_posts(group_id)

    if not posts:
        flash("Carousel not found.", "danger")
        return redirect(url_for("index"))

    try:
        payload = build_carousel_payload(group_id)

        if not payload:
            flash("Carousel payload could not be built.", "danger")
            return redirect(url_for("index"))

        send_payload_to_make(payload)

        for post in posts:
            post.status = "sent_to_make"
            post.sent_at = datetime.utcnow()

        db.session.commit()

        flash("Carousel sent to Make.com successfully.", "success")

    except Exception as e:
        print("Send carousel error:", e)
        flash(f"Failed: {e}", "danger")

    return redirect(url_for("index"))


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

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
