from contextlib import contextmanager

from flask import template_rendered, url_for

import app as smu_app


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


def test_contact_route_is_registered_once_with_old_endpoint(module):
    rules = rules_for(module.app, "/contact")

    assert len(rules) == 1
    assert rules[0].endpoint == "contact"
    assert {"GET", "POST"}.issubset(rules[0].methods)
    assert "public" in module.app.blueprints
    assert list(module.app.blueprints).count("public") == 1


def test_contact_old_endpoint_name_still_resolves(module):
    with module.app.test_request_context():
        assert url_for("contact") == "/contact"


def test_contact_get_preserves_template_and_public_access(client, app):
    with captured_templates(app) as templates:
        response = client.get("/contact")

    assert response.status_code == 200
    assert templates[0][0] == "contact.html"


def test_contact_valid_submission_creates_one_message_and_redirects(client, module):
    response = client.post(
        "/contact",
        data={
            "name": "  Andrew  ",
            "email": "  ANDREW@example.COM  ",
            "message": "  Hello from contact.  ",
        },
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)
    messages = module.ContactMessage.query.all()

    assert response.status_code == 200
    assert "Thanks. Your message has been received." in html
    assert len(messages) == 1
    assert messages[0].name == "Andrew"
    assert messages[0].email == "andrew@example.com"
    assert messages[0].message == "Hello from contact."
    assert messages[0].created_at is not None


def test_contact_duplicate_submissions_remain_allowed(client, module):
    payload = {
        "name": "Andrew",
        "email": "andrew@example.com",
        "message": "Duplicate check",
    }

    first_response = client.post("/contact", data=payload)
    second_response = client.post("/contact", data=payload)

    assert first_response.status_code == 302
    assert second_response.status_code == 302
    assert module.ContactMessage.query.count() == 2


def test_contact_invalid_submission_returns_400_and_creates_no_message(client, app, module):
    with captured_templates(app) as templates:
        response = client.post(
            "/contact",
            data={
                "name": "",
                "email": "not-an-email",
                "message": "",
            },
        )

    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert templates[0][0] == "contact.html"
    assert module.ContactMessage.query.count() == 0
    assert "Name is required." in html
    assert "A valid email is required." in html
    assert "Message is required." in html


def test_contact_length_validation_remains_unchanged(client, module):
    response = client.post(
        "/contact",
        data={
            "name": "A" * 121,
            "email": "valid@example.com",
            "message": "B" * 2001,
        },
    )

    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert module.ContactMessage.query.count() == 0
    assert "Name or email is too long." in html
    assert "Message must be 2000 characters or fewer." in html


def test_contact_model_and_app_import_compatibility_remain(module):
    from smu_core.models import ContactMessage

    assert smu_app.app
    assert smu_app.ContactMessage is ContactMessage
    assert module.ContactMessage is ContactMessage


def test_auth_and_public_endpoint_names_remain_registered(module):
    endpoints = {rule.endpoint for rule in module.app.url_map.iter_rules()}

    assert {
        "register",
        "login",
        "logout",
        "landing_page",
        "privacy_policy",
        "terms_of_service",
        "maintenance",
        "help_centre",
    }.issubset(endpoints)


def test_critical_unrelated_endpoints_remain_registered(module):
    endpoints = {rule.endpoint for rule in module.app.url_map.iter_rules()}

    assert {
        "index",
        "beta_apply",
        "admin_beta",
        "submit_feedback",
        "calendar_view",
        "create_post",
        "post_studio",
        "connected_accounts",
    }.issubset(endpoints)


def test_scheduler_reference_is_untouched(module):
    assert module.scheduler is smu_app.scheduler
