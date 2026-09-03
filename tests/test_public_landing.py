from urllib.parse import urlparse
from pathlib import Path

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
    assert "Turn one idea into social content in minutes" in html
    assert "Social media workspace" in html


def test_landing_page_endpoint_remains_public(client):
    response = client.get("/landing")

    assert response.status_code == 200
    assert "Turn one idea into social content in minutes" in response.get_data(as_text=True)


def test_landing_ctas_point_to_real_routes(client):
    html = client.get("/").get_data(as_text=True)

    assert 'href="/register"' in html
    assert 'href="/login"' in html
    assert 'href="#how-it-works"' in html
    assert 'href="/pricing"' in html


def test_landing_sections_render_current_product_capabilities(client):
    html = client.get("/").get_data(as_text=True)

    assert "Everything you need to turn ideas into content" in html
    assert "Create" in html
    assert "Content Packs" in html
    assert "Publish" in html
    assert "See SMU in action" in html
    assert "Create and manage posts" in html
    assert "Build carousel content" in html
    assert "TikTok Repurpose — Beta" in html
    assert "screenshots/dashboard.png" in html
    assert "screenshots/post-detail.png" in html
    assert "screenshots/carousel.png" in html
    assert "screenshots/tiktok-repurpose.png" in html
    assert "screenshots/brand-brief.png" in html
    assert html.index("screenshots/brand-brief.png") < html.index("screenshots/post-detail.png")
    assert html.index("screenshots/post-detail.png") < html.index("screenshots/carousel.png")
    assert html.index("screenshots/carousel.png") < html.index("screenshots/tiktok-repurpose.png")
    assert "Content Packs" in html
    assert "Brand Brief" in html
    assert "Calendar scheduling" in html
    assert "Connected publishing accounts" in html
    assert "Billing and subscription management" in html
    assert "£9.99/month" in html


def test_landing_uses_bootstrap_responsive_structure(client):
    html = client.get("/").get_data(as_text=True)

    assert "smu-horizontal-logo.png" in html
    assert "https://fonts.googleapis.com" in html
    assert "https://fonts.gstatic.com" in html
    assert "family=Lato:wght@700&family=Roboto:wght@400;500;700&display=swap" in html
    assert 'class="navbar navbar-expand-xl landing-nav"' in html
    assert 'id="landingNav"' in html
    assert "collapse navbar-collapse" in html
    assert "row justify-content-center" in html
    assert "hero-screenshot-viewport rounded-3" in html
    assert "col-12 col-lg-6" in html
    assert "card border-0 shadow-sm rounded-4" in html
    assert "img-fluid w-100 landing-screenshot" in html
    assert "img-fluid w-100 rounded-3 shadow-sm landing-screenshot" in html
    assert "row g-4" in html
    assert "col-12 col-md-6 col-xl-3" in html
    assert html.count("col-12 col-lg-6") >= 4
    assert "list-group list-group-flush rounded-4" in html
    assert "d-grid d-sm-flex" in html
    assert "justify-content-lg-center" in html
    assert "gap-2" in html


def test_landing_uses_shared_roboto_lato_typography():
    css = Path("static/landing.css").read_text(encoding="utf-8")

    assert 'font-family: "Roboto", sans-serif;' in css
    assert 'font-family: "Lato", sans-serif;' in css
    assert "Inter" not in css


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
    assert "Turn one idea into social content in minutes" not in html


def test_authenticated_landing_shows_dashboard_cta(client, module):
    user = create_user(module)
    login(client, user)

    response = client.get("/landing")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Go to Dashboard" in html
    assert 'href="/"' in html


def test_unpaid_authenticated_landing_shows_pricing_cta(client, app, module):
    app.config["REGISTRATION_MODE"] = "subscription"
    user = create_user(module, email="landing-unpaid@example.com")
    login(client, user)

    response = client.get("/landing")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Choose Your Plan" in html
    assert 'href="/pricing"' in html


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
    assert "fake Free" not in html
    assert "Enterprise tiers" not in html


def test_landing_no_longer_uses_beta_marketing_copy(client):
    html = client.get("/").get_data(as_text=True).lower()

    assert "join beta" not in html
    assert "private beta" not in html
    assert "beta access" not in html
    assert "apply for beta" not in html


def test_landing_legal_links_remain(client):
    html = client.get("/").get_data(as_text=True)

    assert 'href="/about"' in html
    assert 'href="/contact"' in html
    assert 'href="/privacy"' in html
    assert 'href="/terms"' in html
