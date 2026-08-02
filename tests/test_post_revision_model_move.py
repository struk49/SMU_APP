import app as smu_app
from conftest import create_post, create_user, login
from smu_core.models import PostRevision


EXPECTED_COLUMNS = [
    "id",
    "post_id",
    "user_id",
    "version_number",
    "caption",
    "score",
    "source",
    "created_at",
]


def test_post_revision_model_remains_compatible(module):
    assert smu_app.PostRevision is PostRevision
    assert module.PostRevision is PostRevision
    assert PostRevision.__table__.name == "post_revision"
    assert list(PostRevision.__table__.columns.keys()) == EXPECTED_COLUMNS
    assert "post_revision" in module.db.metadata.tables
    assert {
        foreign_key.target_fullname
        for foreign_key in PostRevision.__table__.foreign_keys
    } == {"post.id", "user.id"}


def test_post_revision_relationships_and_order_still_work(app, module):
    with app.app_context():
        user = create_user(module)
        post = create_post(module, user)
        first = module.save_post_revision(post, source="first")
        post.caption = "Second caption"
        second = module.save_post_revision(post, source="second")
        module.db.session.commit()
        module.db.session.expire_all()

        saved_post = module.db.session.get(module.Post, post.id)
        revisions = module.PostRevision.query.filter_by(
            post_id=saved_post.id,
            user_id=user.id,
        ).order_by(module.PostRevision.version_number.desc()).all()

        assert revisions == [second, first]
        assert revisions[0].post is saved_post
        assert {revision.id for revision in saved_post.revisions} == {first.id, second.id}
        assert first.caption == "Caption"
        assert second.caption == "Second caption"


def test_post_revision_delete_cascade_still_works(app, module):
    with app.app_context():
        user = create_user(module)
        post = create_post(module, user)
        module.save_post_revision(post, source="cascade_check")
        module.db.session.commit()
        post_id = post.id

        module.db.session.delete(post)
        module.db.session.commit()

        assert module.PostRevision.query.filter_by(post_id=post_id).count() == 0


def test_studio_save_still_creates_revision(client, module, monkeypatch):
    user = create_user(module)
    post = create_post(module, user)
    login(client, user)
    monkeypatch.setattr(module, "update_brand_coach", lambda post, brand_context="": {})

    response = client.post(
        f"/post/{post.id}/studio",
        data={"final_caption": "Studio saved caption"},
    )
    revision = module.PostRevision.query.filter_by(
        post_id=post.id,
        user_id=user.id,
    ).first()

    assert response.status_code == 302
    assert response.location.endswith(f"/post/{post.id}/studio")
    assert revision is not None
    assert revision.version_number == 1
    assert revision.caption == "Caption"
    assert revision.source == "before_studio_save"
    assert post.caption == "Studio saved caption"


def test_restore_revision_restores_selected_caption(client, module, monkeypatch):
    user = create_user(module)
    post = create_post(module, user)
    post.caption = "Current caption"
    revision = module.PostRevision(
        post_id=post.id,
        user_id=user.id,
        version_number=1,
        caption="Earlier caption",
        source="manual",
    )
    module.db.session.add(revision)
    module.db.session.commit()
    login(client, user)
    monkeypatch.setattr(module, "update_brand_coach", lambda post, brand_context="": {})

    response = client.post(f"/post/{post.id}/revision/{revision.id}/restore")

    restore_revision = module.PostRevision.query.filter_by(
        post_id=post.id,
        user_id=user.id,
        source="before_revision_restore",
    ).first()

    assert response.status_code == 302
    assert response.location.endswith(f"/post/{post.id}/studio")
    assert post.caption == "Earlier caption"
    assert restore_revision is not None
    assert restore_revision.caption == "Current caption"


def test_user_cannot_restore_another_users_revision(client, module, monkeypatch):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    owner_post = create_post(module, owner)
    other_post = create_post(module, other)
    revision = module.PostRevision(
        post_id=owner_post.id,
        user_id=owner.id,
        version_number=1,
        caption="Owner revision",
        source="manual",
    )
    module.db.session.add(revision)
    module.db.session.commit()
    login(client, other)
    monkeypatch.setattr(module, "update_brand_coach", lambda post, brand_context="": {})

    response = client.post(f"/post/{other_post.id}/revision/{revision.id}/restore")

    assert response.status_code == 404
    assert other_post.caption == "Caption"
    assert owner_post.caption == "Caption"
