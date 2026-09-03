from datetime import timedelta
from html import unescape
from io import BytesIO

from conftest import create_post, create_user, login
from smu_core.services.time_utils import utc_now


CONTENT_PACK_RESULT = """INSTAGRAM_CAPTION:
Instagram caption

FACEBOOK_POST:
Facebook caption

CAROUSEL_IDEA:
Slide 1: First slide
Slide 2: Second slide

IMAGE_PROMPT:
Image prompt

HASHTAGS:
#one #two
"""


def set_create_helper(app, monkeypatch, name, helper):
    monkeypatch.setitem(app.extensions["smu_post_create_helpers"], name, helper)


def set_edit_helper(app, monkeypatch, name, helper):
    monkeypatch.setitem(app.extensions["smu_post_edit_helpers"], name, helper)


def set_content_pack_helper(app, monkeypatch, name, helper):
    monkeypatch.setitem(app.extensions["smu_content_pack_helpers"], name, helper)


def set_tiktok_helper(app, monkeypatch, name, helper):
    monkeypatch.setitem(app.extensions["smu_tiktok_helpers"], name, helper)


def add_usage(module, user, *, plan="starter", images=0, packs=0):
    user_usage = module.UserUsage(
        user_id=user.id,
        plan=plan,
        ai_images_used=images,
        content_packs_used=packs,
        usage_period_start=utc_now() - timedelta(days=1),
        usage_period_end=utc_now() + timedelta(days=30),
    )
    module.db.session.add(user_usage)
    module.db.session.commit()
    return user_usage


def test_single_ai_image_creation_consumes_one_credit(client, app, module, monkeypatch):
    user = create_user(module)
    login(client, user)
    set_create_helper(app, monkeypatch, "build_brand_context", lambda user_id: "")
    set_create_helper(app, monkeypatch, "apply_image_style", lambda prompt, style: prompt)
    set_create_helper(
        app,
        monkeypatch,
        "generate_multiple_openai_images",
        lambda prompt, count: ["https://cdn.test/generated.jpg"],
    )

    response = client.post(
        "/create",
        data={
            "prompt": "Create image",
            "caption": "Caption",
            "platforms": ["instagram"],
        },
    )
    user_usage = module.UserUsage.query.filter_by(user_id=user.id).one()

    assert response.status_code == 302
    assert module.Post.query.count() == 1
    assert user_usage.ai_images_used == 1


