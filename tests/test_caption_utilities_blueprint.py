from datetime import datetime

from flask import url_for

import app as smu_app
from conftest import create_carousel, create_post, create_user, login
from smu_core.models import Post, PostRevision


def rules_for(app, path):
    return [rule for rule in app.url_map.iter_rules() if rule.rule == path]


def set_caption_helper(app, monkeypatch, name, helper):
    monkeypatch.setitem(app.extensions["smu_caption_helpers"], name, helper)


def test_caption_routes_registered_once_with_old_endpoints(module):
    expected = {
        "/rewrite-caption/<int:post_id>": ("rewrite_caption", {"POST"}),
        "/rewrite-carousel-caption/<group_id>": ("rewrite_carousel_caption", {"POST"}),
        "/post/<int:post_id>/improve": ("improve_post", {"POST"}),
        "/post/<int:post_id>/use-improved": ("use_improved_caption", {"POST"}),
        "/post/<int:post_id>/custom-caption": ("use_custom_caption", {"POST"}),
        "/post/<int:post_id>/discard-improved": ("discard_improved_caption", {"POST"}),
    }

    assert "posts" in module.app.blueprints
    assert list(module.app.blueprints).count("posts") == 1

    for path, (endpoint, methods) in expected.items():
        rules = rules_for(module.app, path)

        assert len(rules) == 1
        assert rules[0].endpoint == endpoint
        assert methods.issubset(rules[0].methods)


def test_caption_url_for_compatibility(module):
    with module.app.test_request_context():
        assert url_for("rewrite_caption", post_id=1) == "/rewrite-caption/1"
        assert url_for("rewrite_carousel_caption", group_id="abc") == (
            "/rewrite-carousel-caption/abc"
        )
        assert url_for("improve_post", post_id=1) == "/post/1/improve"
        assert url_for("use_improved_caption", post_id=1) == "/post/1/use-improved"
        assert url_for("use_custom_caption", post_id=1) == "/post/1/custom-caption"
        assert url_for("discard_improved_caption", post_id=1) == (
            "/post/1/discard-improved"
        )


def test_caption_routes_require_login(client, module):
    user = create_user(module)
    post = create_post(module, user)
    group_id, _posts = create_carousel(module, user)

    paths = [
        f"/rewrite-caption/{post.id}",
        f"/rewrite-carousel-caption/{group_id}",
        f"/post/{post.id}/improve",
        f"/post/{post.id}/use-improved",
        f"/post/{post.id}/custom-caption",
        f"/post/{post.id}/discard-improved",
    ]

    for path in paths:
        response = client.post(path)

        assert response.status_code == 302
        assert "/login" in response.location


def test_single_caption_rewrite_updates_caption_without_real_openai(
    client,
    app,
    module,
    monkeypatch,
):
    user = create_user(module)
    post = create_post(module, user)
    post.caption = "Original"
    post.prompt = "Prompt"
    module.db.session.commit()
    calls = []

    def fake_rewrite(caption, rewrite_type):
        calls.append((caption, rewrite_type))
        return "Rewritten"

    set_caption_helper(app, monkeypatch, "rewrite_caption_with_ai", fake_rewrite)
    login(client, user)

    response = client.post(
        f"/rewrite-caption/{post.id}",
        data={"caption": " Original ", "rewrite_type": "viral"},
        follow_redirects=True,
    )
    updated = module.db.session.get(Post, post.id)

    assert response.status_code == 200
    assert "Caption rewritten successfully." in response.get_data(as_text=True)
    assert calls == [("Original", "viral")]
    assert updated.caption == "Rewritten"
    assert updated.prompt == "Prompt"


