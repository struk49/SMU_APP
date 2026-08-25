from pathlib import Path

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

import app as smu_app
from conftest import create_accounts, create_post, create_user, login
from smu_core.models import User


EXPECTED_COLUMNS = [
    "id",
    "email",
    "password_hash",
    "created_at",
    "stripe_customer_id",
    "stripe_subscription_id",
    "subscription_status",
    "subscription_current_period_end",
    "subscription_cancel_at_period_end",
]


def test_user_model_remains_compatible(module):
    assert smu_app.User is User
    assert module.User is User
    assert issubclass(User, UserMixin)
    assert User.__table__.name == "user"
    assert list(User.__table__.columns.keys()) == EXPECTED_COLUMNS
    assert "user" in module.db.metadata.tables
    assert User.__table__.c.email.unique is True
    assert User.__table__.c.email.nullable is False
    assert User.__table__.c.password_hash.nullable is False
    assert User.__table__.c.stripe_customer_id.nullable is True
    assert User.__table__.c.stripe_subscription_id.nullable is True
    assert User.__table__.c.subscription_status.nullable is True
    assert User.__table__.c.subscription_current_period_end.nullable is True
    assert User.__table__.c.subscription_cancel_at_period_end.nullable is False


def test_subscription_cancel_boolean_patch_uses_database_safe_default():
    app_source = Path(smu_app.__file__).read_text(encoding="utf-8")
    alter_sql = next(
        line
        for line in app_source.splitlines()
        if '"subscription_cancel_at_period_end":' in line
    )

    assert "BOOLEAN DEFAULT FALSE NOT NULL" in alter_sql
    assert "BOOLEAN DEFAULT 0" not in alter_sql


def test_user_loader_returns_correct_user(app, module):
    with app.app_context():
        user = create_user(module)

        loaded_user = module.load_user(str(user.id))

        assert loaded_user is user
        assert loaded_user.email == user.email
        assert loaded_user.get_id() == str(user.id)


def test_registration_creates_hashed_user_and_logs_in(client, module):
    module.db.session.add(
        module.BetaApplication(
            name="Approved User",
            email="new@example.com",
            primary_platform="LinkedIn",
            posting_frequency="6-15 posts",
            challenge="Planning content.",
            consent=True,
            status="approved",
        )
    )
    module.db.session.commit()

    response = client.post(
        "/register",
        data={
            "email": "new@example.com",
            "password": "secret-password",
            "confirm_password": "secret-password",
        },
    )
    user = module.User.query.filter_by(email="new@example.com").first()

    assert response.status_code == 302
    assert response.location.endswith("/")
    assert user is not None
    assert user.password_hash != "secret-password"
    assert check_password_hash(user.password_hash, "secret-password")
    with client.session_transaction() as session:
        assert session["_user_id"] == str(user.id)


def test_login_succeeds_with_valid_credentials(client, module):
    user = module.User(
        email="login@example.com",
        password_hash=generate_password_hash("correct-password"),
    )
    module.db.session.add(user)
    module.db.session.commit()

    response = client.post(
        "/login",
        data={"email": "login@example.com", "password": "correct-password"},
    )

    assert response.status_code == 302
    assert response.location.endswith("/")
    with client.session_transaction() as session:
        assert session["_user_id"] == str(user.id)


def test_login_rejects_invalid_credentials(client, module):
    user = module.User(
        email="reject@example.com",
        password_hash=generate_password_hash("correct-password"),
    )
    module.db.session.add(user)
    module.db.session.commit()

    response = client.post(
        "/login",
        data={"email": "reject@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 302
    assert response.location.endswith("/login")
    with client.session_transaction() as session:
        assert "_user_id" not in session


def test_logout_clears_session(client, module):
    user = create_user(module)
    login(client, user)

    response = client.get("/logout")

    assert response.status_code == 302
    assert response.location.endswith("/login")
    with client.session_transaction() as session:
        assert "_user_id" not in session


def test_public_root_renders_landing_for_unauthenticated_users(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "Turn one idea into content for every platform." in response.get_data(as_text=True)


def test_user_relationships_still_work(app, module):
    with app.app_context():
        user = create_user(module)
        post = create_post(module, user)
        brief = module.BrandBrief(user_id=user.id, business_name="Relationship Brand")
        accounts = create_accounts(module, user, instagram=True, facebook=False)
        module.db.session.add(brief)
        module.db.session.commit()
        module.db.session.expire_all()

        saved_user = module.db.session.get(module.User, user.id)
        saved_post = module.db.session.get(module.Post, post.id)
        saved_brief = module.BrandBrief.query.filter_by(user_id=user.id).first()
        saved_accounts = module.ConnectedAccount.query.filter_by(user_id=user.id).first()

        assert saved_post in saved_user.posts
        assert saved_post.user is saved_user
        assert saved_user.brand_brief is saved_brief
        assert saved_brief.user is saved_user
        assert saved_user.connected_account is saved_accounts
        assert saved_accounts.user is saved_user
        assert saved_accounts.id == accounts.id


def test_feedback_ownership_still_records_user_id(client, module):
    user = create_user(module)
    login(client, user)

    response = client.post(
        "/feedback",
        data={"message": "Useful feedback", "page_url": "/dashboard"},
    )
    feedback = module.Feedback.query.filter_by(user_id=user.id).first()

    assert response.status_code == 302
    assert feedback is not None
    assert feedback.message == "Useful feedback"


def test_user_cannot_access_or_edit_another_users_post(client, module):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    post = create_post(module, owner)
    login(client, other)

    view_response = client.get(f"/post/{post.id}")
    edit_response = client.post(
        f"/edit-post/{post.id}",
        data={"caption": "Changed by other user", "platforms": "instagram"},
    )
    saved_post = module.db.session.get(module.Post, post.id)

    assert view_response.status_code == 302
    assert view_response.location.endswith("/")
    assert edit_response.status_code == 302
    assert edit_response.location.endswith("/")
    assert saved_post.caption == "Caption"
