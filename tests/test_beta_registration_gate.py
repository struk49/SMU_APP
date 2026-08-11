from werkzeug.security import check_password_hash, generate_password_hash

from conftest import create_user, login


PRIVATE_BETA_MESSAGE = (
    "SMU is currently in private beta. Apply for access using the Join Beta form."
)


def create_beta_application(module, *, email, status="new"):
    application = module.BetaApplication(
        name="Beta Applicant",
        email=email.strip().lower(),
        primary_platform="LinkedIn",
        posting_frequency="6-15 posts",
        challenge="Keeping content consistent.",
        consent=True,
        status=status,
    )
    module.db.session.add(application)
    module.db.session.commit()
    return application


def registration_form(email="approved@example.com", password="secret-password"):
    return {
        "email": email,
        "password": password,
        "confirm_password": password,
    }


def test_register_get_shows_private_beta_notice_and_apply_link(client):
    response = client.get("/register")
    html = response.get_data(as_text=True)
    normalized_html = " ".join(html.split())

    assert response.status_code == 200
    assert "SMU is currently in private beta" in normalized_html
    assert "Registration is available to approved beta users" in normalized_html
    assert "Apply for Beta Access" in html
    assert 'href="/beta/apply"' in html


def test_missing_beta_application_blocks_registration(client, module):
    response = client.post(
        "/register",
        data=registration_form(email="missing@example.com"),
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 403
    assert PRIVATE_BETA_MESSAGE in html
    assert module.User.query.filter_by(email="missing@example.com").first() is None


def test_pending_beta_application_blocks_registration(client, module):
    create_beta_application(module, email="pending@example.com", status="new")

    response = client.post(
        "/register",
        data=registration_form(email="pending@example.com"),
    )

    assert response.status_code == 403
    assert PRIVATE_BETA_MESSAGE in response.get_data(as_text=True)
    assert module.User.query.filter_by(email="pending@example.com").first() is None


def test_rejected_beta_application_blocks_registration(client, module):
    create_beta_application(module, email="rejected@example.com", status="rejected")

    response = client.post(
        "/register",
        data=registration_form(email="rejected@example.com"),
    )

    assert response.status_code == 403
    assert PRIVATE_BETA_MESSAGE in response.get_data(as_text=True)
    assert module.User.query.filter_by(email="rejected@example.com").first() is None


def test_blocked_registration_uses_generic_message_for_all_unapproved_states(
    client,
    module,
):
    create_beta_application(module, email="alpha@example.com", status="new")
    create_beta_application(module, email="bravo@example.com", status="rejected")
    responses = [
        client.post("/register", data=registration_form(email="missing@example.com")),
        client.post("/register", data=registration_form(email="alpha@example.com")),
        client.post("/register", data=registration_form(email="bravo@example.com")),
    ]

    for response in responses:
        html = response.get_data(as_text=True)

        assert response.status_code == 403
        assert PRIVATE_BETA_MESSAGE in html
        assert "Your application is pending" not in html
        assert "Your application was rejected" not in html
        assert "No application" not in html


def test_approved_beta_email_can_register_and_is_logged_in(client, module):
    create_beta_application(module, email="approved@example.com", status="approved")

    response = client.post(
        "/register",
        data=registration_form(email="approved@example.com"),
    )
    user = module.User.query.filter_by(email="approved@example.com").first()

    assert response.status_code == 302
    assert response.location.endswith("/")
    assert user is not None
    assert check_password_hash(user.password_hash, "secret-password")
    with client.session_transaction() as session:
        assert session["_user_id"] == str(user.id)


def test_approved_beta_email_matching_is_case_insensitive_and_trimmed(client, module):
    create_beta_application(module, email="mixed@example.com", status="approved")

    response = client.post(
        "/register",
        data=registration_form(email="  MIXED@example.COM  "),
    )
    user = module.User.query.filter_by(email="mixed@example.com").first()

    assert response.status_code == 302
    assert user is not None
    assert module.User.query.count() == 1


def test_password_validation_still_runs_before_beta_gate(client, module):
    create_beta_application(module, email="approved@example.com", status="approved")

    response = client.post(
        "/register",
        data={
            "email": "approved@example.com",
            "password": "secret-password",
            "confirm_password": "different-password",
        },
    )

    assert response.status_code == 302
    assert response.location.endswith("/register")
    assert module.User.query.count() == 0


def test_duplicate_user_behavior_remains_unchanged(client, module):
    create_user(module, email="duplicate@example.com")

    response = client.post(
        "/register",
        data=registration_form(email="duplicate@example.com"),
    )

    assert response.status_code == 302
    assert response.location.endswith("/register")
    assert module.User.query.filter_by(email="duplicate@example.com").count() == 1


def test_existing_user_login_still_works_without_beta_application(client, module):
    user = module.User(
        email="existing@example.com",
        password_hash=generate_password_hash("correct-password"),
    )
    module.db.session.add(user)
    module.db.session.commit()

    response = client.post(
        "/login",
        data={"email": "existing@example.com", "password": "correct-password"},
    )

    assert response.status_code == 302
    assert response.location.endswith("/")
    with client.session_transaction() as session:
        assert session["_user_id"] == str(user.id)


def test_authenticated_users_keep_register_redirect(client, module):
    user = create_user(module, email="already@example.com")
    login(client, user)

    response = client.get("/register")

    assert response.status_code == 302
    assert response.location.endswith("/")


def test_admin_approval_action_permits_registration(client, app, module):
    app.config["SMU_ADMIN_EMAILS"] = {"admin@example.com"}
    admin = create_user(module, email="admin@example.com")
    application = create_beta_application(
        module,
        email="admin-approved@example.com",
        status="new",
    )
    login(client, admin)

    approve_response = client.post(
        f"/admin/beta/{application.id}/status",
        data={"status": "approved"},
    )
    client.get("/logout")
    register_response = client.post(
        "/register",
        data=registration_form(email="admin-approved@example.com"),
    )

    assert approve_response.status_code == 302
    assert module.db.session.get(module.BetaApplication, application.id).status == (
        "approved"
    )
    assert register_response.status_code == 302
    assert module.User.query.filter_by(email="admin-approved@example.com").count() == 1
