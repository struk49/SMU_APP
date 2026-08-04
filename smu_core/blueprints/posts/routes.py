import json
import uuid

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
