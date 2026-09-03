from flask import Flask

import app as smu_app
from smu_core import create_app
from smu_core.extensions import db, login_manager


def scheduler_job_ids(scheduler):
    try:
        return sorted(job.id for job in scheduler.get_jobs())
    except Exception:
        return []


def test_create_app_returns_flask_app():
    factory_app = create_app({"TESTING": True})

    assert isinstance(factory_app, Flask)


def test_create_app_uses_shared_extensions():
    factory_app = create_app({"TESTING": True})

    assert factory_app.extensions["sqlalchemy"] is db
    assert factory_app.login_manager is login_manager


def test_create_app_accepts_test_config_override():
    factory_app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SQLALCHEMY_ENGINE_OPTIONS": {},
            "SECRET_KEY": "factory-test-secret",
            "FACTORY_TEST_MARKER": "factory-override",
        }
    )

    assert factory_app.config["TESTING"] is True
    assert factory_app.config["SQLALCHEMY_DATABASE_URI"] == "sqlite:///:memory:"
    assert factory_app.config["SECRET_KEY"] == "factory-test-secret"
    assert factory_app.config["FACTORY_TEST_MARKER"] == "factory-override"


def test_app_compatibility_exports_same_app_and_extensions(module):
    assert smu_app.app is module.app
    assert smu_app.db is db
    assert smu_app.login_manager is login_manager
    assert smu_app.User is module.User
    assert smu_app.Post is module.Post


def test_critical_endpoint_names_remain_registered(module):
    endpoints = {rule.endpoint for rule in module.app.url_map.iter_rules()}

    assert {
        "index",
        "create_post",
        "calendar_view",
        "calendar_events",
        "post_studio",
        "connected_accounts",
        "login",
        "logout",
    }.issubset(endpoints)


def test_model_metadata_available_after_compatibility_startup(module):
    assert "user" in module.db.metadata.tables
    assert "post" in module.db.metadata.tables
    assert "post_revision" in module.db.metadata.tables
    assert "scheduler_lease" in module.db.metadata.tables


def test_create_app_does_not_change_scheduler_jobs(module):
    before = scheduler_job_ids(module.scheduler)

    create_app({"TESTING": True})

    after = scheduler_job_ids(module.scheduler)
    assert after == before


def test_import_app_does_not_create_duplicate_extension_instances(module):
    assert module.db is db
    assert module.login_manager is login_manager
