from datetime import datetime, timedelta

import app as smu_app
from conftest import create_accounts, create_carousel, create_post, create_user, login
from smu_core.models import Post
from smu_core.services.time_utils import utc_now


EXPECTED_COLUMNS = [
    "id",
    "file_url",
    "file_type",
    "prompt",
    "caption",
    "status",
    "created_at",
    "sent_at",
    "scheduled_time",
    "group_id",
    "post_type",
    "platforms",
    "sort_order",
    "is_cover",
    "user_id",
    "grade_result",
    "grade_score",
    "graded_at",
    "improved_caption",
    "improved_at",
    "brand_score",
    "brand_feedback",
    "zernio_post_id",
    "zernio_status",
    "zernio_platforms",
    "zernio_published_url",
    "zernio_error",
]


def test_post_model_remains_compatible(module):
    assert smu_app.Post is Post
    assert module.Post is Post
    assert Post.__table__.name == "post"
    assert list(Post.__table__.columns.keys()) == EXPECTED_COLUMNS
    assert "post" in module.db.metadata.tables
    assert {
        foreign_key.target_fullname
        for foreign_key in Post.__table__.foreign_keys
    } == {"user.id"}


def test_post_columns_defaults_and_nullable_settings_are_unchanged(module):
    columns = Post.__table__.c

    assert columns.file_url.nullable is False
    assert columns.file_type.nullable is False
    assert columns.user_id.nullable is True
    assert columns.scheduled_time.nullable is True
    assert columns.group_id.nullable is True
    assert columns.status.default.arg == "draft"
    assert callable(columns.created_at.default.arg)
    created_at_default = columns.created_at.default.arg(None)
    assert isinstance(created_at_default, datetime)
    assert created_at_default.tzinfo is None
    assert columns.post_type.default.arg == "single"
    assert columns.platforms.default.arg == "instagram,facebook"
    assert columns.sort_order.default.arg == 0
    assert columns.is_cover.default.arg is False
    assert columns.grade_result.nullable is True
    assert columns.grade_score.nullable is True
    assert columns.graded_at.nullable is True
    assert columns.improved_caption.nullable is True
    assert columns.improved_at.nullable is True
    assert columns.brand_score.nullable is True
    assert columns.brand_feedback.nullable is True
    assert columns.zernio_post_id.nullable is True
    assert columns.zernio_status.nullable is True
    assert columns.zernio_platforms.nullable is True
    assert columns.zernio_published_url.nullable is True
    assert columns.zernio_error.nullable is True


def test_post_user_revision_relationships_and_cascade_still_work(app, module):
    with app.app_context():
        user = create_user(module)
        post = create_post(module, user)
        revision = module.save_post_revision(post, source="relationship_check")
        module.db.session.commit()
        module.db.session.expire_all()

        saved_user = module.db.session.get(module.User, user.id)
        saved_post = module.db.session.get(module.Post, post.id)
        saved_revision = module.db.session.get(module.PostRevision, revision.id)

        assert saved_post in saved_user.posts
        assert saved_post.user is saved_user
        assert saved_revision in saved_post.revisions
        assert saved_revision.post is saved_post

        module.db.session.delete(saved_post)
        module.db.session.commit()

        assert module.PostRevision.query.filter_by(post_id=post.id).count() == 0


def test_user_can_view_and_edit_own_post(client, module):
    user = create_user(module)
    post = create_post(module, user)
    login(client, user)

    view_response = client.get(f"/post/{post.id}")
    edit_response = client.post(
        f"/edit-post/{post.id}",
        data={
            "caption": "Edited caption",
            "prompt": "Edited prompt",
            "platforms": ["instagram"],
        },
    )

    assert view_response.status_code == 200
    assert edit_response.status_code == 302
    assert edit_response.location.endswith(f"/post/{post.id}")
    assert post.caption == "Edited caption"
    assert post.prompt == "Edited prompt"
    assert post.platforms == "instagram"


def test_other_user_cannot_view_edit_delete_schedule_or_publish_post(
    client,
    module,
    monkeypatch,
):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    post = create_post(module, owner)
    create_accounts(module, other, instagram=True, facebook=False)
    login(client, other)

    sent_payloads = []
    monkeypatch.setattr(
        module,
        "send_payload_to_make",
        lambda payload, webhook_url=None: sent_payloads.append(payload),
    )

    assert client.get(f"/post/{post.id}").status_code == 302
    assert client.post(
        f"/edit-post/{post.id}",
        data={"caption": "Other edit", "prompt": "Other prompt"},
    ).status_code == 302
    assert client.post(
        f"/delete/{post.id}",
    ).status_code == 302
    assert client.post(
        f"/schedule/{post.id}",
        data={"scheduled_time": "2026-07-10T09:00"},
    ).status_code == 404
    assert client.post(f"/send/{post.id}").status_code == 404

    saved_post = module.db.session.get(module.Post, post.id)
    assert saved_post.caption == "Caption"
    assert saved_post.status == "draft"
    assert sent_payloads == []


def test_single_payload_shape_remains_unchanged(app, module):
    user = create_user(module)
    post = create_post(module, user, platforms="instagram")

    payload = module.build_single_payload(post)

    assert payload == {
        "post_type": "single",
        "post_id": post.id,
        "caption": "Caption",
        "prompt": "Prompt",
        "file_url": post.file_url,
        "file_type": "image",
        "platforms": ["instagram"],
    }


def test_carousel_relationship_fields_remain_unchanged(app, module):
    user = create_user(module)
    group_id, posts = create_carousel(module, user)

    ordered_posts = module.get_ordered_carousel_posts(group_id, user_id=user.id)

    assert [post.id for post in ordered_posts] == [post.id for post in posts]
    assert [post.sort_order for post in ordered_posts] == [0, 1]
    assert [post.post_type for post in ordered_posts] == ["carousel", "carousel"]
    assert ordered_posts[0].is_cover is True
    assert ordered_posts[1].is_cover is False


def test_scheduled_due_query_still_finds_due_post(app, module):
    with app.app_context():
        user = create_user(module)
        due_post = create_post(
            module,
            user,
            status="scheduled",
            scheduled_time=utc_now() - timedelta(minutes=1),
        )
        future_post = create_post(
            module,
            user,
            status="scheduled",
            scheduled_time=utc_now() + timedelta(days=1),
        )

        due_posts = module.Post.query.filter(
            module.Post.scheduled_time.isnot(None),
            module.Post.status == "scheduled",
            module.Post.scheduled_time <= utc_now(),
        ).order_by(module.Post.scheduled_time.asc()).all()

        assert due_post in due_posts
        assert future_post not in due_posts
