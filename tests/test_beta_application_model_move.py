import app as smu_app
from conftest import create_user, login
from smu_core.models import BetaApplication


def test_beta_application_model_remains_compatible(module):
    assert smu_app.BetaApplication is BetaApplication
    assert module.BetaApplication is BetaApplication
    assert BetaApplication.__table__.name == "beta_application"
    assert list(BetaApplication.__table__.columns.keys()) == [
        "id",
        "name",
        "email",
        "primary_platform",
        "posting_frequency",
        "challenge",
        "consent",
        "status",
        "created_at",
    ]
    assert "beta_application" in module.db.metadata.tables
    assert BetaApplication.__table__.c.email.unique is True


def test_beta_application_submission_still_creates_row(client, module):
    response = client.post(
        "/beta/apply",
        data={
            "name": "Beta Applicant",
            "email": "beta-applicant@example.com",
            "primary_platform": "Instagram",
            "posting_frequency": "6-15 posts",
            "challenge": "Planning content consistently.",
            "consent": "on",
        },
        follow_redirects=True,
    )
    application = module.BetaApplication.query.filter_by(
        email="beta-applicant@example.com"
    ).first()

    assert response.status_code == 200
    assert "private beta application has been received" in response.get_data(
        as_text=True
    )
    assert application is not None
    assert application.status == "new"
    assert application.consent is True


def test_beta_application_duplicate_email_still_blocked(client, module):
    module.db.session.add(
        module.BetaApplication(
            name="Existing Applicant",
            email="duplicate-beta@example.com",
            primary_platform="Instagram",
            posting_frequency="1-5 posts",
            challenge="Need help with ideas.",
            consent=True,
        )
    )
    module.db.session.commit()

    response = client.post(
        "/beta/apply",
        data={
            "name": "Duplicate Applicant",
            "email": "duplicate-beta@example.com",
            "primary_platform": "Facebook",
            "posting_frequency": "6-15 posts",
            "challenge": "Need help with scheduling.",
            "consent": "on",
        },
    )

    assert response.status_code == 400
    assert module.BetaApplication.query.count() == 1
    assert "already exists" in response.get_data(as_text=True)


def test_admin_beta_still_queries_beta_applications(client, module, app):
    app.config["SMU_ADMIN_EMAILS"] = {"admin@example.com"}
    admin = create_user(module, email="admin@example.com")
    login(client, admin)
    module.db.session.add(
        module.BetaApplication(
            name="Admin Visible Applicant",
            email="visible@example.com",
            primary_platform="Pinterest",
            posting_frequency="16-30 posts",
            challenge="Planning ahead.",
            consent=True,
        )
    )
    module.db.session.commit()

    response = client.get("/admin/beta")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Admin Visible Applicant" in html
    assert "Pinterest" in html
