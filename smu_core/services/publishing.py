import os

import requests
from flask_login import current_user

from smu_core.models import ConnectedAccount, Post
from smu_core.services import linkedin_publishing
from smu_core.services.time_utils import utc_now


MAKE_WEBHOOK_SINGLE = os.getenv("MAKE_WEBHOOK_SINGLE", "").strip()
MAKE_WEBHOOK_CAROUSEL = os.getenv("MAKE_WEBHOOK_CAROUSEL", "").strip()


def _parse_platforms(platforms_string):
    if not platforms_string:
        return []

    return [
        platform.strip() for platform in platforms_string.split(",") if platform.strip()
    ]


def _get_ordered_carousel_posts(group_id, user_id=None):
    query = Post.query.filter_by(group_id=group_id)

    if user_id is not None:
        query = query.filter_by(user_id=user_id)

    return query.order_by(
        Post.is_cover.desc(),
        Post.sort_order.asc(),
        Post.id.asc(),
    ).all()


def _make_instagram_safe_url(url):
    return url.replace("/upload/", "/upload/c_fill,w_1080,h_1080,q_auto,f_jpg/")


def get_user_connected_accounts(user_id=None):
    if user_id is None:
        if not current_user.is_authenticated:
            return None

        user_id = current_user.id

    return ConnectedAccount.query.filter_by(
        user_id=user_id
    ).first()


def get_enabled_platforms_for_user(
    selected_platforms,
    user_id=None,
    *,
    get_user_connected_accounts_func=None,
):
    if get_user_connected_accounts_func is None:
        get_user_connected_accounts_func = get_user_connected_accounts

    accounts = get_user_connected_accounts_func(user_id)

    if not accounts:
        return []

    platform_map = {
        "instagram": accounts.instagram_connected,
        "facebook": accounts.facebook_connected,
        "linkedin": accounts.linkedin_connected,
        "pinterest": accounts.pinterest_connected,
        "reddit": accounts.reddit_connected,
        "x": accounts.x_connected,
    }

    enabled_platforms = []

    for platform in selected_platforms:
        clean_platform = platform.strip().lower()

        if platform_map.get(clean_platform, False):
            enabled_platforms.append(clean_platform)

    return enabled_platforms


def get_user_make_webhook(
    post_type,
    user_id=None,
    *,
    get_user_connected_accounts_func=None,
    make_webhook_single=None,
    make_webhook_carousel=None,
):
    if get_user_connected_accounts_func is None:
        get_user_connected_accounts_func = get_user_connected_accounts
    if make_webhook_single is None:
        make_webhook_single = MAKE_WEBHOOK_SINGLE
    if make_webhook_carousel is None:
        make_webhook_carousel = MAKE_WEBHOOK_CAROUSEL

    accounts = get_user_connected_accounts_func(user_id)

    if post_type == "carousel":
        if accounts and accounts.make_webhook_carousel:
            return accounts.make_webhook_carousel

        return make_webhook_carousel or None

    if accounts and accounts.make_webhook_single:
        return accounts.make_webhook_single

    return make_webhook_single or None


def build_single_payload(post, *, parse_platforms_func=None):
    if parse_platforms_func is None:
        parse_platforms_func = _parse_platforms

    return {
        "post_type": "single",
        "post_id": post.id,
        "caption": post.caption,
        "prompt": post.prompt,
        "file_url": post.file_url,
        "file_type": post.file_type,
        "platforms": parse_platforms_func(post.platforms),
    }


def build_carousel_payload(
    group_id,
    user_id=None,
    *,
    get_ordered_carousel_posts_func=None,
    parse_platforms_func=None,
    make_instagram_safe_url_func=None,
):
    if get_ordered_carousel_posts_func is None:
        get_ordered_carousel_posts_func = _get_ordered_carousel_posts
    if parse_platforms_func is None:
        parse_platforms_func = _parse_platforms
    if make_instagram_safe_url_func is None:
        make_instagram_safe_url_func = _make_instagram_safe_url

    posts = get_ordered_carousel_posts_func(group_id, user_id=user_id)

    if not posts:
        return None

    first_post = posts[0]

    return {
        "post_type": "carousel",
        "group_id": group_id,
        "caption": first_post.caption,
        "prompt": first_post.prompt,
        "platforms": parse_platforms_func(first_post.platforms),
        "media": [
            {
                "post_id": post.id,
                "file_url": make_instagram_safe_url_func(post.file_url),
                "file_type": post.file_type,
                "sort_order": post.sort_order,
                "is_cover": post.is_cover,
            }
            for post in posts
        ],
    }


