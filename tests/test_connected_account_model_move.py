import pytest
from sqlalchemy.exc import IntegrityError

import app as smu_app
from conftest import create_accounts, create_post, create_user, login
from smu_core.models import ConnectedAccount


EXPECTED_COLUMNS = [
    "id",
    "user_id",
    "instagram_connected",
    "facebook_connected",
    "linkedin_connected",
    "pinterest_connected",
    "reddit_connected",
    "x_connected",
    "make_webhook_single",
    "make_webhook_carousel",
    "created_at",
    "updated_at",
]


def test_connected_account_model_remains_compatible(module):
    assert smu_app.ConnectedAccount is ConnectedAccount
    assert module.ConnectedAccount is ConnectedAccount
    assert ConnectedAccount.__table__.name == "connected_account"
    assert list(ConnectedAccount.__table__.columns.keys()) == EXPECTED_COLUMNS
    assert "connected_account" in module.db.metadata.tables
    assert ConnectedAccount.__table__.c.user_id.unique is True
    assert {
        foreign_key.target_fullname
        for foreign_key in ConnectedAccount.__table__.foreign_keys
    } == {"user.id"}


def test_connected_account_unique_user_and_relationship_still_work(app, module):
    with app.app_context():
        user = create_user(module)
        account = module.ConnectedAccount(user_id=user.id, instagram_connected=True)
        module.db.session.add(account)
        module.db.session.commit()
        module.db.session.expire_all()

        saved_user = module.db.session.get(module.User, user.id)
        saved_account = module.ConnectedAccount.query.filter_by(user_id=user.id).first()

        assert saved_user.connected_account is saved_account
        assert saved_account.user is saved_user

        module.db.session.add(module.ConnectedAccount(user_id=user.id))
        with pytest.raises(IntegrityError):
            module.db.session.commit()
        module.db.session.rollback()


def test_settings_accounts_creates_and_updates_existing_row(client, module):
    user = create_user(module)
    login(client, user)

    response = client.get("/settings/accounts")
    accounts = module.ConnectedAccount.query.filter_by(user_id=user.id).all()

    assert response.status_code == 200
    assert len(accounts) == 1

    response = client.post(
        "/settings/accounts",
        data={
            "instagram_connected": "on",
            "facebook_connected": "on",
            "make_webhook_single": "https://make.test/single-updated",
            "make_webhook_carousel": "https://make.test/carousel-updated",
        },
    )
    accounts = module.ConnectedAccount.query.filter_by(user_id=user.id).all()

    assert response.status_code == 302
    assert len(accounts) == 1
    assert accounts[0].instagram_connected is True
    assert accounts[0].facebook_connected is True
    assert accounts[0].pinterest_connected is False
    assert accounts[0].make_webhook_single == "https://make.test/single-updated"
    assert accounts[0].make_webhook_carousel == "https://make.test/carousel-updated"


def test_settings_accounts_isolates_users(client, module):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    owner_accounts = create_accounts(
        module,
        owner,
        single_webhook="https://make.test/owner-single",
        carousel_webhook="https://make.test/owner-carousel",
        instagram=True,
        facebook=False,
    )

    login(client, other)
    get_response = client.get("/settings/accounts")
    post_response = client.post(
        "/settings/accounts",
        data={
            "pinterest_connected": "on",
            "make_webhook_single": "https://make.test/other-single",
            "make_webhook_carousel": "https://make.test/other-carousel",
        },
    )

    owner_saved = module.ConnectedAccount.query.filter_by(user_id=owner.id).first()
    other_saved = module.ConnectedAccount.query.filter_by(user_id=other.id).first()

    assert get_response.status_code == 200
    assert "https://make.test/owner-single" not in get_response.get_data(as_text=True)
    assert post_response.status_code == 302
    assert owner_saved.id == owner_accounts.id
    assert owner_saved.make_webhook_single == "https://make.test/owner-single"
    assert owner_saved.make_webhook_carousel == "https://make.test/owner-carousel"
    assert owner_saved.instagram_connected is True
    assert other_saved.pinterest_connected is True
    assert other_saved.make_webhook_single == "https://make.test/other-single"


def test_platform_and_webhook_resolution_still_use_user_settings(app, module):
    with app.app_context():
        user = create_user(module)
        create_accounts(
            module,
            user,
            single_webhook="https://make.test/user-single",
            carousel_webhook="https://make.test/user-carousel",
            instagram=True,
            facebook=False,
        )

        assert module.get_enabled_platforms_for_user(
            ["instagram", "facebook", "pinterest"],
            user_id=user.id,
        ) == ["instagram"]
        assert module.get_user_make_webhook("single", user_id=user.id) == (
            "https://make.test/user-single"
        )
        assert module.get_user_make_webhook("carousel", user_id=user.id) == (
            "https://make.test/user-carousel"
        )


def test_scheduled_publish_resolves_connected_account_from_post_owner(
    app,
    module,
    monkeypatch,
):
    with app.app_context():
        owner = create_user(module, email="owner@example.com")
        other = create_user(module, email="other@example.com")
        create_accounts(
            module,
            owner,
            single_webhook="https://make.test/owner-single",
            instagram=True,
            facebook=False,
        )
        create_accounts(
            module,
            other,
            single_webhook="https://make.test/other-single",
            instagram=True,
            facebook=False,
        )
        post = create_post(module, owner, platforms="instagram,facebook")
        captured = {}

        def fake_send_payload_to_make(payload, webhook_url=None):
            captured["payload"] = payload
            captured["webhook_url"] = webhook_url
            return object()

        monkeypatch.setattr(module, "send_payload_to_make", fake_send_payload_to_make)

        module.publish_post_to_make(post, user_id=post.user_id)

        assert captured["webhook_url"] == "https://make.test/owner-single"
        assert captured["payload"]["platforms"] == ["instagram"]
