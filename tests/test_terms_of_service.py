from flask import template_rendered


def normalized_text(html):
    return " ".join(html.split())


def test_terms_page_renders_for_anonymous_user(client, app):
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append(template.name)

    template_rendered.connect(record, app)
    try:
        response = client.get("/terms")
    finally:
        template_rendered.disconnect(record, app)

    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert recorded == ["terms.html"]
    assert "SMU Terms of Service" in html
    assert "Last Updated:" in html
    assert "private beta" in html


def test_terms_page_contains_required_sections(client):
    html = client.get("/terms").get_data(as_text=True)

    assert "User Content" in html
    assert "AI Generated Content" in html
    assert "Social Media Publishing" in html
    assert "Acceptable Use" in html
    assert "Third-Party Services" in html
    assert "Consumer Rights" in html
    assert "Governing Law" in html


def test_terms_page_contains_core_legal_positioning(client):
    html = client.get("/terms").get_data(as_text=True)
    text = normalized_text(html)

    assert "You retain ownership of content" in text
    assert "AI features may produce inaccurate" in text
    assert "third-party APIs" in text
    assert "Nothing in these Terms affects rights" in text
    assert "laws of England and Wales" in text


def test_terms_page_links_to_privacy_contact_and_footer_terms(client):
    response = client.get("/terms")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'href="/privacy"' in html
    assert 'href="/contact"' in html
    assert 'href="/terms"' in html
    assert "Privacy Policy" in html
    assert "Terms of Service" in html
    assert "Contact" in html