def send_payload_to_make(
    payload,
    webhook_url=None,
    *,
    make_webhook_single=None,
    make_webhook_carousel=None,
):
    if make_webhook_single is None:
        make_webhook_single = MAKE_WEBHOOK_SINGLE
    if make_webhook_carousel is None:
        make_webhook_carousel = MAKE_WEBHOOK_CAROUSEL

    if not webhook_url:
        if payload.get("post_type") == "carousel":
            webhook_url = make_webhook_carousel
        else:
            webhook_url = make_webhook_single

    if not webhook_url:
        raise Exception(
            f"No Make webhook configured for "
            f"{payload.get('post_type', 'unknown')} posts."
        )

    print("\n========== MAKE REQUEST ==========")
    print("Post type:", payload.get("post_type"))
    print("Webhook configured:", bool(webhook_url))
    print("Platforms:", payload.get("platforms"))
    print("Media count:", len(payload.get("media", [])))
    print("==================================")

    response = requests.post(
        webhook_url,
        json=payload,
        timeout=30,
    )

    print("Make status:", response.status_code)

    response.raise_for_status()

    return response


def publish_post_to_make(
    post,
    user_id,
    *,
    get_enabled_platforms_func=None,
    build_carousel_payload_func=None,
    get_user_make_webhook_func=None,
    send_payload_func=None,
    get_ordered_carousel_posts_func=None,
    build_single_payload_func=None,
    log_single_image_diagnostics_func=None,
    get_user_connected_accounts_func=None,
    publish_linkedin_text_post_func=None,
):
    if user_id is None:
        raise ValueError("user_id is required for publishing")

    if get_enabled_platforms_func is None:
        get_enabled_platforms_func = get_enabled_platforms_for_user
    if build_carousel_payload_func is None:
        build_carousel_payload_func = build_carousel_payload
    if get_user_make_webhook_func is None:
        get_user_make_webhook_func = get_user_make_webhook
    if send_payload_func is None:
        send_payload_func = send_payload_to_make
    if get_ordered_carousel_posts_func is None:
        get_ordered_carousel_posts_func = _get_ordered_carousel_posts
    if build_single_payload_func is None:
        build_single_payload_func = build_single_payload
    if log_single_image_diagnostics_func is None:
        log_single_image_diagnostics_func = lambda post, enabled_platforms: None
    if get_user_connected_accounts_func is None:
        get_user_connected_accounts_func = get_user_connected_accounts
    if publish_linkedin_text_post_func is None:
        publish_linkedin_text_post_func = linkedin_publishing.publish_text_only_post

    selected_platforms = [
        platform.strip().lower()
        for platform in (post.platforms or "").split(",")
        if platform.strip()
    ]
    linkedin_selected = "linkedin" in selected_platforms

    enabled_platforms = get_enabled_platforms_func(
        selected_platforms,
        user_id=user_id,
    )

    accounts = get_user_connected_accounts_func(user_id)
    if linkedin_selected:
        linkedin_publishing._validate_account(accounts, now_provider=utc_now)
        linkedin_publishing.validate_text_only_eligibility(post)

    make_enabled_platforms = [
        platform for platform in enabled_platforms if platform != "linkedin"
    ]

    if not enabled_platforms and not linkedin_selected:
        raise Exception(
            "No connected platforms are enabled for this post. "
            "Check Connected Accounts."
        )

    if post.group_id:
        if linkedin_selected:
            raise Exception("LinkedIn carousel publishing is not available yet.")

        payload = build_carousel_payload_func(
            post.group_id,
            user_id=user_id,
        )

        if not payload:
            raise Exception("Carousel payload could not be built.")

        webhook_url = get_user_make_webhook_func(
            "carousel",
            user_id=user_id,
        )

        if not webhook_url:
            raise Exception(
                "No carousel webhook is configured. "
                "Add it in Connected Accounts."
            )

        if not make_enabled_platforms:
            raise Exception(
                "No connected Make-supported platforms are enabled for this post. "
                "Check Connected Accounts."
            )

        payload["platforms"] = make_enabled_platforms
        response = send_payload_func(payload, webhook_url)

        group_posts = get_ordered_carousel_posts_func(
            post.group_id,
            user_id=user_id,
        )

        for group_post in group_posts:
            group_post.status = "sent_to_make"
            group_post.sent_at = utc_now()

        return response

    payload = build_single_payload_func(post)
    payload["platforms"] = make_enabled_platforms
    log_single_image_diagnostics_func(post, make_enabled_platforms)

    if "instagram" in make_enabled_platforms and not post.file_url:
        raise Exception("Instagram single-image posts require an image URL.")

    make_response = None
    linkedin_response = None

    if make_enabled_platforms:
        webhook_url = get_user_make_webhook_func(
            "single",
            user_id=user_id,
        )

        if not webhook_url:
            raise Exception(
                "No single-post webhook is configured. "
                "Add it in Connected Accounts."
            )

        make_response = send_payload_func(payload, webhook_url)

    if linkedin_selected:
        linkedin_response = publish_linkedin_text_post_func(post, accounts)

    if make_response is not None:
        post.status = "sent_to_make"
        post.sent_at = utc_now()
    elif linkedin_response is not None:
        post.status = "published"
        post.sent_at = utc_now()

    if make_response is not None:
        return make_response

    return linkedin_response
