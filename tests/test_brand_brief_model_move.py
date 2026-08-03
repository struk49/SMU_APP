import app as smu_app
from conftest import create_user, login
from smu_core.models import BrandBrief


BRAND_FORM = {
    "business_name": "SMU Test Brand",
    "niche": "Social media",
    "target_audience": "Small business owners",
    "offer": "Content planning",
    "tone_of_voice": "Friendly",
    "content_goals": "Plan consistent posts",
    "main_platforms": ["instagram", "facebook"],
    "cta_style": "Direct",
    "words_to_avoid": "jargon",
}


def test_brand_brief_model_remains_compatible(module):
    assert smu_app.BrandBrief is BrandBrief
    assert module.BrandBrief is BrandBrief
    assert BrandBrief.__table__.name == "brand_brief"
    assert list(BrandBrief.__table__.columns.keys()) == [
        "id",
        "user_id",
        "business_name",
        "niche",
        "target_audience",
        "offer",
        "tone_of_voice",
        "content_goals",
        "main_platforms",
        "cta_style",
        "words_to_avoid",
        "created_at",
        "updated_at",
    ]
    assert "brand_brief" in module.db.metadata.tables
    assert BrandBrief.__table__.c.user_id.unique is True
    assert {
        foreign_key.target_fullname
        for foreign_key in BrandBrief.__table__.foreign_keys
    } == {"user.id"}


def test_brand_brief_relationship_and_backref_still_work(app, module):
    with app.app_context():
        user = create_user(module)
        brief = module.BrandBrief(
            user_id=user.id,
            business_name="Relationship Test",
            niche="Content",
        )
        module.db.session.add(brief)
        module.db.session.commit()
        module.db.session.expire_all()

        saved_user = module.db.session.get(module.User, user.id)
        saved_brief = module.BrandBrief.query.filter_by(user_id=user.id).first()

        assert saved_user.brand_brief is saved_brief
        assert saved_brief.user is saved_user


def test_user_can_create_brand_brief(client, module):
    user = create_user(module)
    login(client, user)

    response = client.post("/brand-brief", data=BRAND_FORM)
    brief = module.BrandBrief.query.filter_by(user_id=user.id).first()

    assert response.status_code == 302
    assert response.location.endswith("/brand-brief")
    assert brief is not None
    assert brief.business_name == "SMU Test Brand"
    assert brief.main_platforms == "instagram,facebook"


def test_brand_brief_post_updates_existing_row(client, module):
    user = create_user(module)
    login(client, user)

    client.post("/brand-brief", data=BRAND_FORM)
    response = client.post(
        "/brand-brief",
        data={
            **BRAND_FORM,
            "business_name": "Updated Brand",
            "niche": "Updated niche",
        },
    )
    briefs = module.BrandBrief.query.filter_by(user_id=user.id).all()

    assert response.status_code == 302
    assert len(briefs) == 1
    assert briefs[0].business_name == "Updated Brand"
    assert briefs[0].niche == "Updated niche"


def test_brand_brief_route_isolates_users(client, module):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    module.db.session.add(
        module.BrandBrief(
            user_id=owner.id,
            business_name="Owner Brand",
            niche="Owner niche",
        )
    )
    module.db.session.commit()
    login(client, other)

    get_response = client.get("/brand-brief")
    post_response = client.post(
        "/brand-brief",
        data={
            **BRAND_FORM,
            "business_name": "Other Brand",
        },
    )
    owner_brief = module.BrandBrief.query.filter_by(user_id=owner.id).first()
    other_brief = module.BrandBrief.query.filter_by(user_id=other.id).first()

    assert get_response.status_code == 200
    assert "Owner Brand" not in get_response.get_data(as_text=True)
    assert post_response.status_code == 302
    assert owner_brief.business_name == "Owner Brand"
    assert other_brief.business_name == "Other Brand"
