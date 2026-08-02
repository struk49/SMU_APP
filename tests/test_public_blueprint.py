from contextlib import contextmanager
from urllib.parse import parse_qs, urlparse

import app as smu_app
from conftest import create_user, login
from flask import template_rendered, url_for


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


def test_public_blueprint_is_registered_once(module):
    assert "public" in module.app.blueprints
    assert list(module.app.blueprints).count("public") == 1


def test_landing_route_preserves_status_and_template(client, app):
    with captured_templates(app) as templates:
        response = client.get("/landing")

    assert response.status_code == 200
    assert templates[0][0] == "landing.html"


def test_privacy_route_preserves_status_and_template(client, app):
    with captured_templates(app) as templates:
        response = client.get("/privacy")

    assert response.status_code == 200
    assert templates[0][0] == "privacy.html"


def test_terms_route_preserves_status_and_template(client, app):
    with captured_templates(app) as templates:
        response = client.get("/terms")

    assert response.status_code == 200
    assert templates[0][0] == "terms.html"


def test_maintenance_route_preserves_503_and_template(client, app):
    with captured_templates(app) as templates:
        response = client.get("/maintenance")

    assert response.status_code == 503
    assert templates[0][0] == "maintenance.html"


def test_help_route_requires_login(client):
    response = client.get("/help")

    assert response.status_code == 302
    assert "/login" in response.location
    next_values = parse_qs(urlparse(response.location).query).get("next")
    if next_values:
        assert next_values == ["/help"]


def test_help_route_renders_for_logged_in_user(client, app, module):
    user = create_user(module)
    login(client, user)

    with captured_templates(app) as templates:
        authenticated_response = client.get("/help")

    assert authenticated_response.status_code == 200
    assert templates[0][0] == "help.html"


def test_old_endpoint_names_still_resolve(module):
    with module.app.test_request_context():
        assert url_for("landing_page") == "/landing"
        assert url_for("privacy_policy") == "/privacy"
        assert url_for("terms_of_service") == "/terms"
        assert url_for("maintenance") == "/maintenance"
        assert url_for("help_centre") == "/help"


def test_no_duplicate_public_url_rules(module):
    for path in ["/landing", "/privacy", "/terms", "/maintenance", "/help"]:
        assert len(rules_for(module.app, path)) == 1


def test_critical_unrelated_endpoints_remain_registered(module):
    endpoints = {rule.endpoint for rule in module.app.url_map.iter_rules()}

    assert {
        "index",
        "contact",
        "beta_apply",
        "login",
        "calendar_view",
        "create_post",
        "post_studio",
    }.issubset(endpoints)


def test_app_import_compatibility_remains():
    assert smu_app.app
    assert smu_app.db
    assert smu_app.login_manager