def test_single_caption_rewrite_rejects_other_user(client, app, module, monkeypatch):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    post = create_post(module, owner)
    calls = []
    set_caption_helper(app, monkeypatch, "rewrite_caption_with_ai", lambda *a: calls.append(a))
    login(client, other)

    response = client.post(
        f"/rewrite-caption/{post.id}",
        data={"caption": "Caption", "rewrite_type": "viral"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "You do not have access to this post." in response.get_data(as_text=True)
    assert calls == []


def test_single_caption_rewrite_validation_and_failure_preserve_caption(
    client,
    app,
    module,
    monkeypatch,
):
    user = create_user(module)
    post = create_post(module, user)
    post.caption = "Original"
    module.db.session.commit()
    login(client, user)

    empty_response = client.post(
        f"/rewrite-caption/{post.id}",
        data={"caption": " ", "rewrite_type": "viral"},
        follow_redirects=True,
    )
    assert "Add a caption before using AI rewrite." in empty_response.get_data(as_text=True)
    assert module.db.session.get(Post, post.id).caption == "Original"

    def fail_rewrite(*args):
        raise RuntimeError("AI failed")

    set_caption_helper(app, monkeypatch, "rewrite_caption_with_ai", fail_rewrite)
    failure_response = client.post(
        f"/rewrite-caption/{post.id}",
        data={"caption": "Original", "rewrite_type": "viral"},
        follow_redirects=True,
    )

    assert "Failed to rewrite caption: AI failed" in failure_response.get_data(as_text=True)
    assert module.db.session.get(Post, post.id).caption == "Original"


def test_carousel_caption_rewrite_updates_owned_group_only(client, app, module, monkeypatch):
    user = create_user(module)
    other = create_user(module, email="other@example.com")
    group_id, posts = create_carousel(module, user)
    other_group_id, other_posts = create_carousel(module, other)
    calls = []

    def fake_rewrite(caption, rewrite_type):
        calls.append((caption, rewrite_type))
        return "Carousel rewritten"

    set_caption_helper(app, monkeypatch, "rewrite_caption_with_ai", fake_rewrite)
    login(client, user)

    response = client.post(
        f"/rewrite-carousel-caption/{group_id}",
        data={"caption": " Group caption ", "rewrite_type": "cta"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Carousel caption rewritten successfully." in response.get_data(as_text=True)
    assert calls == [("Group caption", "cta")]
    assert {post.caption for post in Post.query.filter_by(group_id=group_id)} == {
        "Carousel rewritten"
    }
    assert {post.caption for post in Post.query.filter_by(group_id=other_group_id)} == {
        post.caption for post in other_posts
    }
    assert Post.query.filter_by(group_id=group_id).count() == len(posts)


def test_carousel_caption_rewrite_missing_and_failure(client, app, module, monkeypatch):
    user = create_user(module)
    group_id, posts = create_carousel(module, user)
    login(client, user)

    missing_response = client.post(
        "/rewrite-carousel-caption/missing",
        data={"caption": "Caption"},
        follow_redirects=True,
    )
    assert "Carousel not found." in missing_response.get_data(as_text=True)

    def fail_rewrite(*args):
        raise RuntimeError("AI failed")

    set_caption_helper(app, monkeypatch, "rewrite_caption_with_ai", fail_rewrite)
    failure_response = client.post(
        f"/rewrite-carousel-caption/{group_id}",
        data={"caption": "Caption", "rewrite_type": "viral"},
        follow_redirects=True,
    )

    assert "Failed to rewrite carousel caption: AI failed" in failure_response.get_data(as_text=True)
    assert {post.caption for post in Post.query.filter_by(group_id=group_id)} == {
        post.caption for post in posts
    }


def test_improve_stores_improved_caption_and_preserves_original(
    client,
    app,
    module,
    monkeypatch,
):
    user = create_user(module)
    post = create_post(module, user)
    post.caption = "Original"
    module.db.session.commit()
    calls = []

    set_caption_helper(app, monkeypatch, "build_brand_context", lambda user_id: "BRAND")

    def fake_improve(improved_post, brand_context):
        calls.append((improved_post.id, improved_post.caption, brand_context))
        return "Improved"

    set_caption_helper(app, monkeypatch, "improve_post_with_ai", fake_improve)
    set_caption_helper(app, monkeypatch, "update_brand_coach", lambda *args: None)
    login(client, user)

    response = client.post(f"/post/{post.id}/improve", follow_redirects=True)
    updated = module.db.session.get(Post, post.id)

    assert response.status_code == 200
    assert "Improved caption created successfully." in response.get_data(as_text=True)
    assert calls == [(post.id, "Original", "BRAND")]
    assert updated.caption == "Original"
    assert updated.improved_caption == "Improved"
    assert updated.improved_at is not None


def test_improve_failure_does_not_persist_partial_changes(client, app, module, monkeypatch):
    user = create_user(module)
    post = create_post(module, user)
    set_caption_helper(app, monkeypatch, "build_brand_context", lambda user_id: "")

    def fail_improve(*args):
        raise RuntimeError("improve failed")

    set_caption_helper(app, monkeypatch, "improve_post_with_ai", fail_improve)
    login(client, user)

    response = client.post(f"/post/{post.id}/improve", follow_redirects=True)
    updated = module.db.session.get(Post, post.id)

    assert "Failed to improve post: improve failed" in response.get_data(as_text=True)
    assert updated.improved_caption is None
    assert updated.improved_at is None


def test_use_improved_replaces_caption_and_creates_revision(client, app, module, monkeypatch):
    user = create_user(module)
    post = create_post(module, user)
    post.caption = "Original"
    post.improved_caption = "Improved"
    post.improved_at = datetime.utcnow()
    module.db.session.commit()

    set_caption_helper(app, monkeypatch, "build_brand_context", lambda user_id: "")
    set_caption_helper(app, monkeypatch, "update_brand_coach", lambda *args: None)
    login(client, user)

    response = client.post(f"/post/{post.id}/use-improved", follow_redirects=True)
    updated = module.db.session.get(Post, post.id)
    revision = PostRevision.query.filter_by(post_id=post.id).one()

    assert response.status_code == 200
    assert "Improved caption is now the main caption." in response.get_data(as_text=True)
    assert updated.caption == "Improved"
    assert updated.improved_caption is None
    assert updated.improved_at is None
    assert revision.caption == "Original"
    assert revision.source == "before_ai_improved"


def test_use_improved_requires_existing_improved_caption(client, module):
    user = create_user(module)
    post = create_post(module, user)
    login(client, user)

    response = client.post(f"/post/{post.id}/use-improved", follow_redirects=True)

    assert "No improved caption found." in response.get_data(as_text=True)
    assert PostRevision.query.count() == 0


def test_custom_caption_saves_and_creates_revision(client, module):
    user = create_user(module)
    post = create_post(module, user)
    post.caption = "Original"
    post.improved_caption = "Improved"
    post.improved_at = datetime.utcnow()
    module.db.session.commit()
    login(client, user)

    response = client.post(
        f"/post/{post.id}/custom-caption",
        data={"custom_caption": " Custom caption "},
        follow_redirects=True,
    )
    updated = module.db.session.get(Post, post.id)
    revision = PostRevision.query.filter_by(post_id=post.id).one()

    assert response.status_code == 200
    assert "Custom caption saved as the main caption." in response.get_data(as_text=True)
    assert updated.caption == "Custom caption"
    assert updated.improved_caption is None
    assert updated.improved_at is None
    assert revision.caption == "Original"
    assert revision.source == "before_custom_caption"


def test_custom_caption_rejects_blank(client, module):
    user = create_user(module)
    post = create_post(module, user)
    post.caption = "Original"
    module.db.session.commit()
    login(client, user)

    response = client.post(
        f"/post/{post.id}/custom-caption",
        data={"custom_caption": " "},
        follow_redirects=True,
    )

    assert "Custom caption cannot be empty." in response.get_data(as_text=True)
    assert module.db.session.get(Post, post.id).caption == "Original"
    assert PostRevision.query.count() == 0


def test_discard_improved_clears_suggestion_without_changing_caption(client, module):
    user = create_user(module)
    post = create_post(module, user)
    post.caption = "Original"
    post.improved_caption = "Improved"
    post.improved_at = datetime.utcnow()
    module.db.session.commit()
    login(client, user)

    response = client.post(f"/post/{post.id}/discard-improved", follow_redirects=True)
    updated = module.db.session.get(Post, post.id)

    assert response.status_code == 200
    assert "Improved caption discarded." in response.get_data(as_text=True)
    assert updated.caption == "Original"
    assert updated.improved_caption is None
    assert updated.improved_at is None


def test_caption_routes_reject_other_users_for_owned_post_routes(client, app, module, monkeypatch):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    post = create_post(module, owner)
    post.improved_caption = "Improved"
    module.db.session.commit()
    set_caption_helper(app, monkeypatch, "build_brand_context", lambda user_id: "")
    set_caption_helper(app, monkeypatch, "improve_post_with_ai", lambda *args: "Improved")
    login(client, other)

    for path, data in {
        f"/post/{post.id}/improve": {},
        f"/post/{post.id}/use-improved": {},
        f"/post/{post.id}/custom-caption": {"custom_caption": "Custom"},
        f"/post/{post.id}/discard-improved": {},
    }.items():
        response = client.post(path, data=data)

        assert response.status_code == 404


def test_caption_helper_bridge_and_model_compatibility(module):
    helpers = module.app.extensions["smu_caption_helpers"]

    for name in {
        "rewrite_caption_with_ai",
        "get_ordered_carousel_posts",
        "build_brand_context",
        "improve_post_with_ai",
        "update_brand_coach",
        "save_post_revision",
    }:
        assert callable(helpers[name])

    assert smu_app.Post is Post
    assert smu_app.PostRevision is PostRevision


def test_studio_and_existing_endpoints_remain_registered(module):
    assert "smu_caption_helpers" in module.app.extensions

    expected = {
        "/post/<int:post_id>": "view_post",
        "/edit-post/<int:post_id>": "edit_post",
        "/create": "create_post",
        "/delete/<int:post_id>": "delete_post",
        "/duplicate-post/<int:post_id>": "duplicate_post",
        "/schedule/<int:post_id>": "schedule_post",
        "/send/<int:post_id>": "send_to_make",
        "/post/<int:post_id>/ai-editor": "ai_editor",
        "/post/<int:post_id>/studio": "post_studio",
        "/post/<int:post_id>/studio/action/<action>": "studio_action",
        "/post/<int:post_id>/studio/regrade": "studio_regrade",
        "/post/<int:post_id>/revision/<int:revision_id>/restore": "restore_revision",
    }

    for path, endpoint in expected.items():
        rules = [
            rule
            for rule in module.app.url_map.iter_rules()
            if rule.endpoint == endpoint
        ]

        assert len(rules) == 1
        assert rules[0].rule == path
