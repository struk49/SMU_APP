from contextlib import contextmanager
from datetime import datetime

import pytest
from flask import template_rendered, url_for

import app as smu_app
from conftest import create_post, create_user, login
from smu_core.models import BrandBrief, Post, PostRevision


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


def set_studio_helper(app, monkeypatch, name, helper):
    monkeypatch.setitem(app.extensions["smu_studio_helpers"], name, helper)


def create_revision(module, post, user, *, version_number, caption, source="manual"):
    revision = module.PostRevision(
        post_id=post.id,
        user_id=user.id,
        version_number=version_number,
        caption=caption,
        score=post.grade_score,
        source=source,
    )
    module.db.session.add(revision)
    module.db.session.commit()
    return revision


def test_studio_routes_registered_once_with_old_endpoints(module):
    expected = {
        "post_studio": ("/post/<int:post_id>/studio", {"GET", "POST"}),
        "studio_action": ("/post/<int:post_id>/studio/action/<action>", {"POST"}),
        "studio_regrade": ("/post/<int:post_id>/studio/regrade", {"POST"}),
        "restore_revision": (
            "/post/<int:post_id>/revision/<int:revision_id>/restore",
            {"POST"},
        ),
    }

    assert "posts" in module.app.blueprints
    assert list(module.app.blueprints).count("posts") == 1

    for endpoint, (path, methods) in expected.items():
        rules = rules_for_endpoint(module.app, endpoint)

        assert len(rules) == 1
        assert rules[0].rule == path
        assert methods.issubset(rules[0].methods)


def test_studio_url_for_compatibility(module):
    with module.app.test_request_context():
        assert url_for("post_studio", post_id=1) == "/post/1/studio"
        assert url_for("studio_action", post_id=1, action="hook") == (
            "/post/1/studio/action/hook"
        )
        assert url_for("studio_regrade", post_id=1) == "/post/1/studio/regrade"
        assert url_for("restore_revision", post_id=1, revision_id=2) == (
            "/post/1/revision/2/restore"
        )


def test_studio_routes_require_login(client, module):
    user = create_user(module)
    post = create_post(module, user)
    revision = create_revision(
        module,
        post,
        user,
        version_number=1,
        caption="Previous caption",
    )

    responses = [
        client.get(f"/post/{post.id}/studio"),
        client.post(f"/post/{post.id}/studio", data={"final_caption": "Final"}),
        client.post(f"/post/{post.id}/studio/action/hook"),
        client.post(f"/post/{post.id}/studio/regrade"),
        client.post(f"/post/{post.id}/revision/{revision.id}/restore"),
    ]

    for response in responses:
        assert response.status_code == 302
        assert "/login" in response.location


def test_owner_can_open_studio_with_template_context_and_revision_order(client, app, module):
    user = create_user(module)
    post = create_post(module, user)
    create_revision(module, post, user, version_number=1, caption="First")
    create_revision(module, post, user, version_number=2, caption="Second")
    login(client, user)

    with captured_templates(app) as templates:
        response = client.get(f"/post/{post.id}/studio")

    assert response.status_code == 200
    assert templates[0][0] == "post_studio.html"
    assert templates[0][1]["post"].id == post.id
    assert templates[0][1]["brief"] is None
    assert [revision.version_number for revision in templates[0][1]["revisions"]] == [2, 1]


def test_studio_context_includes_user_brand_brief(client, app, module):
    user = create_user(module)
    post = create_post(module, user)
    brief = BrandBrief(user_id=user.id, business_name="SMU")
    module.db.session.add(brief)
    module.db.session.commit()
    login(client, user)

    with captured_templates(app) as templates:
        response = client.get(f"/post/{post.id}/studio")

    assert response.status_code == 200
    assert templates[0][1]["brief"].id == brief.id


def test_studio_rejects_cross_user_and_missing_posts(client, module):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    post = create_post(module, owner)
    login(client, other)

    assert client.get(f"/post/{post.id}/studio").status_code == 404
    assert client.post(f"/post/{post.id}/studio/action/hook").status_code == 404
    assert client.post(f"/post/{post.id}/studio/regrade").status_code == 404
    assert client.get("/post/999/studio").status_code == 404


