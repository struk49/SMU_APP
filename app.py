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
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(BASE_DIR, 'posts.db')}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

MAKE_WEBHOOK_SINGLE = os.getenv("MAKE_WEBHOOK_SINGLE", "").strip()
MAKE_WEBHOOK_CAROUSEL = os.getenv("MAKE_WEBHOOK_CAROUSEL", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

openai_client = OpenAI(api_key=OPENAI_API_KEY)

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
)


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


with app.app_context():
    db.create_all()

    inspector = db.inspect(db.engine)
    columns = [col["name"] for col in inspector.get_columns("post")]

    with db.engine.connect() as conn:
        if "group_id" not in columns:
            conn.execute(db.text("ALTER TABLE post ADD COLUMN group_id VARCHAR(100)"))

        if "post_type" not in columns:
            conn.execute(db.text("ALTER TABLE post ADD COLUMN post_type VARCHAR(50) DEFAULT 'single'"))

        if "platforms" not in columns:
            conn.execute(db.text("ALTER TABLE post ADD COLUMN platforms VARCHAR(200) DEFAULT 'instagram,facebook'"))

        if "scheduled_time" not in columns:
            conn.execute(db.text("ALTER TABLE post ADD COLUMN scheduled_time DATETIME"))

        if "sort_order" not in columns:
            conn.execute(db.text("ALTER TABLE post ADD COLUMN sort_order INTEGER DEFAULT 0"))

        if "is_cover" not in columns:
            conn.execute(db.text("ALTER TABLE post ADD COLUMN is_cover BOOLEAN DEFAULT 0"))

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
        platform.strip()
        for platform in platforms_string.split(",")
        if platform.strip()
    ]


def convert_uk_time_to_utc(scheduled_time_str):
    uk = pytz.timezone("Europe/London")
    local_time = datetime.fromisoformat(scheduled_time_str)
    local_time = uk.localize(local_time)
    utc_time = local_time.astimezone(pytz.utc)
    return utc_time.replace(tzinfo=None)


def upload_to_cloudinary(file_or_url):
    return cloudinary.uploader.upload(
        file_or_url,
        folder="social_posts",
        resource_type="auto"
    )


def make_instagram_safe_url(url):
    return url.replace(
        "/upload/",
        "/upload/c_fill,w_1080,h_1080,q_auto,f_jpg/"
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
        rewrite_type,
        "Improve this social media caption while keeping the meaning."
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
        output_format="jpeg"
    )

    image_base64 = result.data[0].b64_json
    image_bytes = base64.b64decode(image_base64)

    upload_result = cloudinary.uploader.upload(
        image_bytes,
        folder="social_posts",
        resource_type="image"
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
    return Post.query.filter_by(group_id=group_id).order_by(
        Post.is_cover.desc(),
        Post.sort_order.asc(),
        Post.id.asc()
    ).all()


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


def repurpose_tiktok_content(transcript):
    if not OPENAI_API_KEY:
        raise Exception("OPENAI_API_KEY is missing from your .env file")

    prompt = f"""
You are a social media content repurposing assistant.

Turn this TikTok transcript into content for Instagram and Facebook.

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

        posts = Post.query.filter(
            Post.scheduled_time != None,
            Post.status == "scheduled",
            Post.scheduled_time <= now
        ).order_by(Post.sort_order.asc(), Post.id.asc()).all()

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


scheduler = BackgroundScheduler()
scheduler.add_job(func=check_scheduled_posts, trigger="interval", seconds=30)
scheduler.start()


def generate_content_pack(source_text):
    if not OPENAI_API_KEY:
        raise Exception("OPENAI_API_KEY is missing from your .env file")

    prompt = f"""
You are a social media content repurposing assistant.

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


@app.route("/")
def index():
    status_filter = request.args.get("status", "all")
    type_filter = request.args.get("type", "all")
    search_query = request.args.get("q", "").strip()

    query = Post.query

    if status_filter != "all":
        query = query.filter(Post.status == status_filter)

    if type_filter != "all":
        query = query.filter(Post.post_type == type_filter)

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
        Post.id.asc()
    ).all()

    stats = {
        "total": Post.query.count(),
        "drafts": Post.query.filter_by(status="draft").count(),
        "scheduled": Post.query.filter_by(status="scheduled").count(),
        "sent": Post.query.filter_by(status="sent_to_make").count(),
        "carousels": Post.query.filter_by(post_type="carousel").count(),
    }

    return render_template(
        "index.html",
        posts=posts,
        status_filter=status_filter,
        type_filter=type_filter,
        search_query=search_query,
        stats=stats
    )


