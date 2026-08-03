from contextlib import contextmanager

from flask import template_rendered, url_for

import app as smu_app
from conftest import create_user, login
from smu_core.models import BrandBrief


BRAND_FORM = {
    "business_name": "  SMU Test Brand  ",
    "niche": "  Social media  ",
    "target_audience": "  Small business owners  ",
    "offer": "  Content planning  ",
    "tone_of_voice": "  Friendly  ",
    "content_goals": "  Plan consistent posts  ",
    "main_platforms": ["instagram", "facebook"],
    "cta_style": "  Direct  ",
    "words_to_avoid": "  jargon  ",
}


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


def test_brand_blueprint_is_registered_once(module):
    assert "brand" in module.app.blueprints
    assert list(module.app.blueprints).count("brand") == 1


def test_brand_route_preserves_endpoint_and_methods(module):
    rules = rules_for(module.app, "/brand-brief")

    assert len(rules) == 1
    assert rules[0].endpoint == "brand_brief"
    assert {"GET", "POST"}.issubset(rules[0].methods)


def test_brand_url_for_compatibility(module):
    with module.app.test_request_context():
        assert url_for("brand_brief") == "/brand-brief"


def test_brand_brief_requires_login(client):
    response = client.get("/brand-brief")

    assert response.status_code == 302
    assert "/login" in response.location


def test_brand_brief_get_renders_template_for_logged_in_user(client, app, module):
    user = create_user(module)
    login(client, user)

    with captured_templates(app) as templates:
        response = client.get("/brand-brief")

    assert response.status_code == 200
    assert templates[0][0] == "brand_brief.html"
    assert templates[0][1]["brief"] is None


def test_brand_brief_create_stores_all_fields_and_redirects(client, module):
    user = create_user(module)
    login(client, user)

    response = client.post("/brand-brief", data=BRAND_FORM, follow_redirects=True)
    html = response.get_data(as_text=True)
    brief = module.BrandBrief.query.filter_by(user_id=user.id).first()

    assert response.status_code == 200
    assert "Brand Brief saved successfully." in html
    assert brief is not None
    assert brief.business_name == "SMU Test Brand"
    assert brief.niche == "Social media"
    assert brief.target_audience == "Small business owners"
    assert brief.offer == "Content planning"
    assert brief.tone_of_voice == "Friendly"
    assert brief.content_goals == "Plan consistent posts"
    assert brief.main_platforms == "instagram,facebook"
    assert brief.cta_style == "Direct"
    assert brief.words_to_avoid == "jargon"
    assert brief.created_at is not None
    assert brief.updated_at is not None


def test_brand_brief_update_reuses_existing_row(client, module):
    user = create_user(module)
    login(client, user)
    client.post("/brand-brief", data=BRAND_FORM)
    original = module.BrandBrief.query.filter_by(user_id=user.id).first()
    original_id = original.id
    original_created_at = original.created_at

    response = client.post(
        "/brand-brief",
        data={
            **BRAND_FORM,
            "business_name": "Updated Brand",
            "niche": "Updated niche",
            "main_platforms": ["pinterest"],
        },
        follow_redirects=True,
    )
    briefs = module.BrandBrief.query.filter_by(user_id=user.id).all()

    assert response.status_code == 200
    assert len(briefs) == 1
    assert briefs[0].id == original_id
    assert briefs[0].created_at == original_created_at
    assert briefs[0].updated_at is not None
    assert briefs[0].business_name == "Updated Brand"
    assert briefs[0].niche == "Updated niche"
    assert briefs[0].main_platforms == "pinterest"


def test_brand_brief_route_isolates_users(client, app, module):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    owner_brief = module.BrandBrief(
        user_id=owner.id,
        business_name="Owner Brand",
        niche="Owner niche",
    )
    module.db.session.add(owner_brief)
    module.db.session.commit()
    login(client, other)

    with captured_templates(app) as templates:
        get_response = client.get("/brand-brief")
    post_response = client.post(
        "/brand-brief",
        data={
            **BRAND_FORM,
            "business_name": "Other Brand",
        },
    )
    saved_owner_brief = module.BrandBrief.query.filter_by(user_id=owner.id).first()
    other_brief = module.BrandBrief.query.filter_by(user_id=other.id).first()

    assert get_response.status_code == 200
    assert templates[0][1]["brief"] is None
    assert "Owner Brand" not in get_response.get_data(as_text=True)
    assert post_response.status_code == 302
    assert saved_owner_brief.business_name == "Owner Brand"
    assert other_brief.business_name == "Other Brand"
    assert module.BrandBrief.query.count() == 2


def test_brand_brief_model_and_app_import_compatibility_remain(module):
    assert smu_app.BrandBrief is BrandBrief
    assert module.BrandBrief is BrandBrief


def test_public_auth_beta_feedback_and_unrelated_endpoints_remain_registered(module):
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
        "submit_feedback",
        "index",
        "calendar_view",
        "create_post",
        "post_studio",
        "send_to_make",
        "connected_accounts",
    }.issubset(endpoints)


def test_scheduler_reference_is_untouched(module):
    assert module.scheduler is smu_app.scheduler
