from flask import template_rendered, url_for


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
    assert "private beta" in html
    assert "AI-powered content workspace" in html
    assert 'href="/beta/apply"' in html
    assert 'href="/privacy"' in html
    assert 'href="/terms"' in html
    assert 'href="/contact"' in html


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
    assert "AI-assisted caption and content creation" in html
    assert "AI image generation" in html
    assert "Brand Briefs" in html
    assert "Content Packs" in html
    assert "TikTok repurposing" in html
    assert "AI Studio and caption improvement" in html
    assert "Content scheduling" in html
    assert "Content calendar" in html
    assert "Connected Accounts" in html
    assert "Instagram publishing" in html
    assert "Facebook publishing" in html
    assert "LinkedIn personal-profile text publishing" in html
    assert "LinkedIn personal-profile single-image publishing" in html
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
