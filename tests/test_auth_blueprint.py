from contextlib import contextmanager
from urllib.parse import parse_qs, urlparse

from flask import template_rendered, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import app as smu_app
from conftest import create_user, login as login_client


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


def test_auth_blueprint_is_registered_once(module):
    assert "auth" in module.app.blueprints
    assert list(module.app.blueprints).count("auth") == 1


def test_auth_routes_preserve_unqualified_endpoints_and_methods(module):
    expected = {
        "/register": ("register", {"GET", "POST"}),
        "/login": ("login", {"GET", "POST"}),
        "/logout": ("logout", {"GET"}),
    }

    for path, (endpoint, methods) in expected.items():
        rules = rules_for(module.app, path)

        assert len(rules) == 1
        assert rules[0].endpoint == endpoint
        assert methods.issubset(rules[0].methods)


def test_old_auth_endpoint_names_still_resolve(module):
    with module.app.test_request_context():
        assert url_for("register") == "/register"
        assert url_for("login") == "/login"
        assert url_for("logout") == "/logout"


def test_login_manager_still_uses_login_endpoint(module):
    assert module.login_manager.login_view == "login"


def test_register_get_preserves_template(client, app):
    with captured_templates(app) as templates:
        response = client.get("/register")

    assert response.status_code == 200
    assert templates[0][0] == "register.html"


def test_registration_creates_hashed_user_and_logs_in(client, module):
    module.db.session.add(
        module.BetaApplication(
            name="Approved Auth",
            email="new-auth@example.com",
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
            "email": "new-auth@example.com",
            "password": "secret-password",
            "confirm_password": "secret-password",
        },
    )
    user = module.User.query.filter_by(email="new-auth@example.com").first()

    assert response.status_code == 302
    assert response.location.endswith("/")
    assert user is not None
    assert user.password_hash != "secret-password"
    assert check_password_hash(user.password_hash, "secret-password")
    with client.session_transaction() as session:
        assert session["_user_id"] == str(user.id)


def test_duplicate_registration_remains_blocked(client, module):
    create_user(module, email="duplicate-auth@example.com")

    response = client.post(
        "/register",
        data={
            "email": "duplicate-auth@example.com",
            "password": "secret-password",
            "confirm_password": "secret-password",
        },
    )

    assert response.status_code == 302
    assert response.location.endswith("/register")
    assert module.User.query.filter_by(email="duplicate-auth@example.com").count() == 1


def test_registration_validation_redirects_back_to_register(client, module):
    response = client.post(
        "/register",
        data={
            "email": "",
            "password": "secret-password",
            "confirm_password": "secret-password",
        },
    )

    assert response.status_code == 302
    assert response.location.endswith("/register")
    assert module.User.query.count() == 0


def test_unapproved_registration_is_blocked(client, module):
    response = client.post(
        "/register",
        data={
            "email": "unapproved-auth@example.com",
            "password": "secret-password",
            "confirm_password": "secret-password",
        },
    )

    assert response.status_code == 403
    assert "SMU is currently in private beta" in response.get_data(as_text=True)
    assert module.User.query.filter_by(email="unapproved-auth@example.com").first() is None


def test_login_get_preserves_template(client, app):
    with captured_templates(app) as templates:
        response = client.get("/login")

    assert response.status_code == 200
    assert templates[0][0] == "login.html"


def test_valid_login_authenticates_user(client, module):
    user = module.User(
        email="valid-auth@example.com",
        password_hash=generate_password_hash("correct-password"),
    )
    module.db.session.add(user)
    module.db.session.commit()

    response = client.post(
        "/login",
        data={"email": "valid-auth@example.com", "password": "correct-password"},
    )

    assert response.status_code == 302
    assert response.location.endswith("/")
    with client.session_transaction() as session:
        assert session["_user_id"] == str(user.id)


def test_invalid_login_does_not_authenticate(client, module):
    user = module.User(
        email="invalid-auth@example.com",
        password_hash=generate_password_hash("correct-password"),
    )
    module.db.session.add(user)
    module.db.session.commit()

    response = client.post(
        "/login",
        data={"email": "invalid-auth@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 302
    assert response.location.endswith("/login")
    with client.session_transaction() as session:
        assert "_user_id" not in session


def test_login_next_parameter_behaviour_is_preserved(client, module):
    user = module.User(
        email="next-auth@example.com",
        password_hash=generate_password_hash("correct-password"),
    )
    module.db.session.add(user)
    module.db.session.commit()

    response = client.post(
        "/login?next=/calendar",
        data={"email": "next-auth@example.com", "password": "correct-password"},
    )

    assert response.status_code == 302
    assert response.location.endswith("/")


def test_authenticated_register_and_login_redirect_to_dashboard(client, module):
    user = create_user(module, email="already-auth@example.com")
    login_client(client, user)

    register_response = client.get("/register")
    login_response = client.get("/login")

    assert register_response.status_code == 302
    assert register_response.location.endswith("/")
    assert login_response.status_code == 302
    assert login_response.location.endswith("/")


def test_logout_clears_session_and_redirects_to_login(client, module):
    user = create_user(module, email="logout-auth@example.com")
    login_client(client, user)

    response = client.get("/logout")

    assert response.status_code == 302
    assert response.location.endswith("/login")
    with client.session_transaction() as session:
        assert "_user_id" not in session


def test_login_required_redirect_still_points_to_login(client):
    response = client.get("/calendar")

    assert response.status_code == 302
    assert "/login" in response.location
    next_values = parse_qs(urlparse(response.location).query).get("next")
    if next_values:
        assert next_values == ["/calendar"]


def test_user_loader_still_returns_user(app, module):
    with app.app_context():
        user = create_user(module, email="loader-auth@example.com")

        loaded_user = module.load_user(str(user.id))

        assert loaded_user is user


def test_critical_unrelated_endpoints_remain_registered(module):
    endpoints = {rule.endpoint for rule in module.app.url_map.iter_rules()}

    assert {
        "index",
        "landing_page",
        "help_centre",
        "beta_apply",
        "contact",
        "calendar_view",
        "create_post",
        "post_studio",
        "connected_accounts",
    }.issubset(endpoints)


def test_app_import_compatibility_remains():
    assert smu_app.app
    assert smu_app.db
    assert smu_app.login_manager
    assert smu_app.User