@app.route("/create", methods=["GET", "POST"])
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
                styled_prompt = apply_image_style(prompt, image_style)
                image_urls = generate_multiple_openai_images(styled_prompt, image_count)

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
                        is_cover=(index == 0)
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
                    flash("Instagram carousel posts can only contain up to 10 images.", "danger")
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
                        is_cover=(is_carousel and index == 0)
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
            print("ERROR:", e)
            flash(f"Failed: {e}", "danger")
            return redirect(url_for("create_post"))

    return render_template("create_post.html")


@app.route("/post/<int:post_id>")
def view_post(post_id):
    post = Post.query.get_or_404(post_id)

    carousel_posts = []

    if post.group_id:
        carousel_posts = get_ordered_carousel_posts(post.group_id)

    return render_template(
        "view_post.html",
        post=post,
        carousel_posts=carousel_posts
    )


@app.route("/edit-post/<int:post_id>", methods=["GET", "POST"])
def edit_post(post_id):
    post = Post.query.get_or_404(post_id)

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
def rewrite_caption(post_id):
    post = Post.query.get_or_404(post_id)

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
def duplicate_post(post_id):
    original = Post.query.get_or_404(post_id)

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
            sent_at=None
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
def duplicate_carousel(group_id):
    original_posts = Post.query.filter_by(
        group_id=group_id
    ).order_by(
        Post.sort_order.asc()
    ).all()

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
                sent_at=None
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
def send_to_make(post_id):
    post = Post.query.get_or_404(post_id)

    try:
        if post.post_type == "carousel" and post.group_id:
            payload = build_carousel_payload(post.group_id)

            if not payload:
                flash("Carousel payload could not be built.", "danger")
                return redirect(url_for("index"))

            send_payload_to_make(payload)

            group_posts = Post.query.filter_by(group_id=post.group_id).all()

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
        flash(f"Failed: {e}", "danger")

    return redirect(url_for("view_post", post_id=post.id))


@app.route("/send-carousel/<group_id>", methods=["POST"])
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
        flash(f"Failed: {e}", "danger")

    return redirect(url_for("index"))


@app.route("/edit-carousel/<group_id>", methods=["GET", "POST"])
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
def schedule_post(post_id):
    post = Post.query.get_or_404(post_id)

    scheduled_time_str = request.form.get("scheduled_time")

    if not scheduled_time_str:
        flash("Please select a date and time.", "danger")
        return redirect(url_for("view_post", post_id=post.id))

    try:
        scheduled_time = convert_uk_time_to_utc(scheduled_time_str)

        if post.post_type == "carousel" and post.group_id:
            group_posts = Post.query.filter_by(group_id=post.group_id).all()

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
        flash(f"Error scheduling post: {e}", "danger")

    return redirect(url_for("view_post", post_id=post.id))


@app.route("/delete/<int:post_id>", methods=["POST"])
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)

    if post.post_type == "carousel" and post.group_id:
        group_posts = Post.query.filter_by(group_id=post.group_id).all()

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
def delete_carousel(group_id):
    posts = Post.query.filter_by(group_id=group_id).all()

    for post in posts:
        db.session.delete(post)

    db.session.commit()

    flash("Carousel deleted.", "warning")
    return redirect(url_for("index"))


@app.route("/tiktok", methods=["GET", "POST"])
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
            generated_content = repurpose_tiktok_content(transcript)

        except Exception as e:
            print("TikTok repurpose error:", e)
            flash(f"Failed: {e}", "danger")

    return render_template(
        "tiktok.html",
        tiktok_url=tiktok_url,
        transcript=transcript,
        generated_content=generated_content
    )


@app.route("/tiktok/create-draft", methods=["POST"])
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
            is_cover=False
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
                line.strip()
                for line in carousel_idea.splitlines()
                if line.strip()
            ]

        slides = slides[:6]

        if len(slides) < 2:
            flash("Carousel needs at least 2 slides.", "danger")
            return redirect(url_for("tiktok_repurpose"))

        group_id = str(uuid.uuid4())

        for index, slide_text in enumerate(slides):
            if index == 0:
                full_prompt = f"""
Create a HIGH-CONVERTING viral Instagram carousel COVER slide.

Main headline:
{slide_text}

Visual inspiration:
Bold creator-business Instagram carousel style.

Design style:
- black or dark background
- premium modern marketing design
- BIG bold typography
- yellow accent blocks
- white headline text
- bright green and blue highlight colours
- infographic style
- visually striking composition
- premium entrepreneur aesthetic
- clean but attention-grabbing
- high contrast
- square Instagram format (1:1)
- social media viral style

Layout:
- very large headline
- clear visual hierarchy
- room for supporting subheading
- premium polished look
- designed for high engagement

Use this visual direction:
{styled_image_prompt}

Avoid:
- clutter
- tiny text
- unreadable typography
- bad spacing
- generic stock image look
- distorted text
"""
            else:
                full_prompt = f"""
Create a HIGH-CONVERTING Instagram carousel educational slide.

Slide content:
{slide_text}

Visual inspiration:
Modern viral entrepreneur Instagram carousel.

Design style:
- dark background
- bold premium typography
- yellow highlight boxes
- white main text
- green accent colours
- infographic feel
- modern creator-business aesthetic
- highly readable
- visually engaging
- square Instagram format (1:1)

Layout:
- one clear takeaway
- visually balanced composition
- large readable text
- simple icon or visual support
- premium content creator feel

Keep visual consistency with previous slides.

Use this visual direction:
{styled_image_prompt}

Avoid:
- clutter
- tiny unreadable text
- distorted text
- generic image style
"""

            image_url = generate_openai_image(full_prompt)

            post = Post(
                file_url=image_url,
                file_type="image",
                prompt=full_prompt,
                caption=caption,
                platforms="instagram,facebook",
                post_type="carousel",
                status="draft",
                group_id=group_id,
                sort_order=index,
                is_cover=(index == 0)
            )

            db.session.add(post)

        db.session.commit()

        first_post = Post.query.filter_by(group_id=group_id).order_by(
            Post.sort_order.asc()
        ).first()

        flash("TikTok carousel draft created successfully.", "success")
        return redirect(url_for("view_post", post_id=first_post.id))

    except Exception as e:
        print("Create TikTok carousel draft error:", e)
        flash(f"Failed to create carousel draft: {e}", "danger")
        return redirect(url_for("tiktok_repurpose"))


