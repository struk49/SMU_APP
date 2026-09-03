from calendar import monthrange

from smu_core.services.access import is_admin_user
from smu_core.services.time_utils import utc_now


DEFAULT_PLAN = "pro"
PLAN_LIMITS = {
    "starter": {
        "ai_images": 20,
        "content_packs": 10,
        "connected_accounts": 1,
    },
    "pro": {
        "ai_images": 50,
        "content_packs": 30,
        "connected_accounts": 2,
    },
    "business": {
        "ai_images": 100,
        "content_packs": 60,
        "connected_accounts": 3,
    },
}
ZERNIO_SOCIAL_ACCOUNT_FIELDS = {
    "instagram": "zernio_instagram_account_id",
    "facebook": "zernio_facebook_account_id",
}


def normalize_plan(plan):
    normalized = (plan or "").strip().lower()
    return normalized if normalized in PLAN_LIMITS else DEFAULT_PLAN


def get_plan_limits(plan):
    return PLAN_LIMITS[normalize_plan(plan)]


def get_connected_account_limit(plan):
    return get_plan_limits(plan)["connected_accounts"]


def zernio_account_id_for_platform(connected_account, platform):
    field_name = ZERNIO_SOCIAL_ACCOUNT_FIELDS.get((platform or "").strip().lower())
    if not field_name:
        return None
    return getattr(connected_account, field_name, None)


def linkedin_account_id(connected_account, *, now_provider=utc_now):
    if not connected_account:
        return None
    if not getattr(connected_account, "linkedin_connected", False):
        return None
    if not getattr(connected_account, "linkedin_access_token", None):
        return None

    member_urn = (getattr(connected_account, "linkedin_member_urn", None) or "").strip()
    if not member_urn:
        return None

    expires_at = getattr(connected_account, "linkedin_access_token_expires_at", None)
    if expires_at is not None and expires_at <= now_provider():
        return None

    return member_urn


def social_account_id_for_platform(connected_account, platform, *, now_provider=utc_now):
    platform = (platform or "").strip().lower()
    if platform == "linkedin":
        return linkedin_account_id(connected_account, now_provider=now_provider)
    return zernio_account_id_for_platform(connected_account, platform)


def get_connected_account_platforms(connected_account, *, now_provider=utc_now):
    if not connected_account:
        return []

    platforms = []
    seen_zernio_ids = set()
    for platform, field_name in ZERNIO_SOCIAL_ACCOUNT_FIELDS.items():
        account_id = str(getattr(connected_account, field_name, "") or "").strip()
        if account_id and account_id not in seen_zernio_ids:
            platforms.append(platform)
            seen_zernio_ids.add(account_id)

    if linkedin_account_id(connected_account, now_provider=now_provider):
        platforms.append("linkedin")

    return platforms


def get_connected_account_count(connected_account, *, now_provider=utc_now):
    return len(
        get_connected_account_platforms(
            connected_account,
            now_provider=now_provider,
        )
    )


def account_limit_message(limit):
    return (
        f"You've reached your {limit} connected social account limit. "
        "Upgrade your plan to connect another account."
    )


def add_calendar_month(value):
    year = value.year
    month = value.month + 1

    if month > 12:
        month = 1
        year += 1

    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def initial_period_end(user, start):
    period_end = getattr(user, "subscription_current_period_end", None)

    if period_end and period_end > start:
        return period_end

    return add_calendar_month(start)


def get_or_create_usage(user, *, usage_model, db_session, now_provider=utc_now):
    usage = usage_model.query.filter_by(user_id=user.id).first()
    now = now_provider()

    if usage is None:
        usage = usage_model(
            user_id=user.id,
            plan=DEFAULT_PLAN,
            ai_images_used=0,
            content_packs_used=0,
            usage_period_start=now,
            usage_period_end=initial_period_end(user, now),
        )
        db_session.add(usage)
        db_session.commit()
        return usage

    changed = False
    normalized_plan = normalize_plan(usage.plan)

    if usage.plan != normalized_plan:
        usage.plan = normalized_plan
        changed = True

    if usage.usage_period_start is None:
        usage.usage_period_start = now
        changed = True

    if usage.usage_period_end is None:
        usage.usage_period_end = initial_period_end(user, usage.usage_period_start)
        changed = True

    if now >= usage.usage_period_end:
        reset_usage_period(usage, user, now)
        changed = True

    if changed:
        db_session.commit()

    return usage


def reset_usage_period(usage, user, now):
    usage.ai_images_used = 0
    usage.content_packs_used = 0
    usage.usage_period_start = now

    period_end = getattr(user, "subscription_current_period_end", None)
    if period_end and period_end > now:
        usage.usage_period_end = period_end
    else:
        usage.usage_period_end = add_calendar_month(now)


def set_usage_plan(user, plan, *, usage_model, db_session, now_provider=utc_now):
    usage = get_or_create_usage(
        user,
        usage_model=usage_model,
        db_session=db_session,
        now_provider=now_provider,
    )
    usage.plan = normalize_plan(plan)
    return usage


