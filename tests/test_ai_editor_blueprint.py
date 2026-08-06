from datetime import datetime
from contextlib import contextmanager

import pytest
from flask import template_rendered, url_for

import app as smu_app
from conftest import create_post, create_user, login
from smu_core.models import Post, PostRevision


@contextmanager
def captured_templates(app):
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append((template.name, context))

    template_rendered.connect(record, app)
    try:
        yield recorded
    finally:
        template_rendered.disconnect(record, app)


def rules_for_endpoint(app, endpoint):
    return [rule for rule in app.url_map.iter_rules() if rule.endpoint == endpoint]


def set_ai_editor_helper(app, monkeypatch, name, helper):
    monkeypatch.setitem(app.extensions["smu_ai_editor_helpers"], name, helper)


def test_ai_editor_route_registered_once_with_old_endpoint(module):
    rules = rules_for_endpoint(module.app, "ai_editor")

    assert "posts" in module.app.blueprints
    assert list(module.app.blueprints).count("posts") == 1
    assert len(rules) == 1
    assert rules[0].rule == "/post/<int:post_id>/ai-editor"
    assert {"GET", "POST"}.issubset(rules[0].methods)


def test_ai_editor_url_for_compatibility(module):
    with module.app.test_request_context():
        assert url_for("ai_editor", post_id=1) == "/post/1/ai-editor"


def test_ai_editor_requires_login(client, module):
    user = create_user(module)
    post = create_post(module, user)

    get_response = client.get(f"/post/{post.id}/ai-editor")
    post_response = client.post(
        f"/post/{post.id}/ai-editor",
        data={"final_caption": "Final"},
    )

    assert get_response.status_code == 302
    assert "/login" in get_response.location
    assert post_response.status_code == 302
    assert "/login" in post_response.location


def test_owner_can_open_ai_editor_with_same_template_context(client, app, module):
    user = create_user(module)
    post = create_post(module, user)
    post.caption = "Original"
    post.improved_caption = "Improved"
    module.db.session.commit()
    login(client, user)

    with captured_templates(app) as templates:
        response = client.get(f"/post/{post.id}/ai-editor")

    assert response.status_code == 200
    assert templates[0][0] == "ai_editor.html"
    assert templates[0][1]["post"].id == post.id


def test_ai_editor_rejects_cross_user_post(client, module):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    post = create_post(module, owner)
    login(client, other)

    response = client.get(f"/post/{post.id}/ai-editor")

    assert response.status_code == 404


def test_ai_editor_missing_post_preserves_404(client, module):
    user = create_user(module)
    login(client, user)

    response = client.get("/post/999/ai-editor")

    assert response.status_code == 404


def test_ai_editor_post_saves_final_caption_and_revision_without_real_openai(
    client,
    app,
    module,
    monkeypatch,
):
    user = create_user(module)
    post = create_post(module, user)
    post.caption = "Original caption"
    post.improved_caption = "Suggested caption"
    post.improved_at = datetime(2026, 7, 10, 8, 0)
    module.db.session.commit()
    context_calls = []
    coach_calls = []

    def fake_context(user_id):
        context_calls.append(user_id)
        return "BRAND CONTEXT"

    def fake_coach(coached_post, brand_context):
        coach_calls.append((coached_post.id, coached_post.caption, brand_context))

    set_ai_editor_helper(app, monkeypatch, "build_brand_context", fake_context)
    set_ai_editor_helper(app, monkeypatch, "update_brand_coach", fake_coach)
    login(client, user)

    response = client.post(
        f"/post/{post.id}/ai-editor",
        data={"final_caption": " Final caption "},
        follow_redirects=True,
    )
    updated = module.db.session.get(Post, post.id)
    revision = PostRevision.query.filter_by(post_id=post.id).one()

    assert response.status_code == 200
    assert "Final caption saved successfully." in response.get_data(as_text=True)
    assert updated.caption == "Final caption"
    assert updated.improved_caption is None
    assert updated.improved_at is None
    assert revision.caption == "Original caption"
    assert revision.source == "before_ai_editor"
    assert context_calls == [user.id]
    assert coach_calls == [(post.id, "Final caption", "BRAND CONTEXT")]


