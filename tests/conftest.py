import os
import sys
from datetime import timedelta

import pytest
from flask import g, has_app_context


os.environ.setdefault("SECRET_KEY", "test-secret-key")
TEST_DATABASE_URL = "sqlite:///test_publishing.db"

os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL)
os.environ.setdefault("MAKE_WEBHOOK_SINGLE", "")
os.environ.setdefault("MAKE_WEBHOOK_CAROUSEL", "")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import app as smu_app  # noqa: E402
from smu_core.services.time_utils import utc_now  # noqa: E402


try:
    smu_app.scheduler.shutdown(wait=False)
except Exception:
    pass


@pytest.fixture()
def app():
    smu_app.app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=TEST_DATABASE_URL,
        WTF_CSRF_ENABLED=False,
        SMU_ADMIN_EMAILS=set(),
        REGISTRATION_MODE="open",
        STRIPE_PRICE_STARTER="",
        STRIPE_PRICE_PRO="",
        STRIPE_PRICE_BUSINESS="",
        STRIPE_PRICE_ID="",
    )

    with smu_app.app.app_context():
        smu_app.db.drop_all()
        smu_app.db.create_all()
        yield smu_app.app
        smu_app.db.session.remove()
        smu_app.db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def module():
    return smu_app


def create_user(module, email="owner@example.com"):
    user = module.User(
        email=email,
        password_hash="unused",
    )
    module.db.session.add(user)
    module.db.session.commit()
    return user


def login(client, user):
    if has_app_context():
        g.pop("_login_user", None)

    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
        session["_fresh"] = True


def create_accounts(
    module,
    user,
    *,
    single_webhook="https://make.test/single",
    carousel_webhook="https://make.test/carousel",
    instagram=True,
    facebook=False,
):
    accounts = module.ConnectedAccount(
        user_id=user.id,
        instagram_connected=instagram,
        facebook_connected=facebook,
        make_webhook_single=single_webhook,
        make_webhook_carousel=carousel_webhook,
    )
    module.db.session.add(accounts)
    module.db.session.commit()
    return accounts


def create_post(
    module,
    user,
    *,
    status="draft",
    scheduled_time=None,
    group_id=None,
    sort_order=0,
    is_cover=False,
    platforms="instagram,facebook",
    file_url=None,
):
    post = module.Post(
        file_url=(
            file_url
            or f"https://cdn.test/{email_safe(user.email)}-{sort_order}.jpg"
        ),
        file_type="image",
        prompt="Prompt",
        caption="Caption",
        platforms=platforms,
        post_type="carousel" if group_id else "single",
        status=status,
        scheduled_time=scheduled_time,
        group_id=group_id,
        sort_order=sort_order,
        is_cover=is_cover,
        user_id=user.id,
    )
    module.db.session.add(post)
    module.db.session.commit()
    return post


def create_carousel(module, user, *, status="draft", scheduled=False):
    group_id = f"group-{user.id}"
    scheduled_time = utc_now() - timedelta(minutes=1) if scheduled else None
    first = create_post(
        module,
        user,
        status=status,
        scheduled_time=scheduled_time,
        group_id=group_id,
        sort_order=0,
        is_cover=True,
    )
    second = create_post(
        module,
        user,
        status=status,
        scheduled_time=scheduled_time,
        group_id=group_id,
        sort_order=1,
        is_cover=False,
    )
    return group_id, [first, second]


class MockMakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.text = "mock response"

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code} error")


def email_safe(email):
    return email.replace("@", "-").replace(".", "-")
