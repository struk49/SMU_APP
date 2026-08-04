import json
import uuid
from datetime import datetime

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from smu_core.extensions import db
from smu_core.models import Post


posts_bp = Blueprint("posts", __name__)


def _post_detail_helper(name):
    helpers = current_app.extensions.get("smu_post_detail_helpers", {})
    helper = helpers.get(name)

    if not helper:
        raise RuntimeError(f"Post detail helper is not available: {name}")

    return helper


def _post_edit_helper(name):
    helpers = current_app.extensions.get("smu_post_edit_helpers", {})
    helper = helpers.get(name)

    if not helper:
        raise RuntimeError(f"Post edit helper is not available: {name}")

    return helper


def _post_delete_duplicate_helper(name):
    helpers = current_app.extensions.get("smu_post_delete_duplicate_helpers", {})
    helper = helpers.get(name)

    if not helper:
        raise RuntimeError(f"Post delete/duplicate helper is not available: {name}")

    return helper


def _post_create_helper(name):
    helpers = current_app.extensions.get("smu_post_create_helpers", {})
    helper = helpers.get(name)

    if not helper:
        raise RuntimeError(f"Post create helper is not available: {name}")

    return helper


def _post_schedule_helper(name):
    helpers = current_app.extensions.get("smu_post_schedule_helpers", {})
    helper = helpers.get(name)

    if not helper:
        raise RuntimeError(f"Post schedule helper is not available: {name}")

    return helper


@login_required
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
            scheduled_time = _post_create_helper("convert_uk_time_to_utc")(
                scheduled_time_str
            )

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

                brand_context = _post_create_helper("build_brand_context")(
                    current_user.id
                )

                branded_prompt = f"""
{brand_context}

Create a branded social media image.

User Request:
{prompt}
"""

                styled_prompt = _post_create_helper("apply_image_style")(
                    branded_prompt,
                    image_style
                )

                image_urls = _post_create_helper("generate_multiple_openai_images")(
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
                    file_type = _post_create_helper("get_file_type")(file.filename)

                    if make_carousel and file_type != "image":
                        raise Exception("Carousel posts currently support images only.")

                    upload_result = _post_create_helper("upload_to_cloudinary")(
                        file,
                        force_jpeg=(
                            file_type == "image"
                            and _post_create_helper("is_instagram_selected")(platforms)
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


@login_required
def schedule_post(post_id):
    post = Post.query.filter_by(
        id=post_id,
        user_id=current_user.id
    ).first_or_404()

    scheduled_time_str = request.form.get(
        "scheduled_time",
        ""
    ).strip()

    if not scheduled_time_str:
        flash("Please select a date and time.", "danger")
        return redirect(
            url_for("view_post", post_id=post.id)
        )

    try:
        scheduled_time = _post_schedule_helper("convert_uk_time_to_utc")(
            scheduled_time_str
        )

        if post.group_id:
            group_posts = _post_schedule_helper("get_ordered_carousel_posts")(
                post.group_id,
                user_id=current_user.id,
            )

            if not group_posts:
                flash(
                    "No carousel posts were found.",
                    "danger"
                )
                return redirect(
                    url_for("view_post", post_id=post.id)
                )

            for group_post in group_posts:
                group_post.scheduled_time = scheduled_time
                group_post.status = "scheduled"

            db.session.commit()

            for group_post in group_posts:
                _post_schedule_helper("log_scheduled_post_diagnostics")(
                    group_post,
                    input_local_time=scheduled_time_str,
                )

            flash(
                "Carousel scheduled successfully.",
                "success"
            )

            return redirect(
                url_for("view_post", post_id=post.id)
            )

        post.scheduled_time = scheduled_time
        post.status = "scheduled"

        db.session.commit()

        _post_schedule_helper("log_scheduled_post_diagnostics")(
            post,
            input_local_time=scheduled_time_str,
        )

        flash(
            "Post scheduled successfully.",
            "success"
        )

    except Exception as e:
        db.session.rollback()
        print("Schedule error:", e)
        flash(
            f"Error scheduling post: {e}",
            "danger"
        )

    return redirect(
        url_for("view_post", post_id=post.id)
    )


@login_required
def view_post(post_id):
    post = Post.query.get_or_404(post_id)

    if post.user_id != current_user.id:
        flash("You do not have access to this post.", "danger")
        return redirect(url_for("index"))

    carousel_posts = []

    if post.group_id:
        carousel_posts = _post_detail_helper("get_ordered_carousel_posts")(
            post.group_id,
            user_id=current_user.id,
        )

    return render_template("view_post.html", post=post, carousel_posts=carousel_posts)


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

                image_url = _post_edit_helper("generate_openai_image")(prompt)
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


@login_required
def edit_carousel(group_id):
    posts = _post_edit_helper("get_ordered_carousel_posts")(
        group_id,
        user_id=current_user.id,
    )

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


@login_required
def duplicate_carousel(group_id):
    original_posts = _post_delete_duplicate_helper("get_ordered_carousel_posts")(
        group_id,
        user_id=current_user.id,
    )

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


@login_required
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)

    if post.user_id != current_user.id:
        flash("You do not have access to this post.", "danger")
        return redirect(url_for("index"))

    if post.group_id:
        group_posts = _post_delete_duplicate_helper("get_ordered_carousel_posts")(
            post.group_id,
            user_id=current_user.id,
        )

        for group_post in group_posts:
            db.session.delete(group_post)

        db.session.commit()

        flash("Carousel deleted.", "warning")
        return redirect(url_for("index"))

    db.session.delete(post)
    db.session.commit()

    flash("Post deleted.", "warning")
    return redirect(url_for("index"))


@login_required
def delete_carousel(group_id):
    posts = _post_delete_duplicate_helper("get_ordered_carousel_posts")(
        group_id,
        user_id=current_user.id,
    )

    if not posts:
        flash("Carousel not found.", "danger")
        return redirect(url_for("index"))

    for post in posts:
        db.session.delete(post)

    db.session.commit()

    flash("Carousel deleted.", "warning")
    return redirect(url_for("index"))


@posts_bp.record_once
def register_routes(state):
    state.app.add_url_rule(
        "/create",
        "create_post",
        create_post,
        methods=["GET", "POST"],
    )
    state.app.add_url_rule(
        "/schedule/<int:post_id>",
        "schedule_post",
        schedule_post,
        methods=["POST"],
    )
    state.app.add_url_rule("/post/<int:post_id>", "view_post", view_post)
    state.app.add_url_rule(
        "/edit-post/<int:post_id>",
        "edit_post",
        edit_post,
        methods=["GET", "POST"],
    )
    state.app.add_url_rule(
        "/edit-carousel/<group_id>",
        "edit_carousel",
        edit_carousel,
        methods=["GET", "POST"],
    )
    state.app.add_url_rule(
        "/duplicate-post/<int:post_id>",
        "duplicate_post",
        duplicate_post,
        methods=["POST"],
    )
    state.app.add_url_rule(
        "/duplicate-carousel/<group_id>",
        "duplicate_carousel",
        duplicate_carousel,
        methods=["POST"],
    )
    state.app.add_url_rule(
        "/delete/<int:post_id>",
        "delete_post",
        delete_post,
        methods=["POST"],
    )
    state.app.add_url_rule(
        "/delete-carousel/<group_id>",
        "delete_carousel",
        delete_carousel,
        methods=["POST"],
    )
