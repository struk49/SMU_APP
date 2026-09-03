from datetime import datetime

import pytest
from flask import url_for

import app as smu_app
from conftest import create_carousel, create_post, create_user, login
from smu_core.models import Post


def rules_for(app, path):
    return [rule for rule in app.url_map.iter_rules() if rule.rule == path]


def create_revision(module, post, user, version=1):
    revision = module.PostRevision(
        post_id=post.id,
        user_id=user.id,
        version_number=version,
        caption=f"Revision {version}",
        score=80.0,
        source="test",
    )
    module.db.session.add(revision)
    module.db.session.commit()
    return revision


def test_delete_duplicate_routes_registered_once_with_old_endpoints(module):
    expected = {
        "/delete/<int:post_id>": ("delete_post", {"POST"}),
        "/delete-carousel/<group_id>": ("delete_carousel", {"POST"}),
        "/duplicate-post/<int:post_id>": ("duplicate_post", {"POST"}),
        "/duplicate-carousel/<group_id>": ("duplicate_carousel", {"POST"}),
    }

    assert "posts" in module.app.blueprints
    assert list(module.app.blueprints).count("posts") == 1

    for path, (endpoint, methods) in expected.items():
        rules = rules_for(module.app, path)

        assert len(rules) == 1
        assert rules[0].endpoint == endpoint
        assert methods.issubset(rules[0].methods)


def test_delete_duplicate_url_for_compatibility(module):
    with module.app.test_request_context():
        assert url_for("delete_post", post_id=1) == "/delete/1"
        assert url_for("delete_carousel", group_id="abc") == "/delete-carousel/abc"
        assert url_for("duplicate_post", post_id=1) == "/duplicate-post/1"
        assert url_for("duplicate_carousel", group_id="abc") == "/duplicate-carousel/abc"


def test_delete_duplicate_routes_require_login(client, module):
    user = create_user(module)
    post = create_post(module, user)
    group_id, _posts = create_carousel(module, user)

    responses = [
        client.post(f"/delete/{post.id}"),
        client.post(f"/delete-carousel/{group_id}"),
        client.post(f"/duplicate-post/{post.id}"),
        client.post(f"/duplicate-carousel/{group_id}"),
    ]

    for response in responses:
        assert response.status_code == 302
        assert "/login" in response.location


def test_owner_can_delete_single_post_and_revision_cascade(client, module):
    user = create_user(module)
    post = create_post(module, user)
    unrelated = create_post(module, user)
    revision = create_revision(module, post, user)
    login(client, user)

    response = client.post(f"/delete/{post.id}", follow_redirects=True)

    assert response.status_code == 200
    assert "Post deleted." in response.get_data(as_text=True)
    assert module.db.session.get(Post, post.id) is None
    assert module.db.session.get(Post, unrelated.id) is not None
    assert module.db.session.get(module.PostRevision, revision.id) is None


def test_another_user_cannot_delete_single_post(client, module):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    post = create_post(module, owner)
    login(client, other)

    response = client.post(f"/delete/{post.id}", follow_redirects=True)

    assert response.status_code == 200
    assert "You do not have access to this post." in response.get_data(as_text=True)
    assert module.db.session.get(Post, post.id) is not None


def test_missing_single_delete_returns_404(client, module):
    user = create_user(module)
    login(client, user)

    response = client.post("/delete/999")

    assert response.status_code == 404


def test_owner_can_delete_carousel_and_revision_cascade(client, module):
    user = create_user(module)
    other_user = create_user(module, email="other@example.com")
    group_id, posts = create_carousel(module, user)
    other_group_id, other_posts = create_carousel(module, other_user)
    revisions = [
        create_revision(module, post, user, version=index + 1)
        for index, post in enumerate(posts)
    ]
    login(client, user)

    response = client.post(f"/delete-carousel/{group_id}", follow_redirects=True)

    assert response.status_code == 200
    assert "Carousel deleted." in response.get_data(as_text=True)
    assert Post.query.filter_by(group_id=group_id).count() == 0
    assert Post.query.filter_by(group_id=other_group_id).count() == len(other_posts)
    assert all(module.db.session.get(module.PostRevision, rev.id) is None for rev in revisions)


def test_another_user_cannot_delete_carousel(client, module):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    group_id, posts = create_carousel(module, owner)
    login(client, other)

    response = client.post(f"/delete-carousel/{group_id}", follow_redirects=True)

    assert response.status_code == 200
    assert "Carousel not found." in response.get_data(as_text=True)
    assert Post.query.filter_by(group_id=group_id).count() == len(posts)