def test_uploaded_image_creation_does_not_consume_ai_credit(
    client, app, module, monkeypatch
):
    user = create_user(module)
    login(client, user)
    set_create_helper(
        app,
        monkeypatch,
        "upload_to_cloudinary",
        lambda file, force_jpeg=False: {"secure_url": "https://cdn.test/uploaded.jpg"},
    )

    response = client.post(
        "/create",
        data={
            "media": (BytesIO(b"image"), "upload.png"),
            "caption": "Caption",
            "platforms": ["instagram"],
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    assert module.Post.query.count() == 1
    assert module.UserUsage.query.filter_by(user_id=user.id).first() is None


def test_image_limit_blocks_generation_without_openai_call(
    client, app, module, monkeypatch
):
    user = create_user(module)
    add_usage(module, user, images=20)
    login(client, user)
    calls = []
    set_create_helper(
        app,
        monkeypatch,
        "generate_multiple_openai_images",
        lambda prompt, count: calls.append((prompt, count)),
    )

    response = client.post(
        "/create",
        data={
            "prompt": "Create image",
            "caption": "Caption",
        },
        follow_redirects=True,
    )
    user_usage = module.UserUsage.query.filter_by(user_id=user.id).one()
    html = response.get_data(as_text=True)
    text = unescape(html)

    assert response.status_code == 200
    assert "You've used all 20 AI image credits for this billing period." in text
    assert calls == []
    assert module.Post.query.count() == 0
    assert user_usage.ai_images_used == 20


def test_failed_ai_image_generation_releases_reserved_credit(
    client, app, module, monkeypatch
):
    user = create_user(module)
    login(client, user)
    set_create_helper(app, monkeypatch, "build_brand_context", lambda user_id: "")
    set_create_helper(app, monkeypatch, "apply_image_style", lambda prompt, style: prompt)

    def fail_generation(prompt, count):
        raise RuntimeError("image failed")

    set_create_helper(app, monkeypatch, "generate_multiple_openai_images", fail_generation)

    response = client.post(
        "/create",
        data={
            "prompt": "Create image",
            "caption": "Caption",
        },
    )
    user_usage = module.UserUsage.query.filter_by(user_id=user.id).one()

    assert response.status_code == 302
    assert module.Post.query.count() == 0
    assert user_usage.ai_images_used == 0


def test_edit_regenerate_consumes_one_ai_image_credit(
    client, app, module, monkeypatch
):
    user = create_user(module)
    post = create_post(module, user)
    login(client, user)
    set_edit_helper(
        app,
        monkeypatch,
        "generate_openai_image",
        lambda prompt: "https://cdn.test/regenerated.jpg",
    )

    response = client.post(
        f"/edit-post/{post.id}",
        data={
            "caption": "Updated",
            "prompt": "New prompt",
            "platforms": ["facebook"],
            "regenerate_image": "on",
        },
    )
    user_usage = module.UserUsage.query.filter_by(user_id=user.id).one()

    assert response.status_code == 302
    assert user_usage.ai_images_used == 1
    assert module.db.session.get(module.Post, post.id).file_url == (
        "https://cdn.test/regenerated.jpg"
    )


def test_content_pack_generation_consumes_one_content_pack_credit(
    client, app, module, monkeypatch
):
    user = create_user(module)
    login(client, user)
    set_content_pack_helper(app, monkeypatch, "build_brand_context", lambda user_id: "")
    set_content_pack_helper(
        app,
        monkeypatch,
        "generate_content_pack",
        lambda source_text, brand_context: CONTENT_PACK_RESULT,
    )

    response = client.post(
        "/content-pack",
        data={"source_type": "text", "source_input": "Topic"},
    )
    user_usage = module.UserUsage.query.filter_by(user_id=user.id).one()

    assert response.status_code == 200
    assert user_usage.content_packs_used == 1


def test_content_pack_limit_blocks_generation_without_openai_call(
    client, app, module, monkeypatch
):
    user = create_user(module)
    add_usage(module, user, packs=10)
    login(client, user)
    calls = []
    set_content_pack_helper(
        app,
        monkeypatch,
        "generate_content_pack",
        lambda source_text, brand_context: calls.append(source_text),
    )

    response = client.post(
        "/content-pack",
        data={"source_type": "text", "source_input": "Topic"},
        follow_redirects=True,
    )
    user_usage = module.UserUsage.query.filter_by(user_id=user.id).one()
    html = response.get_data(as_text=True)
    text = unescape(html)

    assert response.status_code == 200
    assert "You've used all 10 Content Pack credits for this billing period." in text
    assert calls == []
    assert user_usage.content_packs_used == 10


def test_content_pack_generation_failure_releases_reserved_credit(
    client, app, module, monkeypatch
):
    user = create_user(module)
    login(client, user)
    set_content_pack_helper(app, monkeypatch, "build_brand_context", lambda user_id: "")

    def fail_content_pack(source_text, brand_context):
        raise RuntimeError("content failed")

    set_content_pack_helper(app, monkeypatch, "generate_content_pack", fail_content_pack)

    response = client.post(
        "/content-pack",
        data={"source_type": "text", "source_input": "Topic"},
    )
    user_usage = module.UserUsage.query.filter_by(user_id=user.id).one()

    assert response.status_code == 200
    assert user_usage.content_packs_used == 0


def test_platform_draft_from_content_pack_does_not_consume_new_content_pack_credit(
    client, app, module, monkeypatch
):
    user = create_user(module)
    login(client, user)
    set_content_pack_helper(
        app,
        monkeypatch,
        "get_placeholder_image_url",
        lambda: "https://cdn.test/placeholder.jpg",
    )

    response = client.post(
        "/content-pack/create-platform-draft",
        data={
            "content_pack_result": CONTENT_PACK_RESULT,
            "platform": "instagram",
        },
    )

    assert response.status_code == 302
    assert module.Post.query.count() == 1
    assert module.UserUsage.query.filter_by(user_id=user.id).first() is None


def test_tiktok_single_draft_consumes_one_ai_image_credit(
    client, app, module, monkeypatch
):
    user = create_user(module)
    login(client, user)
    set_tiktok_helper(app, monkeypatch, "apply_image_style", lambda prompt, style: prompt)
    set_tiktok_helper(
        app,
        monkeypatch,
        "generate_openai_image",
        lambda prompt: "https://cdn.test/tiktok.jpg",
    )

    response = client.post(
        "/tiktok/create-draft",
        data={
            "caption": "Caption",
            "image_prompt": "Image prompt",
        },
    )
    user_usage = module.UserUsage.query.filter_by(user_id=user.id).one()

    assert response.status_code == 302
    assert module.Post.query.count() == 1
    assert user_usage.ai_images_used == 1


def test_tiktok_single_draft_db_failure_releases_reserved_credit(
    client, app, module, monkeypatch
):
    user = create_user(module)
    login(client, user)
    original_commit = module.db.session.commit
    commit_calls = []
    set_tiktok_helper(app, monkeypatch, "apply_image_style", lambda prompt, style: prompt)
    set_tiktok_helper(
        app,
        monkeypatch,
        "generate_openai_image",
        lambda prompt: "https://cdn.test/tiktok.jpg",
    )

    def flaky_commit():
        commit_calls.append("commit")
        if len(commit_calls) == 3:
            raise RuntimeError("db failed")
        return original_commit()

    monkeypatch.setattr(module.db.session, "commit", flaky_commit)

    response = client.post(
        "/tiktok/create-draft",
        data={
            "caption": "Caption",
            "image_prompt": "Image prompt",
        },
    )
    user_usage = module.UserUsage.query.filter_by(user_id=user.id).one()

    assert response.status_code == 302
    assert module.Post.query.count() == 0
    assert user_usage.ai_images_used == 0
