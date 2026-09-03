from conftest import create_user, login


def test_public_landing_page_renders_without_login(client):
    response = client.get("/landing")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Turn one idea into social content in minutes" in html
    assert "Get Started" in html
    assert "SMU v1.0.0-rc1 · © podgeaisolutions" in html


def test_legacy_branding_is_not_rendered_on_public_pages(client):
    response = client.get("/landing")
    html = response.get_data(as_text=True)

    assert "Social Uploader" not in html
    assert "Social Media Uploader" not in html


def test_beta_application_success(client, module):
    response = client.post(
        "/beta/apply",
        data={
            "name": "Andrew",
            "email": "andrew@example.com",
            "primary_platform": "Instagram",
            "posting_frequency": "6-15 posts",
            "challenge": "Keeping content consistent.",
            "consent": "on",
        },
        follow_redirects=True,
    )
    application = module.BetaApplication.query.filter_by(
        email="andrew@example.com"
    ).first()

    assert response.status_code == 200
    assert "private beta application has been received" in response.get_data(
        as_text=True
    )
    assert application is not None
    assert application.status == "new"
    assert application.consent is True


def test_beta_application_validation_rejects_missing_fields(client, module):
    response = client.post("/beta/apply", data={})

    assert response.status_code == 400
    assert module.BetaApplication.query.count() == 0
    assert "Name is required." in response.get_data(as_text=True)


def test_beta_application_rejects_duplicate_email(client, module):
    module.db.session.add(
        module.BetaApplication(
            name="Existing",
            email="duplicate@example.com",
            primary_platform="Instagram",
            posting_frequency="1-5 posts",
            challenge="Need help.",
            consent=True,
        )
    )
    module.db.session.commit()

    response = client.post(
        "/beta/apply",
        data={
            "name": "Duplicate",
            "email": "duplicate@example.com",
            "primary_platform": "Facebook",
            "posting_frequency": "6-15 posts",
            "challenge": "More help.",
            "consent": "on",
        },
    )

    assert response.status_code == 400
    assert module.BetaApplication.query.count() == 1
    assert "already exists" in response.get_data(as_text=True)


def test_legal_and_contact_pages_render(client):
    for path, text in [
        ("/privacy", "Privacy Policy"),
        ("/terms", "Terms of Service"),
        ("/contact", "Contact SMU"),
    ]:
        response = client.get(path)
        assert response.status_code == 200
        assert text in response.get_data(as_text=True)


def test_contact_form_stores_message(client, module):
    response = client.post(
        "/contact",
        data={
            "name": "Beta User",
            "email": "beta@example.com",
            "message": "Can I join?",
        },
        follow_redirects=True,
    )
    message = module.ContactMessage.query.filter_by(
        email="beta@example.com"
    ).first()

    assert response.status_code == 200
    assert "message has been received" in response.get_data(as_text=True)
    assert message is not None


def test_admin_beta_requires_login(client):
    response = client.get("/admin/beta")

    assert response.status_code == 302
    assert "/login" in response.location


def test_admin_beta_rejects_non_admin_user(client, module):
    user = create_user(module, email="user@example.com")
    login(client, user)

    response = client.get("/admin/beta")

    assert response.status_code == 404


def test_admin_beta_renders_applications_and_feedback(client, module, app):
    app.config["SMU_ADMIN_EMAILS"] = {"admin@example.com"}
    user = create_user(module, email="admin@example.com")
    login(client, user)
    module.db.session.add(
        module.BetaApplication(
            name="Applicant",
            email="applicant@example.com",
            primary_platform="Pinterest",
            posting_frequency="16-30 posts",
            challenge="Planning ahead.",
            consent=True,
        )
    )
    module.db.session.add(
        module.Feedback(
            user_id=user.id,
            message="Nice dashboard.",
            page_url="/",
        )
    )
    module.db.session.commit()

    response = client.get("/admin/beta")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Applicant" in html
    assert "Pinterest" in html
    assert "Nice dashboard." in html


def test_rc1_footer_version_on_authenticated_dashboard(client, module):
    user = create_user(module)
    login(client, user)

    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "SMU v1.0.0-rc1" in html
    assert "podgeaisolutions" in html


def test_authenticated_dashboard_remains_accessible(client, module):
    user = create_user(module)
    login(client, user)

    response = client.get("/")

    assert response.status_code == 200
    assert "Content Dashboard" in response.get_data(as_text=True)