def test_missing_carousel_delete_redirects_to_dashboard(client, module):
    user = create_user(module)
    login(client, user)

    response = client.post("/delete-carousel/missing", follow_redirects=True)

    assert response.status_code == 200
    assert "Carousel not found." in response.get_data(as_text=True)


def test_owner_can_duplicate_single_post_with_current_copied_and_reset_fields(client, module):
    user = create_user(module)
    post = create_post(module, user, status="sent_to_make", platforms="instagram,pinterest")
    post.sent_at = datetime(2026, 7, 1, 10, 0)
    post.scheduled_time = datetime(2026, 7, 2, 10, 0)
    post.grade_result = "Grade"
    post.grade_score = 91.0
    post.improved_caption = "Improved"
    post.brand_score = 82.0
    post.brand_feedback = "Feedback"
    module.db.session.commit()
    login(client, user)

    response = client.post(f"/duplicate-post/{post.id}")
    duplicate = Post.query.filter(Post.id != post.id).one()

    assert response.status_code == 302
    assert response.location.endswith(f"/post/{duplicate.id}")
    assert duplicate.file_url == post.file_url
    assert duplicate.file_type == post.file_type
    assert duplicate.prompt == post.prompt
    assert duplicate.caption == post.caption
    assert duplicate.platforms == post.platforms
    assert duplicate.status == "draft"
    assert duplicate.post_type == "single"
    assert duplicate.group_id is None
    assert duplicate.sort_order == 0
    assert duplicate.is_cover is False
    assert duplicate.scheduled_time is None
    assert duplicate.sent_at is None
    assert duplicate.user_id == user.id
    assert duplicate.grade_result is None
    assert duplicate.grade_score is None
    assert duplicate.improved_caption is None
    assert duplicate.brand_score is None
    assert duplicate.brand_feedback is None
    assert module.db.session.get(Post, post.id) is not None


def test_another_user_cannot_duplicate_single_post(client, module):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    post = create_post(module, owner)
    login(client, other)

    response = client.post(f"/duplicate-post/{post.id}", follow_redirects=True)

    assert response.status_code == 200
    assert "You do not have access to this post." in response.get_data(as_text=True)
    assert Post.query.count() == 1


def test_missing_single_duplicate_returns_404(client, module):
    user = create_user(module)
    login(client, user)

    response = client.post("/duplicate-post/999")

    assert response.status_code == 404


def test_duplicate_single_redirects_carousel_rows_to_post_detail(client, module):
    user = create_user(module)
    _group_id, posts = create_carousel(module, user)
    login(client, user)

    response = client.post(f"/duplicate-post/{posts[0].id}")

    assert response.status_code == 302
    assert response.location.endswith(f"/post/{posts[0].id}")
    assert Post.query.count() == 2


def test_owner_can_duplicate_carousel_with_current_copied_and_reset_fields(client, module):
    user = create_user(module)
    group_id, posts = create_carousel(module, user, status="sent_to_make")
    for index, post in enumerate(posts):
        post.sent_at = datetime(2026, 7, index + 1, 10, 0)
        post.scheduled_time = datetime(2026, 8, index + 1, 10, 0)
        post.grade_result = f"Grade {index}"
        post.grade_score = 90 + index
        post.improved_caption = f"Improved {index}"
        post.brand_score = 80 + index
        post.brand_feedback = f"Feedback {index}"
    module.db.session.commit()
    login(client, user)

    response = client.post(f"/duplicate-carousel/{group_id}")
    duplicates = (
        Post.query.filter(Post.group_id != group_id)
        .order_by(Post.sort_order)
        .all()
    )
    new_group_ids = {post.group_id for post in duplicates}

    assert response.status_code == 302
    assert len(duplicates) == len(posts)
    assert len(new_group_ids) == 1
    assert group_id not in new_group_ids
    assert response.location.endswith(f"/post/{duplicates[0].id}")

    for source, duplicate in zip(posts, duplicates):
        assert duplicate.file_url == source.file_url
        assert duplicate.file_type == source.file_type
        assert duplicate.prompt == source.prompt
        assert duplicate.caption == source.caption
        assert duplicate.platforms == source.platforms
        assert duplicate.sort_order == source.sort_order
        assert duplicate.is_cover == source.is_cover
        assert duplicate.status == "draft"
        assert duplicate.post_type == "carousel"
        assert duplicate.scheduled_time is None
        assert duplicate.sent_at is None
        assert duplicate.user_id == user.id
        assert duplicate.grade_result is None
        assert duplicate.grade_score is None
        assert duplicate.improved_caption is None
        assert duplicate.brand_score is None
        assert duplicate.brand_feedback is None

    assert Post.query.filter_by(group_id=group_id).count() == len(posts)


