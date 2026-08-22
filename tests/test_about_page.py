from flask import template_rendered, url_for

from conftest import create_user, login


def test_about_page_renders_for_anonymous_user(client, app):
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append(template.name)

    template_rendered.connect(record, app)
    try:
        response = client.get("/about")
    finally:
        template_rendered.disconnect(record, app)

    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert recorded == ["about.html"]
    assert "About SMU" in html
    assert "AI-powered social-media content workspace" in html
    assert "Create, repurpose, improve, schedule and publish social content" in html
    assert "Get Started" in html
    assert 'href="/register"' in html
    assert 'href="/landing#features"' in html
    assert 'href="/pricing"' in html
    assert 'href="/privacy"' in html
    assert 'href="/terms"' in html
    assert 'href="/contact"' in html
    lowered = html.lower()
    assert "join beta" not in lowered
    assert "private beta" not in lowered
    assert "built with beta users" not in lowered


def test_about_route_and_url_for_compatibility(module):
    rules = [rule for rule in module.app.url_map.iter_rules() if rule.rule == "/about"]

    assert len(rules) == 1
    assert rules[0].endpoint == "about_page"
    assert "GET" in rules[0].methods

    with module.app.test_request_context():
        assert url_for("about_page") == "/about"


def test_about_page_lists_current_capabilities_without_overclaiming(client):
    response = client.get("/about")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "AI Content Studio" in html
    assert "AI-assisted caption and content creation" in html
    assert "AI image generation" in html
    assert "Brand Brief" in html
    assert "Brand Coach" in html
    assert "Content Packs" in html
    assert "TikTok repurposing" in html
    assert "Revision history" in html
    assert "Carousel creation" in html
    assert "AI Studio and caption improvement" in html
    assert "Content scheduling" in html
    assert "Content calendar" in html
    assert "Connected Accounts" in html
    assert "Instagram publishing workflows" in html
    assert "Facebook publishing workflows" in html
    assert "LinkedIn personal-profile text and single-image workflows" in html
    assert "Pinterest publishing" not in html
    assert "LinkedIn Page publishing" not in html
    assert "video publishing" not in html


def test_about_future_features_are_clearly_framed(client):
    response = client.get("/about")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Future Direction" in html
    assert "Future direction may include" in html
    assert "AI Research" in html
    assert "Analytics" in html
    assert "AI Memory" in html
    assert "performance is analysed later" in html


def test_about_is_linked_from_public_navigation_and_footer(client):
    response = client.get("/about")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'href="/about"' in html
    assert "About" in html


def test_about_unpaid_logged_in_cta_points_to_pricing(client, app, module):
    app.config["REGISTRATION_MODE"] = "subscription"
    user = create_user(module, email="about-unpaid@example.com")
    login(client, user)

    response = client.get("/about")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Choose Your Plan" in html
    assert 'href="/pricing"' in html
    assert "Join the Beta" not in html
    assert 'href="/beta/apply"' not in html


def test_about_active_logged_in_cta_points_to_dashboard(client, app, module):
    app.config["REGISTRATION_MODE"] = "subscription"
    user = create_user(module, email="about-active@example.com")
    user.subscription_status = "active"
    module.db.session.commit()
    login(client, user)

    response = client.get("/about")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Go to Dashboard" in html
    assert 'href="/"' in html
    assert "Join the Beta" not in html


def test_about_replaces_beta_section_with_real_workflows(client):
    response = client.get("/about")
    html = response.get_data(as_text=True)
    lowered = html.lower()
    normalized = " ".join(html.split())

    assert response.status_code == 200
    assert "Built Around Real Content Workflows" in html
    assert "starting with an idea or existing piece of content" in html
    assert (
        "The product will keep improving based on how people use it "
        "and the feedback they provide."
    ) in normalized
    assert "beta users" not in lowered
    assert "beta access" not in lowered
