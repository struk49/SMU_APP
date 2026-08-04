from flask import Blueprint, current_app, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from smu_core.models import Post


posts_bp = Blueprint("posts", __name__)


def _post_detail_helper(name):
    helpers = current_app.extensions.get("smu_post_detail_helpers", {})
    helper = helpers.get(name)

    if not helper:
        raise RuntimeError(f"Post detail helper is not available: {name}")

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


@posts_bp.record_once
def register_routes(state):
    state.app.add_url_rule("/post/<int:post_id>", "view_post", view_post)
