from flask import template_rendered


def test_privacy_page_renders_for_anonymous_user(client, app):
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append(template.name)

    template_rendered.connect(record, app)
    try:
        response = client.get("/privacy")
    finally:
        template_rendered.disconnect(record, app)

    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert recorded == ["privacy.html"]
    assert "SMU Privacy Policy" in html
    assert "Last Updated:" in html
    assert "Contents" in html
    assert "Information We Collect" in html
    lowered = html.lower()
    assert "private beta" not in lowered
    assert "join beta" not in lowered
    assert "beta access" not in lowered


def test_privacy_page_footer_contains_privacy_policy_link(client):
    response = client.get("/privacy")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'href="/privacy"' in html
    assert "Privacy Policy" in html
