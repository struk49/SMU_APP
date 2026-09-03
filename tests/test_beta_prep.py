from datetime import datetime

from conftest import create_post, create_user, login


def test_onboarding_completion_hides_checklist(client, module):
    user = create_user(module)
    module.db.session.add(
        module.BrandBrief(
            user_id=user.id,
            business_name="SMU",
            niche="Social media",
        )
    )
    create_post(
        module,
        user,
        status="sent_to_make",
        scheduled_time=datetime(2026, 7, 10, 8, 0),
    )
    module.db.session.commit()
    login(client, user)

    with client.session_transaction() as session:
        session["content_pack_started"] = True
        session["calendar_viewed"] = True

    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Welcome checklist" not in html
    assert "Connected Platforms" in html


def test_onboarding_progress_shows_when_incomplete(client, module):
    user = create_user(module)
    login(client, user)

    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Welcome checklist" in html
    assert "Set up your workspace, create useful content, then schedule or send it when ready." in html
    assert "beta-ready" not in html.lower()
    assert "0%" in html


def test_empty_states_render_clear_calls_to_action(client, module):
    user = create_user(module)
    login(client, user)

    dashboard_response = client.get("/")
    brand_response = client.get("/brand-brief")
    content_response = client.get("/content-pack")
    calendar_response = client.get("/calendar")

    assert "Start with an idea" in dashboard_response.get_data(as_text=True)
    assert "Generate Content Pack" in dashboard_response.get_data(as_text=True)
    assert "Create Post" in dashboard_response.get_data(as_text=True)
    assert "No Brand Brief yet" in brand_response.get_data(as_text=True)
    assert "Create Brand Brief" in brand_response.get_data(as_text=True)
    assert "No Content Pack generated yet" in content_response.get_data(as_text=True)
    assert "Start Content Pack" in content_response.get_data(as_text=True)
    assert "No scheduled posts in this month yet" in calendar_response.get_data(
        as_text=True
    )
    assert "Create Scheduled Post" in calendar_response.get_data(as_text=True)


def test_help_page_requires_login(client):
    response = client.get("/help")

    assert response.status_code == 302
    assert "/login" in response.location


def test_help_page_renders_sections(client, module):
    user = create_user(module)
    login(client, user)

    response = client.get("/help")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Quick answers for getting around SMU." in html
    assert "SMU Beta" not in html

    for heading in [
        "Getting Started",
        "Brand Briefs",
        "Content Packs",
        "Scheduling",
        "Calendar",
        "Publishing",
    ]:
        assert heading in html


def test_feedback_endpoint_stores_feedback(client, module):
    user = create_user(module)
    login(client, user)

    response = client.post(
        "/feedback",
        json={
            "message": "The getting started checklist helped.",
            "page_url": "/calendar",
        },
    )
    feedback = module.Feedback.query.filter_by(user_id=user.id).first()

    assert response.status_code == 200
    assert response.get_json() == {"success": True}
    assert feedback is not None
    assert feedback.message == "The getting started checklist helped."
    assert feedback.page_url == "/calendar"


def test_feedback_endpoint_rejects_blank_message(client, module):
    user = create_user(module)
    login(client, user)

    response = client.post("/feedback", json={"message": ""})

    assert response.status_code == 400
    assert response.get_json()["error"] == "Feedback message is required."
