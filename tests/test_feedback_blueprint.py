from flask import url_for

import app as smu_app
from conftest import create_user, login
from smu_core.models import Feedback


def rules_for(app, path):
    return [rule for rule in app.url_map.iter_rules() if rule.rule == path]


def test_feedback_blueprint_is_registered_once(module):
    assert "feedback" in module.app.blueprints
    assert list(module.app.blueprints).count("feedback") == 1


def test_feedback_route_preserves_endpoint_and_methods(module):
    rules = rules_for(module.app, "/feedback")

    assert len(rules) == 1
    assert rules[0].endpoint == "submit_feedback"
    assert {"POST"}.issubset(rules[0].methods)


def test_feedback_url_for_compatibility(module):
    with module.app.test_request_context():
        assert url_for("submit_feedback") == "/feedback"


def test_feedback_requires_login_for_json_submission(client):
    response = client.post("/feedback", json={"message": "Hello"})

    assert response.status_code == 302
    assert "/login" in response.location


def test_feedback_json_submission_creates_row_for_current_user(client, module):
    user = create_user(module, email="feedback-json@example.com")
    login(client, user)

    response = client.post(
        "/feedback",
        json={
            "message": "The beta checklist helped.",
            "page_url": "/calendar",
        },
    )
    feedback = module.Feedback.query.filter_by(user_id=user.id).first()

    assert response.status_code == 200
    assert response.get_json() == {"success": True}
    assert feedback is not None
    assert feedback.message == "The beta checklist helped."
    assert feedback.page_url == "/calendar"
    assert feedback.created_at is not None


def test_feedback_json_blank_message_preserves_error_response(client, module):
    user = create_user(module, email="feedback-blank@example.com")
    login(client, user)

    response = client.post("/feedback", json={"message": "", "page_url": "/calendar"})

    assert response.status_code == 400
    assert response.get_json() == {"error": "Feedback message is required."}
    assert module.Feedback.query.count() == 0


def test_feedback_form_submission_preserves_redirect_flash_and_storage(client, module):
    user = create_user(module, email="feedback-form@example.com")
    login(client, user)

    response = client.post(
        "/feedback",
        data={
            "message": "  Useful feedback  ",
            "page_url": "/dashboard",
        },
        headers={"Referer": "/calendar"},
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)
    feedback = module.Feedback.query.filter_by(user_id=user.id).first()

    assert response.status_code == 200
    assert "Thanks for the feedback." in html
    assert feedback is not None
    assert feedback.message == "Useful feedback"
    assert feedback.page_url == "/dashboard"


def test_feedback_form_blank_message_preserves_redirect_flash_and_no_write(client, module):
    user = create_user(module, email="feedback-form-blank@example.com")
    login(client, user)

    response = client.post(
        "/feedback",
        data={"message": "", "page_url": "/dashboard"},
        headers={"Referer": "/calendar"},
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Please enter feedback before sending." in html
    assert module.Feedback.query.count() == 0


def test_feedback_page_url_is_truncated_to_existing_limit(client, module):
    user = create_user(module, email="feedback-truncate@example.com")
    login(client, user)
    long_url = "/" + ("a" * 600)

    response = client.post(
        "/feedback",
        json={"message": "URL length check", "page_url": long_url},
    )
    feedback = module.Feedback.query.filter_by(user_id=user.id).first()

    assert response.status_code == 200
    assert feedback is not None
    assert feedback.page_url == long_url[:500]


def test_feedback_model_and_app_import_compatibility_remain(module):
    assert smu_app.Feedback is Feedback
    assert module.Feedback is Feedback


def test_public_auth_beta_and_unrelated_endpoints_remain_registered(module):
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
        "beta_apply",
        "admin_beta",
        "index",
        "calendar_view",
        "create_post",
        "post_studio",
        "send_to_make",
    }.issubset(endpoints)


def test_scheduler_reference_is_untouched(module):
    assert module.scheduler is smu_app.scheduler