def test_another_user_cannot_duplicate_carousel(client, module):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    group_id, posts = create_carousel(module, owner)
    login(client, other)

    response = client.post(f"/duplicate-carousel/{group_id}", follow_redirects=True)

    assert response.status_code == 200
    assert "Carousel not found." in response.get_data(as_text=True)
    assert Post.query.filter_by(group_id=group_id).count() == len(posts)
    assert Post.query.count() == len(posts)


def test_missing_carousel_duplicate_redirects_to_dashboard(client, module):
    user = create_user(module)
    login(client, user)

    response = client.post("/duplicate-carousel/missing", follow_redirects=True)

    assert response.status_code == 200
    assert "Carousel not found." in response.get_data(as_text=True)


def test_duplicate_carousel_commit_failure_preserves_current_error_redirect(
    client,
    module,
    monkeypatch,
):
    user = create_user(module)
    group_id, posts = create_carousel(module, user)
    login(client, user)

    original_commit = module.db.session.commit

    def fail_commit():
        raise RuntimeError("commit failed")

    monkeypatch.setattr(module.db.session, "commit", fail_commit)

    response = client.post(f"/duplicate-carousel/{group_id}", follow_redirects=False)
    module.db.session.rollback()
    monkeypatch.setattr(module.db.session, "commit", original_commit)

    assert response.status_code == 302
    assert response.location.endswith("/")
    with client.session_transaction() as session:
        assert (
            "danger",
            "Failed to duplicate carousel: commit failed",
        ) in session.get("_flashes", [])
    assert Post.query.filter_by(group_id=group_id).count() == len(posts)
    assert Post.query.count() == len(posts)


def test_delete_carousel_commit_failure_does_not_partially_delete(client, module, monkeypatch):
    user = create_user(module)
    group_id, posts = create_carousel(module, user)
    login(client, user)

    def fail_commit():
        raise RuntimeError("commit failed")

    monkeypatch.setattr(module.db.session, "commit", fail_commit)

    with pytest.raises(RuntimeError, match="commit failed"):
        client.post(f"/delete-carousel/{group_id}")
    module.db.session.rollback()

    assert Post.query.filter_by(group_id=group_id).count() == len(posts)


def test_delete_duplicate_helper_bridge_and_model_compatibility(module):
    helpers = module.app.extensions["smu_post_delete_duplicate_helpers"]

    assert callable(helpers["get_ordered_carousel_posts"])
    assert smu_app.Post is Post


def test_existing_post_helper_bridges_remain_present(module):
    assert "smu_post_detail_helpers" in module.app.extensions
    assert "smu_post_edit_helpers" in module.app.extensions
    assert "smu_post_delete_duplicate_helpers" in module.app.extensions


def test_unrelated_post_endpoints_remain_registered(module):
    expected = {
        "/create": "create_post",
        "/post/<int:post_id>": "view_post",
        "/edit-post/<int:post_id>": "edit_post",
        "/edit-carousel/<group_id>": "edit_carousel",
        "/rewrite-caption/<int:post_id>": "rewrite_caption",
        "/rewrite-carousel-caption/<group_id>": "rewrite_carousel_caption",
        "/send/<int:post_id>": "send_to_make",
        "/send-carousel/<group_id>": "send_carousel_to_make",
        "/schedule/<int:post_id>": "schedule_post",
        "/post/<int:post_id>/improve": "improve_post",
        "/post/<int:post_id>/use-improved": "use_improved_caption",
        "/post/<int:post_id>/custom-caption": "use_custom_caption",
        "/post/<int:post_id>/discard-improved": "discard_improved_caption",
        "/post/<int:post_id>/ai-editor": "ai_editor",
        "/post/<int:post_id>/studio": "post_studio",
        "/post/<int:post_id>/studio/action/<action>": "studio_action",
        "/post/<int:post_id>/studio/regrade": "studio_regrade",
    }

    for path, endpoint in expected.items():
        rules = rules_for(module.app, path)

        assert len(rules) == 1
        assert rules[0].endpoint == endpoint
