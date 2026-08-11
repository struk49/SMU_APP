from contextlib import contextmanager

from flask import template_rendered, url_for

import app as smu_app
from conftest import create_user, login
from smu_core.models import BetaApplication


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


def rules_for(app, path):
    return [rule for rule in app.url_map.iter_rules() if rule.rule == path]


def test_beta_blueprint_is_registered_once(module):
    assert "beta" in module.app.blueprints
    assert list(module.app.blueprints).count("beta") == 1


def test_beta_routes_preserve_old_endpoints_and_methods(module):
    expected = {
        "/beta/apply": ("beta_apply", {"GET", "POST"}),
        "/admin/beta": ("admin_beta", {"GET"}),
        "/admin/beta/<int:application_id>/status": (
            "update_beta_application_status",
            {"POST"},
        ),
    }

    for path, (endpoint, methods) in expected.items():
        rules = rules_for(module.app, path)

        assert len(rules) == 1
        assert rules[0].endpoint == endpoint
        assert methods.issubset(rules[0].methods)


def test_beta_url_for_compatibility(module):
    with module.app.test_request_context():
        assert url_for("beta_apply") == "/beta/apply"
        assert url_for("admin_beta") == "/admin/beta"
        assert url_for("update_beta_application_status", application_id=7) == (
            "/admin/beta/7/status"
        )


def test_beta_apply_get_preserves_template_and_public_access(client, app):
    with captured_templates(app) as templates:
        response = client.get("/beta/apply")

    assert response.status_code == 200
    assert templates[0][0] == "beta_apply.html"


def test_beta_apply_post_creates_application_and_redirects(client, module):
    response = client.post(
        "/beta/apply",
        data={
            "name": "  Andrew  ",
            "email": "  ANDREW@example.COM  ",
            "primary_platform": "Instagram",
            "posting_frequency": "6-15 posts",
            "challenge": "  Planning content consistently.  ",
            "consent": "on",
        },
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)
    application = module.BetaApplication.query.filter_by(
        email="andrew@example.com"
    ).first()

    assert response.status_code == 200
    assert "private beta application has been received" in html
    assert application is not None
    assert application.name == "Andrew"
    assert application.email == "andrew@example.com"
    assert application.primary_platform == "Instagram"
    assert application.posting_frequency == "6-15 posts"
    assert application.challenge == "Planning content consistently."
    assert application.consent is True
    assert application.status == "new"
    assert application.created_at is not None


def test_beta_apply_validation_preserves_400_and_no_database_write(client, module):
    response = client.post("/beta/apply", data={})
    html = response.get_data(as_text=True)

    assert response.status_code == 400
    assert module.BetaApplication.query.count() == 0
    assert "Name is required." in html
    assert "A valid email is required." in html
    assert "Primary platform is required." in html
    assert "Posting frequency is required." in html
    assert "Tell us your biggest content challenge." in html
    assert "Consent is required for beta-related emails." in html


def test_beta_apply_duplicate_submission_remains_blocked(client, module):
    module.db.session.add(
        module.BetaApplication(
            name="Existing",
            email="duplicate@example.com",
            primary_platform="Instagram",
            posting_frequency="1-5 posts",
            challenge="Need help.",
            consent=True,
        )
    )
    module.db.session.commit()

    response = client.post(
        "/beta/apply",
        data={
            "name": "Duplicate",
            "email": "duplicate@example.com",
            "primary_platform": "Facebook",
            "posting_frequency": "6-15 posts",
            "challenge": "Need more help.",
            "consent": "on",
        },
    )

    assert response.status_code == 400
    assert module.BetaApplication.query.count() == 1
    assert "already exists" in response.get_data(as_text=True)


def test_admin_beta_requires_login(client):
    response = client.get("/admin/beta")

    assert response.status_code == 302
    assert "/login" in response.location


def test_admin_beta_rejects_non_admin_user(client, module):
    user = create_user(module, email="user@example.com")
    login(client, user)

    response = client.get("/admin/beta")

    assert response.status_code == 404


def test_admin_beta_renders_applications_and_feedback(client, module, app):
    app.config["SMU_ADMIN_EMAILS"] = {"admin@example.com"}
    admin = create_user(module, email="admin@example.com")
    login(client, admin)
    module.db.session.add(
        module.BetaApplication(
            name="Admin Visible Applicant",
            email="visible@example.com",
            primary_platform="Pinterest",
            posting_frequency="16-30 posts",
            challenge="Planning ahead.",
            consent=True,
        )
    )
    module.db.session.add(
        module.Feedback(
            user_id=admin.id,
            message="Nice dashboard.",
            page_url="/",
        )
    )
    module.db.session.commit()

    with captured_templates(app) as templates:
        response = client.get("/admin/beta")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert templates[0][0] == "admin_beta.html"
    assert "Admin Visible Applicant" in html
    assert "Pinterest" in html
    assert "Nice dashboard." in html


def test_admin_can_update_beta_application_status(client, module, app):
    app.config["SMU_ADMIN_EMAILS"] = {"admin@example.com"}
    admin = create_user(module, email="admin@example.com")
    application = module.BetaApplication(
        name="Approve Me",
        email="approve-me@example.com",
        primary_platform="LinkedIn",
        posting_frequency="6-15 posts",
        challenge="Planning ahead.",
        consent=True,
    )
    module.db.session.add(application)
    module.db.session.commit()
    login(client, admin)

    response = client.post(
        f"/admin/beta/{application.id}/status",
        data={"status": "approved"},
        follow_redirects=True,
    )
    saved_application = module.db.session.get(module.BetaApplication, application.id)

    assert response.status_code == 200
    assert "Beta application status updated." in response.get_data(as_text=True)
    assert saved_application.status == "approved"


def test_admin_status_update_rejects_non_admin(client, module):
    user = create_user(module, email="user-status@example.com")
    application = module.BetaApplication(
        name="Hidden",
        email="hidden@example.com",
        primary_platform="Instagram",
        posting_frequency="1-5 posts",
        challenge="Planning.",
        consent=True,
    )
    module.db.session.add(application)
    module.db.session.commit()
    login(client, user)

    response = client.post(
        f"/admin/beta/{application.id}/status",
        data={"status": "approved"},
    )

    assert response.status_code == 404
    assert module.db.session.get(module.BetaApplication, application.id).status == "new"


def test_beta_application_model_compatibility_remains(module):
    assert smu_app.BetaApplication is BetaApplication
    assert module.BetaApplication is BetaApplication


def test_public_auth_and_unrelated_endpoints_remain_registered(module):
    endpoints = {rule.endpoint for rule in module.app.url_map.iter_rules()}

    assert {
        "landing_page",
        "privacy_policy",
        "terms_of_service",
        "maintenance",
        "help_centre",
        "contact",
        "register",
        "login",
        "logout",
        "index",
        "calendar_view",
        "create_post",
        "post_studio",
        "connected_accounts",
        "submit_feedback",
    }.issubset(endpoints)
