import os
import time
import json
import uuid
from datetime import datetime

import pytz
import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler

import cloudinary
import cloudinary.uploader


load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(BASE_DIR, 'posts.db')}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

MAKE_WEBHOOK_SINGLE = os.getenv("MAKE_WEBHOOK_SINGLE", "").strip()
MAKE_WEBHOOK_CAROUSEL = os.getenv("MAKE_WEBHOOK_CAROUSEL", "").strip()
KIE_API_KEY = os.getenv("KIE_API_KEY", "").strip()

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


def create_kie_image_task(prompt: str) -> str:
    url = "https://api.kie.ai/api/v1/jobs/createTask"

    headers = {
        "Authorization": f"Bearer {KIE_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "nano-banana-pro",
        "input": {
            "prompt": prompt
        },
    }

    response = requests.post(url, headers=headers, json=payload, timeout=60)
    data = response.json()

    if data.get("code") != 200:
        raise Exception(f"KIE Error: {data}")

    return data["data"]["taskId"]


def poll_kie_image_result(task_id):
    url = "https://api.kie.ai/api/v1/jobs/recordInfo"

    headers = {
        "Authorization": f"Bearer {KIE_API_KEY}",
    }

    while True:
        response = requests.get(
            url,
            headers=headers,
            params={"taskId": task_id},
            timeout=60
        )

        data = response.json()

        if data.get("code") != 200:
            raise Exception(f"KIE polling error: {data}")

        state = data["data"]["state"]

        if state == "success":
            result_json = json.loads(data["data"]["resultJson"])
            return result_json["resultUrls"][0]

        if state == "fail":
            raise Exception(f"Image generation failed: {data}")

        time.sleep(2)


def generate_multiple_images(prompt, count=3):
    images = []

    for _ in range(count):
        task_id = create_kie_image_task(prompt)
        image_url = poll_kie_image_result(task_id)
        images.append(image_url)

    return images


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


@app.route("/")
def index():
    posts = Post.query.order_by(
        Post.created_at.desc(),
        Post.is_cover.desc(),
        Post.sort_order.asc(),
        Post.id.asc()
    ).all()

    return render_template("index.html", posts=posts)


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

        print("FILES RECEIVED:", len(files))
        print("FILE NAMES:", [file.filename for file in files])
        print("MAKE CAROUSEL:", make_carousel)
        print("CAROUSEL ORDER RAW:", carousel_order_raw)
        print("COVER INDEX RAW:", cover_index_raw)

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

                print("ORDERED FILES:", [file.filename for _, file in ordered_items])

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
                image_urls = generate_multiple_images(prompt, image_count)

                group_id = str(uuid.uuid4()) if make_carousel else None
                created_posts = []

                for index, image_url in enumerate(image_urls):
                    upload_result = upload_to_cloudinary(image_url)

                    post = Post(
                        file_url=upload_result["secure_url"],
                        file_type="image",
                        prompt=prompt,
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
        group_id = post.group_id
        group_posts = Post.query.filter_by(group_id=group_id).all()

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


if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)