def connected_account_limit_status(
    user,
    connected_account,
    *,
    usage_model,
    db_session,
    now_provider=utc_now,
):
    connected_platforms = get_connected_account_platforms(
        connected_account,
        now_provider=now_provider,
    )
    count = len(connected_platforms)

    if is_admin_user(user):
        return {
            "plan": "admin",
            "limit": None,
            "count": count,
            "remaining": None,
            "connected_platforms": connected_platforms,
            "over_limit": False,
            "can_connect": True,
            "is_admin": True,
        }

    user_usage = get_or_create_usage(
        user,
        usage_model=usage_model,
        db_session=db_session,
        now_provider=now_provider,
    )
    plan = normalize_plan(user_usage.plan)
    limit = get_connected_account_limit(plan)
    remaining = max(limit - count, 0)

    return {
        "plan": plan,
        "limit": limit,
        "count": count,
        "remaining": remaining,
        "connected_platforms": connected_platforms,
        "over_limit": count > limit,
        "can_connect": count < limit,
        "is_admin": False,
    }


def can_connect_social_account(
    user,
    connected_account,
    *,
    platform=None,
    usage_model,
    db_session,
    now_provider=utc_now,
):
    if platform and social_account_id_for_platform(
        connected_account,
        platform,
        now_provider=now_provider,
    ):
        return True

    return connected_account_limit_status(
        user,
        connected_account,
        usage_model=usage_model,
        db_session=db_session,
        now_provider=now_provider,
    )["can_connect"]


def usage_summary(user, *, usage_model, db_session, now_provider=utc_now):
    usage = get_or_create_usage(
        user,
        usage_model=usage_model,
        db_session=db_session,
        now_provider=now_provider,
    )
    limits = get_plan_limits(usage.plan)

    return {
        "plan": normalize_plan(usage.plan),
        "limits": limits,
        "ai_images_used": usage.ai_images_used,
        "content_packs_used": usage.content_packs_used,
        "ai_images_remaining": remaining_ai_image_credits(usage),
        "content_packs_remaining": remaining_content_pack_credits(usage),
        "usage_period_start": usage.usage_period_start,
        "usage_period_end": usage.usage_period_end,
        "is_admin": is_admin_user(user),
    }


def remaining_ai_image_credits(usage):
    limits = get_plan_limits(usage.plan)
    return max(limits["ai_images"] - usage.ai_images_used, 0)


def remaining_content_pack_credits(usage):
    limits = get_plan_limits(usage.plan)
    return max(limits["content_packs"] - usage.content_packs_used, 0)


def can_generate_image(user, count=1, *, usage_model, db_session, now_provider=utc_now):
    if is_admin_user(user):
        return True

    usage = get_or_create_usage(
        user,
        usage_model=usage_model,
        db_session=db_session,
        now_provider=now_provider,
    )
    return remaining_ai_image_credits(usage) >= count


def can_generate_content_pack(
    user,
    count=1,
    *,
    usage_model,
    db_session,
    now_provider=utc_now,
):
    if is_admin_user(user):
        return True

    usage = get_or_create_usage(
        user,
        usage_model=usage_model,
        db_session=db_session,
        now_provider=now_provider,
    )
    return remaining_content_pack_credits(usage) >= count


def reserve_image_credits(
    user,
    count=1,
    *,
    usage_model,
    db_session,
    now_provider=utc_now,
):
    if is_admin_user(user):
        return True

    usage = get_or_create_usage(
        user,
        usage_model=usage_model,
        db_session=db_session,
        now_provider=now_provider,
    )

    if remaining_ai_image_credits(usage) < count:
        return False

    usage.ai_images_used += count
    db_session.commit()
    return True


def release_image_credits(user, count=1, *, usage_model, db_session):
    if is_admin_user(user):
        return

    usage = usage_model.query.filter_by(user_id=user.id).first()
    if not usage:
        return

    usage.ai_images_used = max(usage.ai_images_used - count, 0)
    db_session.commit()


def record_successful_image_usage(
    user,
    count=1,
    *,
    usage_model,
    db_session,
    now_provider=utc_now,
):
    if is_admin_user(user):
        return True

    usage = get_or_create_usage(
        user,
        usage_model=usage_model,
        db_session=db_session,
        now_provider=now_provider,
    )

    if remaining_ai_image_credits(usage) < count:
        return False

    usage.ai_images_used += count
    db_session.commit()
    return True


def record_successful_content_pack_usage(
    user,
    count=1,
    *,
    usage_model,
    db_session,
    now_provider=utc_now,
):
    if is_admin_user(user):
        return True

    usage = get_or_create_usage(
        user,
        usage_model=usage_model,
        db_session=db_session,
        now_provider=now_provider,
    )

    if remaining_content_pack_credits(usage) < count:
        return False

    usage.content_packs_used += count
    db_session.commit()
    return True


def reserve_content_pack_credits(
    user,
    count=1,
    *,
    usage_model,
    db_session,
    now_provider=utc_now,
):
    if is_admin_user(user):
        return True

    usage = get_or_create_usage(
        user,
        usage_model=usage_model,
        db_session=db_session,
        now_provider=now_provider,
    )

    if remaining_content_pack_credits(usage) < count:
        return False

    usage.content_packs_used += count
    db_session.commit()
    return True


def release_content_pack_credits(user, count=1, *, usage_model, db_session):
    if is_admin_user(user):
        return

    usage = usage_model.query.filter_by(user_id=user.id).first()
    if not usage:
        return

    usage.content_packs_used = max(usage.content_packs_used - count, 0)
    db_session.commit()