def test_studio_post_saves_final_caption_revision_and_brand_coach(
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

    set_studio_helper(app, monkeypatch, "build_brand_context", fake_context)
    set_studio_helper(app, monkeypatch, "update_brand_coach", fake_coach)
    login(client, user)

    response = client.post(
        f"/post/{post.id}/studio",
        data={"final_caption": " Final caption "},
        follow_redirects=True,
    )
    updated = module.db.session.get(Post, post.id)
    revision = PostRevision.query.filter_by(post_id=post.id).one()

    assert response.status_code == 200
    assert "Studio caption saved successfully." in response.get_data(as_text=True)
    assert updated.caption == "Final caption"
    assert updated.improved_caption is None
    assert updated.improved_at is None
    assert revision.caption == "Original caption"
    assert revision.source == "before_studio_save"
    assert context_calls == [user.id]
    assert coach_calls == [(post.id, "Final caption", "BRAND CONTEXT")]


def test_studio_post_blank_caption_preserves_validation_response(client, module):
    user = create_user(module)
    post = create_post(module, user)
    post.caption = "Original"
    module.db.session.commit()
    login(client, user)

    response = client.post(
        f"/post/{post.id}/studio",
        data={"final_caption": " "},
        follow_redirects=True,
    )
    unchanged = module.db.session.get(Post, post.id)

    assert response.status_code == 200
    assert "Final caption cannot be empty." in response.get_data(as_text=True)
    assert unchanged.caption == "Original"
    assert PostRevision.query.count() == 0


def test_studio_post_commit_failure_preserves_current_exception_and_can_roll_back(
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

    set_studio_helper(app, monkeypatch, "build_brand_context", lambda user_id: "")
    set_studio_helper(app, monkeypatch, "update_brand_coach", lambda *args: None)

    def fail_commit():
        raise RuntimeError("commit failed")

    monkeypatch.setattr(module.db.session, "commit", fail_commit)
    login(client, user)

    with pytest.raises(RuntimeError, match="commit failed"):
        client.post(
            f"/post/{post.id}/studio",
            data={"final_caption": "Final"},
        )

    module.db.session.rollback()
    unchanged = module.db.session.get(Post, post.id)

    assert unchanged.caption == "Original"
    assert unchanged.improved_caption == "Suggested"
    assert PostRevision.query.count() == 0


@pytest.mark.parametrize(
    "action",
    ["hook", "cta", "shorten", "professional", "friendly", "alternatives"],
)
def test_studio_action_supported_actions_call_helper(
    client,
    app,
    module,
    monkeypatch,
    action,
):
    user = create_user(module)
    post = create_post(module, user)
    post.caption = "Original"
    module.db.session.commit()
    rewrite_calls = []
    coach_calls = []

    def fake_rewrite(rewrite_post, brand_context, rewrite_action):
        rewrite_calls.append((rewrite_post.id, rewrite_post.caption, brand_context, rewrite_action))
        return f"{rewrite_action} result"

    set_studio_helper(app, monkeypatch, "build_brand_context", lambda user_id: "BRAND")
    set_studio_helper(app, monkeypatch, "rewrite_caption_with_action", fake_rewrite)
    set_studio_helper(
        app,
        monkeypatch,
        "update_brand_coach",
        lambda coached_post, brand_context: coach_calls.append(
            (coached_post.id, coached_post.caption, brand_context)
        ),
    )
    login(client, user)

    response = client.post(
        f"/post/{post.id}/studio/action/{action}",
        data={"final_caption": " Working caption "},
        follow_redirects=True,
    )
    updated = module.db.session.get(Post, post.id)

    assert response.status_code == 200
    assert "AI Studio action completed." in response.get_data(as_text=True)
    assert rewrite_calls == [(post.id, "Working caption", "BRAND", action)]
    assert coach_calls == [(post.id, "Working caption", "BRAND")]
    assert updated.caption == "Working caption"
    assert updated.improved_caption == f"{action} result"
    assert updated.improved_at is not None


def test_studio_action_invalid_action_does_not_call_ai(client, app, module, monkeypatch):
    user = create_user(module)
    post = create_post(module, user)
    calls = []
    set_studio_helper(app, monkeypatch, "rewrite_caption_with_action", lambda *args: calls.append(args))
    login(client, user)

    response = client.post(
        f"/post/{post.id}/studio/action/not-real",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Invalid studio action." in response.get_data(as_text=True)
    assert calls == []


def test_studio_action_helper_failure_preserves_existing_error_response(
    client,
    app,
    module,
    monkeypatch,
):
    user = create_user(module)
    post = create_post(module, user)
    post.caption = "Original"
    module.db.session.commit()

    def fail_rewrite(*args):
        raise RuntimeError("AI failed")

    set_studio_helper(app, monkeypatch, "build_brand_context", lambda user_id: "")
    set_studio_helper(app, monkeypatch, "rewrite_caption_with_action", fail_rewrite)
    login(client, user)

    response = client.post(
        f"/post/{post.id}/studio/action/hook",
        follow_redirects=True,
    )
    unchanged = module.db.session.get(Post, post.id)

    assert response.status_code == 200
    assert "Failed to run studio action: AI failed" in response.get_data(as_text=True)
    assert unchanged.caption == "Original"
    assert unchanged.improved_caption is None


def test_studio_regrade_updates_grade_and_brand_coach(client, app, module, monkeypatch):
    user = create_user(module)
    post = create_post(module, user)
    post.caption = "Original"
    module.db.session.commit()
    grade_calls = []
    coach_calls = []

    def fake_grade(graded_post, brand_context):
        grade_calls.append((graded_post.id, graded_post.caption, brand_context))
        return "Score: 8/10"

    set_studio_helper(app, monkeypatch, "build_brand_context", lambda user_id: "BRAND")
    set_studio_helper(app, monkeypatch, "grade_post_with_ai", fake_grade)
    set_studio_helper(app, monkeypatch, "extract_overall_score", lambda result: 8.0)
    set_studio_helper(
        app,
        monkeypatch,
        "update_brand_coach",
        lambda coached_post, brand_context: coach_calls.append(
            (coached_post.id, coached_post.caption, brand_context)
        ),
    )
    login(client, user)

    response = client.post(
        f"/post/{post.id}/studio/regrade",
        data={"final_caption": " Regraded caption "},
        follow_redirects=True,
    )
    updated = module.db.session.get(Post, post.id)

    assert response.status_code == 200
    assert "Studio caption regraded successfully." in response.get_data(as_text=True)
    assert updated.caption == "Regraded caption"
    assert updated.grade_result == "Score: 8/10"
    assert updated.grade_score == 8.0
    assert updated.graded_at is not None
    assert grade_calls == [(post.id, "Regraded caption", "BRAND")]
    assert coach_calls == [(post.id, "Regraded caption", "BRAND")]


def test_studio_regrade_failure_rolls_back_partial_caption(client, app, module, monkeypatch):
    user = create_user(module)
    post = create_post(module, user)
    post.caption = "Original"
    module.db.session.commit()

    def fail_grade(*args):
        raise RuntimeError("grade failed")

    set_studio_helper(app, monkeypatch, "build_brand_context", lambda user_id: "")
    set_studio_helper(app, monkeypatch, "grade_post_with_ai", fail_grade)
    login(client, user)

    response = client.post(
        f"/post/{post.id}/studio/regrade",
        data={"final_caption": "Changed"},
        follow_redirects=True,
    )
    unchanged = module.db.session.get(Post, post.id)

    assert response.status_code == 200
    assert "Failed to regrade caption: grade failed" in response.get_data(as_text=True)
    assert unchanged.caption == "Original"
    assert unchanged.grade_result is None
    assert unchanged.grade_score is None


def test_restore_revision_restores_caption_and_saves_current_revision(
    client,
    app,
    module,
    monkeypatch,
):
    user = create_user(module)
    post = create_post(module, user)
    post.caption = "Current caption"
    post.improved_caption = "Suggested"
    post.improved_at = datetime(2026, 7, 10, 8, 0)
    module.db.session.commit()
    revision = create_revision(
        module,
        post,
        user,
        version_number=1,
        caption="Restored caption",
    )
    coach_calls = []

    set_studio_helper(app, monkeypatch, "build_brand_context", lambda user_id: "BRAND")
    set_studio_helper(
        app,
        monkeypatch,
        "update_brand_coach",
        lambda coached_post, brand_context: coach_calls.append(
            (coached_post.id, coached_post.caption, brand_context)
        ),
    )
    login(client, user)

    response = client.post(
        f"/post/{post.id}/revision/{revision.id}/restore",
        follow_redirects=True,
    )
    updated = module.db.session.get(Post, post.id)
    saved_revision = PostRevision.query.filter_by(
        post_id=post.id,
        source="before_revision_restore",
    ).one()

    assert response.status_code == 200
    assert "Version 1 restored." in response.get_data(as_text=True)
    assert updated.caption == "Restored caption"
    assert updated.improved_caption is None
    assert updated.improved_at is None
    assert saved_revision.caption == "Current caption"
    assert coach_calls == [(post.id, "Restored caption", "BRAND")]


def test_restore_revision_blocks_cross_user_and_mismatched_revision(client, module):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    owner_post = create_post(module, owner)
    other_post = create_post(module, other)
    other_revision = create_revision(
        module,
        other_post,
        other,
        version_number=1,
        caption="Other caption",
    )
    login(client, owner)

    assert client.post(
        f"/post/{other_post.id}/revision/{other_revision.id}/restore"
    ).status_code == 404
    assert client.post(
        f"/post/{owner_post.id}/revision/{other_revision.id}/restore"
    ).status_code == 404


def test_restore_revision_commit_failure_preserves_current_exception_and_can_roll_back(
    client,
    app,
    module,
    monkeypatch,
):
    user = create_user(module)
    post = create_post(module, user)
    post.caption = "Current"
    module.db.session.commit()
    revision = create_revision(
        module,
        post,
        user,
        version_number=1,
        caption="Previous",
    )

    set_studio_helper(app, monkeypatch, "build_brand_context", lambda user_id: "")
    set_studio_helper(app, monkeypatch, "update_brand_coach", lambda *args: None)

    def fail_commit():
        raise RuntimeError("commit failed")

    monkeypatch.setattr(module.db.session, "commit", fail_commit)
    login(client, user)

    with pytest.raises(RuntimeError, match="commit failed"):
        client.post(f"/post/{post.id}/revision/{revision.id}/restore")

    module.db.session.rollback()
    unchanged = module.db.session.get(Post, post.id)

    assert unchanged.caption == "Current"
    assert PostRevision.query.filter_by(source="before_revision_restore").count() == 0


def test_studio_helper_bridge_and_model_compatibility(module):
    helpers = module.app.extensions["smu_studio_helpers"]

    for name in {
        "save_post_revision",
        "build_brand_context",
        "update_brand_coach",
        "rewrite_caption_with_action",
        "grade_post_with_ai",
        "extract_overall_score",
    }:
        assert callable(helpers[name])

    assert smu_app.Post is Post
    assert smu_app.BrandBrief is BrandBrief
    assert smu_app.PostRevision is PostRevision


def test_existing_helper_bridges_and_neighbor_routes_remain_registered(module):
    for key in {
        "smu_post_detail_helpers",
        "smu_post_edit_helpers",
        "smu_post_delete_duplicate_helpers",
        "smu_post_create_helpers",
        "smu_post_schedule_helpers",
        "smu_manual_publish_helpers",
        "smu_caption_helpers",
        "smu_ai_editor_helpers",
        "smu_studio_helpers",
    }:
        assert key in module.app.extensions

    for endpoint, path in {
        "ai_editor": "/post/<int:post_id>/ai-editor",
        "view_post": "/post/<int:post_id>",
        "edit_post": "/edit-post/<int:post_id>",
        "create_post": "/create",
        "delete_post": "/delete/<int:post_id>",
        "duplicate_post": "/duplicate-post/<int:post_id>",
        "schedule_post": "/schedule/<int:post_id>",
        "send_to_make": "/send/<int:post_id>",
    }.items():
        rules = rules_for_endpoint(module.app, endpoint)

        assert len(rules) == 1
        assert rules[0].rule == path
