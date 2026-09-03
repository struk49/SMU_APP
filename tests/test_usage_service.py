from datetime import datetime, timedelta

from conftest import create_user
from smu_core.services import usage


def fixed_now():
    return datetime(2026, 8, 15, 12, 0, 0)


def test_plan_limits_are_defined_for_paid_tiers():
    assert usage.get_plan_limits("starter") == {
        "ai_images": 20,
        "content_packs": 10,
        "connected_accounts": 1,
    }
    assert usage.get_plan_limits("pro") == {
        "ai_images": 50,
        "content_packs": 30,
        "connected_accounts": 2,
    }
    assert usage.get_plan_limits("business") == {
        "ai_images": 100,
        "content_packs": 60,
        "connected_accounts": 3,
    }
    assert usage.normalize_plan("unknown") == "pro"
    assert usage.get_connected_account_limit("starter") == 1
    assert usage.get_connected_account_limit("pro") == 2
    assert usage.get_connected_account_limit("business") == 3
    assert usage.get_connected_account_limit("unknown") == 2


def test_usage_row_is_created_lazily_with_pro_default(app, module):
    user = create_user(module)

    user_usage = usage.get_or_create_usage(
        user,
        usage_model=module.UserUsage,
        db_session=module.db.session,
        now_provider=fixed_now,
    )

    assert user_usage.user_id == user.id
    assert user_usage.plan == "pro"
    assert user_usage.ai_images_used == 0
    assert user_usage.content_packs_used == 0
    assert user_usage.usage_period_start == fixed_now()
    assert user_usage.usage_period_start.tzinfo is None
    assert user_usage.usage_period_end.tzinfo is None
    assert module.UserUsage.query.filter_by(user_id=user.id).count() == 1


def test_usage_period_resets_lazily_after_period_end(app, module):
    user = create_user(module)
    old_start = fixed_now() - timedelta(days=40)
    old_end = fixed_now() - timedelta(days=1)
    user_usage = module.UserUsage(
        user_id=user.id,
        plan="starter",
        ai_images_used=20,
        content_packs_used=10,
        usage_period_start=old_start,
        usage_period_end=old_end,
    )
    module.db.session.add(user_usage)
    module.db.session.commit()

    refreshed = usage.get_or_create_usage(
        user,
        usage_model=module.UserUsage,
        db_session=module.db.session,
        now_provider=fixed_now,
    )

    assert refreshed.ai_images_used == 0
    assert refreshed.content_packs_used == 0
    assert refreshed.usage_period_start == fixed_now()
    assert refreshed.usage_period_end > fixed_now()
    assert refreshed.usage_period_start.tzinfo is None


def test_set_usage_plan_preserves_current_period_and_counters(app, module):
    user = create_user(module)
    user_usage = module.UserUsage(
        user_id=user.id,
        plan="starter",
        ai_images_used=11,
        content_packs_used=3,
        usage_period_start=fixed_now() - timedelta(days=5),
        usage_period_end=fixed_now() + timedelta(days=20),
    )
    module.db.session.add(user_usage)
    module.db.session.commit()

    updated = usage.set_usage_plan(
        user,
        "business",
        usage_model=module.UserUsage,
        db_session=module.db.session,
        now_provider=fixed_now,
    )

    assert updated.plan == "business"
    assert updated.ai_images_used == 11
    assert updated.content_packs_used == 3
    assert updated.usage_period_start == fixed_now() - timedelta(days=5)
    assert updated.usage_period_end == fixed_now() + timedelta(days=20)


def test_reserve_and_release_image_credits(app, module):
    user = create_user(module)

    assert usage.reserve_image_credits(
        user,
        count=3,
        usage_model=module.UserUsage,
        db_session=module.db.session,
        now_provider=fixed_now,
    )
    user_usage = module.UserUsage.query.filter_by(user_id=user.id).one()
    assert user_usage.ai_images_used == 3

    usage.release_image_credits(
        user,
        count=2,
        usage_model=module.UserUsage,
        db_session=module.db.session,
    )
    assert user_usage.ai_images_used == 1


def test_admin_users_bypass_usage_limits(app, module):
    module.app.config["SMU_ADMIN_EMAILS"] = {"admin@example.com"}
    user = create_user(module, email="admin@example.com")

    assert usage.reserve_image_credits(
        user,
        count=999,
        usage_model=module.UserUsage,
        db_session=module.db.session,
        now_provider=fixed_now,
    )
    assert usage.reserve_content_pack_credits(
        user,
        count=999,
        usage_model=module.UserUsage,
        db_session=module.db.session,
        now_provider=fixed_now,
    )
    assert module.UserUsage.query.filter_by(user_id=user.id).first() is None
