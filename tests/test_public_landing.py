from urllib.parse import urlparse

from conftest import create_user, login


def rules_for(app, path):
    return [rule for rule in app.url_map.iter_rules() if rule.rule == path]


def internal_links(html):
    links = []
    for part in html.split("href=\"")[1:]:
        href = part.split("\"", 1)[0]
        if href.startswith("/") and not href.startswith("//"):
            links.append(href)
    return links


def test_root_landing_page_is_public(client):
    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Turn One Idea Into Content Everywhere" in html
    assert "Join the Beta" in html


def test_landing_page_endpoint_remains_public(client):
    response = client.get("/landing")

    assert response.status_code == 200
    assert "Turn One Idea Into Content Everywhere" in response.get_data(as_text=True)


def test_landing_ctas_point_to_real_routes(client):
    html = client.get("/").get_data(as_text=True)

    assert 'href="/beta/apply"' in html
    assert 'href="/login"' in html
    assert 'href="#how-it-works"' in html


def test_landing_sections_render_current_product_capabilities(client):
    html = client.get("/").get_data(as_text=True)

    assert "AI Content Creation" in html
    assert "AI Images" in html
    assert "Content Repurposing" in html
    assert "Brand-Aware Content" in html
    assert "Smart Scheduling" in html
    assert "Multi-Platform Publishing" in html
    assert "LinkedIn personal-profile publishing" in html


def test_landing_does_not_require_login(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 200
    assert "/login" not in (response.location or "")


def test_no_duplicate_root_route(module):
    rules = rules_for(module.app, "/")

    assert len(rules) == 1
    assert rules[0].endpoint == "index"


def test_authenticated_root_still_shows_dashboard(client, module):
    user = create_user(module)
    login(client, user)

    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Content Dashboard" in html
    assert "Turn One Idea Into Content Everywhere" not in html


def test_authenticated_landing_shows_dashboard_cta(client, module):
    user = create_user(module)
    login(client, user)

    response = client.get("/landing")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Go to Dashboard" in html
    assert 'href="/"' in html


def test_no_dead_internal_links_introduced(client, module):
    html = client.get("/").get_data(as_text=True)
    rules = {rule.rule for rule in module.app.url_map.iter_rules()}

    for href in internal_links(html):
        path = urlparse(href).path
        if path.startswith("/static/"):
            continue
        assert path in rules


def test_future_features_are_not_claimed_as_available(client):
    html = client.get("/").get_data(as_text=True)

    assert "AI Research" not in html
    assert "AI Memory" not in html
    assert "LinkedIn MultiImage" not in html
    assert "Pinterest <small>Coming soon</small>" in html
