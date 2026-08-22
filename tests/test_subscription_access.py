from werkzeug.security import generate_password_hash

from conftest import create_post, create_user, login
from smu_core.services.access import has_product_access
from smu_core.services import billing


def activate_subscription(module, user, status="active"):
    user.subscription_status = status
    user.stripe_customer_id = "cus_test"
    user.stripe_subscription_id = "sub_test"
    module.db.session.commit()
    return user


def test_access_helper_allows_only_active_trialing_admin_or_open_mode(app, module):
    user = create_user(module)

    app.config["REGISTRATION_MODE"] = "subscription"
    for blocked_status in [None, "past_due", "unpaid", "canceled", "incomplete", "paused"]:
        user.subscription_status = blocked_status
        assert has_product_access(user) is False

    user.subscription_status = "active"
    assert has_product_access(user) is True

    user.subscription_status = "trialing"
    assert has_product_access(user) is True

    user.subscription_status = None
    app.config["SMU_ADMIN_EMAILS"] = {user.email}
    assert has_product_access(user) is True

    app.config["SMU_ADMIN_EMAILS"] = set()
    app.config["REGISTRATION_MODE"] = "open"
    assert has_product_access(user) is True


def test_public_and_billing_routes_remain_accessible_without_subscription(client):
    assert client.get("/").status_code == 200
    assert client.get("/landing").status_code == 200
    assert client.get("/pricing").status_code == 200
    assert client.get("/privacy").status_code == 200
    assert client.get("/terms").status_code == 200
    assert client.get("/contact").status_code == 200
    assert client.get("/login").status_code == 200
    assert client.get("/register").status_code == 200
    assert client.post("/billing/webhook").status_code != 302


def test_subscription_mode_registration_allows_user_without_beta_application(
    client,
    app,
    module,
):
    app.config["REGISTRATION_MODE"] = "subscription"

    response = client.post(
        "/register",
        data={
            "email": "new-subscription@example.com",
            "password": "secret-password",
            "confirm_password": "secret-password",
        },
    )
    user = module.User.query.filter_by(email="new-subscription@example.com").first()

    assert response.status_code == 302
    assert response.location.endswith("/pricing")
    assert user is not None
    assert user.subscription_status is None


def test_beta_mode_preserves_beta_registration_requirement(client, app, module):
    app.config["REGISTRATION_MODE"] = "beta"

    response = client.post(
        "/register",
        data={
            "email": "unapproved-beta@example.com",
            "password": "secret-password",
            "confirm_password": "secret-password",
        },
    )

    assert response.status_code == 403
    assert module.User.query.filter_by(email="unapproved-beta@example.com").first() is None


def test_login_redirects_unpaid_users_to_pricing_and_active_users_to_dashboard(
    client,
    app,
    module,
):
    app.config["REGISTRATION_MODE"] = "subscription"
    unpaid = module.User(
        email="unpaid@example.com",
        password_hash=generate_password_hash("correct-password"),
    )
    active = module.User(
        email="active@example.com",
        password_hash=generate_password_hash("correct-password"),
        subscription_status="active",
    )
    module.db.session.add_all([unpaid, active])
    module.db.session.commit()

    unpaid_response = client.post(
        "/login",
        data={"email": "unpaid@example.com", "password": "correct-password"},
    )
    client.get("/logout")
    active_response = client.post(
        "/login",
        data={"email": "active@example.com", "password": "correct-password"},
    )

    assert unpaid_response.status_code == 302
    assert unpaid_response.location.endswith("/pricing")
    assert active_response.status_code == 302
    assert active_response.location.endswith("/")


def test_trialing_user_login_is_treated_as_active_access(client, app, module):
    app.config["REGISTRATION_MODE"] = "subscription"
    trialing = module.User(
        email="trialing@example.com",
        password_hash=generate_password_hash("correct-password"),
        subscription_status="trialing",
    )
    module.db.session.add(trialing)
    module.db.session.commit()

    response = client.post(
        "/login",
        data={"email": "trialing@example.com", "password": "correct-password"},
    )

    assert response.status_code == 302
    assert response.location.endswith("/")