def test_ai_editor_blank_caption_preserves_validation_response(client, module):
    user = create_user(module)
    post = create_post(module, user)
    post.caption = "Original"
    module.db.session.commit()
    login(client, user)

    response = client.post(
        f"/post/{post.id}/ai-editor",
        data={"final_caption": " "},
        follow_redirects=True,
    )
    unchanged = module.db.session.get(Post, post.id)

    assert response.status_code == 200
    assert "Final caption cannot be empty." in response.get_data(as_text=True)
    assert unchanged.caption == "Original"
    assert PostRevision.query.count() == 0


def test_ai_editor_commit_failure_preserves_current_exception_and_can_roll_back(
    client,
    app,
    module,
    monkeypatch,
):
    user = create_user(module)
    post = create_post(module, user)
    post.caption = "Original"
    post.improved_caption = "Suggested"
    module.db.session.commit()

    set_ai_editor_helper(app, monkeypatch, "build_brand_context", lambda user_id: "")
    set_ai_editor_helper(app, monkeypatch, "update_brand_coach", lambda *args: None)

    def fail_commit():
        raise RuntimeError("commit failed")

    monkeypatch.setattr(module.db.session, "commit", fail_commit)
    login(client, user)

    with pytest.raises(RuntimeError, match="commit failed"):
        client.post(
            f"/post/{post.id}/ai-editor",
            data={"final_caption": "Final"},
        )

    module.db.session.rollback()
    unchanged = module.db.session.get(Post, post.id)

    assert unchanged.caption == "Original"
    assert unchanged.improved_caption == "Suggested"
    assert PostRevision.query.count() == 0


def test_ai_editor_helper_bridge_and_model_compatibility(module):
    helpers = module.app.extensions["smu_ai_editor_helpers"]

    assert callable(helpers["save_post_revision"])
    assert callable(helpers["build_brand_context"])
    assert callable(helpers["update_brand_coach"])
    assert smu_app.Post is Post
    assert smu_app.PostRevision is PostRevision


def test_studio_routes_and_existing_bridges_remain_registered_in_app(module):
    for key in {
        "smu_post_detail_helpers",
        "smu_post_edit_helpers",
        "smu_post_delete_duplicate_helpers",
        "smu_post_create_helpers",
        "smu_post_schedule_helpers",
        "smu_manual_publish_helpers",
        "smu_caption_helpers",
        "smu_ai_editor_helpers",
    }:
        assert key in module.app.extensions

    for endpoint, path in {
        "post_studio": "/post/<int:post_id>/studio",
        "studio_action": "/post/<int:post_id>/studio/action/<action>",
        "studio_regrade": "/post/<int:post_id>/studio/regrade",
        "restore_revision": "/post/<int:post_id>/revision/<int:revision_id>/restore",
    }.items():
        rules = rules_for_endpoint(module.app, endpoint)

        assert len(rules) == 1
        assert rules[0].rule == path


def test_post_routes_around_ai_editor_remain_registered(module):
    for endpoint, path in {
        "view_post": "/post/<int:post_id>",
        "edit_post": "/edit-post/<int:post_id>",
        "create_post": "/create",
        "delete_post": "/delete/<int:post_id>",
        "duplicate_post": "/duplicate-post/<int:post_id>",
        "schedule_post": "/schedule/<int:post_id>",
        "send_to_make": "/send/<int:post_id>",
        "rewrite_caption": "/rewrite-caption/<int:post_id>",
        "discard_improved_caption": "/post/<int:post_id>/discard-improved",
    }.items():
        rules = rules_for_endpoint(module.app, endpoint)

        assert len(rules) == 1
        assert rules[0].rule == path