@app.route("/calendar")
def calendar_view():
    scheduled_posts = Post.query.filter(
        Post.scheduled_time != None,
        Post.status == "scheduled"
    ).order_by(
        Post.scheduled_time.asc(),
        Post.created_at.asc()
    ).all()

    grouped_posts = {}

    for post in scheduled_posts:
        date_key = post.scheduled_time.strftime("%A %d %B %Y")

        if date_key not in grouped_posts:
            grouped_posts[date_key] = []

        grouped_posts[date_key].append(post)

    return render_template(
        "calendar.html",
        grouped_posts=grouped_posts
    )


@app.route("/content-pack", methods=["GET", "POST"])
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

            content_pack_result = generate_content_pack(source_text)

        except Exception as e:
            print("Content pack error:", e)
            flash(f"Failed: {e}", "danger")

    return render_template(
        "content_pack.html",
        source_text=source_text,
        content_pack_result=content_pack_result
    )

@app.route("/content-pack/create-carousel", methods=["POST"])
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
                line.strip()
                for line in carousel_idea.splitlines()
                if line.strip()
            ]

      slides = slides[:3]

        if len(slides) < 2:
            flash("Carousel needs at least 2 slides.", "danger")
            return redirect(url_for("content_pack"))

        group_id = str(uuid.uuid4())

        for index, slide_text in enumerate(slides):
            if index == 0:
                full_prompt = f"""
Create a HIGH-CONVERTING viral Instagram carousel COVER slide.

Main headline:
{slide_text}

Visual inspiration:
Bold creator-business Instagram carousel style.

Design style:
- black or dark background
- premium modern marketing design
- BIG bold typography
- yellow accent blocks
- white headline text
- bright green and blue highlight colours
- infographic style
- visually striking composition
- premium entrepreneur aesthetic
- clean but attention-grabbing
- high contrast
- square Instagram format 1:1
- social media viral style

Use this visual direction:
{styled_image_prompt}

Avoid:
- clutter
- tiny text
- distorted text
- bad spacing
- generic stock image look
"""
            else:
                full_prompt = f"""
Create a HIGH-CONVERTING Instagram carousel educational slide.

Slide content:
{slide_text}

Visual inspiration:
Modern viral entrepreneur Instagram carousel.

Design style:
- dark background
- bold premium typography
- yellow highlight boxes
- white main text
- green accent colours
- infographic feel
- modern creator-business aesthetic
- highly readable
- square Instagram format 1:1

Keep visual consistency with previous slides.

Use this visual direction:
{styled_image_prompt}

Avoid:
- clutter
- tiny unreadable text
- distorted text
- generic image style
"""

            image_url = generate_openai_image(full_prompt)

            post = Post(
                file_url=image_url,
                file_type="image",
                prompt=full_prompt,
                caption=caption,
                platforms="instagram,facebook",
                post_type="carousel",
                status="draft",
                group_id=group_id,
                sort_order=index,
                is_cover=(index == 0)
            )

            db.session.add(post)

        db.session.commit()

        first_post = Post.query.filter_by(group_id=group_id).order_by(
            Post.sort_order.asc()
        ).first()

        flash("Content pack carousel draft created successfully.", "success")
        return redirect(url_for("view_post", post_id=first_post.id))

    except Exception as e:
        print("Create content pack carousel error:", e)
        flash(f"Failed to create content pack carousel: {e}", "danger")
        return redirect(url_for("content_pack"))


if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)