def test_dashboard_blocks_unpaid_but_preserves_anonymous_landing_and_active_dashboard(
    client,
    app,
    module,
):
    app.config["REGISTRATION_MODE"] = "subscription"
    anonymous = client.get("/")
    unpaid = create_user(module, email="unpaid-dashboard@example.com")
    login(client, unpaid)
    unpaid_response = client.get("/")
    client.get("/logout")
    active = activate_subscription(
        module,
        create_user(module, email="active-dashboard@example.com"),
    )
    login(client, active)
    active_response = client.get("/")

    assert anonymous.status_code == 200
    assert "Turn One Idea Into Content Everywhere" in anonymous.get_data(as_text=True)
    assert unpaid_response.status_code == 302
    assert unpaid_response.location.endswith("/pricing")
    assert active_response.status_code == 200
    assert "No posts yet" in active_response.get_data(as_text=True)


def test_unpaid_users_cannot_bypass_product_routes_by_direct_url(client, app, module):
    app.config["REGISTRATION_MODE"] = "subscription"
    user = create_user(module, email="unpaid-product@example.com")
    post = create_post(module, user)
    login(client, user)

    blocked_gets = [
        "/tiktok",
        "/create",
        f"/post/{post.id}",
        f"/post/{post.id}/studio",
        "/content-pack",
        "/calendar",
        "/calendar/events?start=2026-01-01&end=2026-02-01",
        "/brand-brief",
    ]
    blocked_posts = [
        f"/schedule/{post.id}",
        f"/send/{post.id}",
    ]

    for path in blocked_gets:
        response = client.get(path)
        assert response.status_code == 302
        assert response.location.endswith("/pricing")

    for path in blocked_posts:
        response = client.post(path)
        assert response.status_code == 302
        assert response.location.endswith("/pricing")


def test_connected_accounts_view_remains_available_but_changes_are_gated(
    client,
    app,
    module,
):
    app.config["REGISTRATION_MODE"] = "subscription"
    user = create_user(module, email="unpaid-accounts@example.com")
    login(client, user)

    get_response = client.get("/settings/accounts")
    post_response = client.post(
        "/settings/accounts",
        data={"instagram_connected": "on"},
    )
    linkedin_response = client.get("/accounts/linkedin/connect")

    assert get_response.status_code == 200
    assert post_response.status_code == 302
    assert post_response.location.endswith("/pricing")
    assert linkedin_response.status_code == 302
    assert linkedin_response.location.endswith("/pricing")


def test_unpaid_user_can_still_start_billing_checkout(
    client,
    app,
    module,
    monkeypatch,
):
    app.config.update(
        REGISTRATION_MODE="subscription",
        STRIPE_SECRET_KEY="sk_test_123",
        STRIPE_PRICE_ID="price_123",
        SERVER_NAME="smu.test",
    )
    user = create_user(module, email="checkout-unpaid@example.com")
    login(client, user)

    monkeypatch.setattr(
        billing,
        "create_checkout_session",
        lambda user_arg, **kwargs: type(
            "Session",
            (),
            {"url": "https://checkout.stripe.test/session"},
        )(),
    )

    response = client.post("/billing/checkout")

    assert response.status_code == 302
    assert response.location == "https://checkout.stripe.test/session"


def test_subscription_success_page_does_not_grant_access(client, app, module):
    app.config["REGISTRATION_MODE"] = "subscription"
    user = create_user(module, email="success-unpaid@example.com")
    login(client, user)

    success = client.get("/billing/success")
    dashboard = client.get("/")

    assert success.status_code == 200
    assert dashboard.status_code == 302
    assert dashboard.location.endswith("/pricing")
