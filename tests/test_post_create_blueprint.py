from io import BytesIO

from flask import template_rendered, url_for

import app as smu_app
from conftest import create_user, login
from smu_core.models import Post


def rules_for(app, path):
    return [rule for rule in app.url_map.iter_rules() if rule.rule == path]


def set_create_helper(app, monkeypatch, name, helper):
    monkeypatch.setitem(app.extensions["smu_post_create_helpers"], name, helper)


def test_create_route_registered_once_with_old_endpoint(module):
    rules = rules_for(module.app, "/create")

    assert "posts" in module.app.blueprints
    assert list(module.app.blueprints).count("posts") == 1
    assert len(rules) == 1
    assert rules[0].endpoint == "create_post"
    assert {"GET", "POST"}.issubset(rules[0].methods)


def test_create_url_for_compatibility(module):
    with module.app.test_request_context():
        assert url_for("create_post") == "/create"


def test_create_requires_login_for_get_and_post(client):
    get_response = client.get("/create")
    post_response = client.post("/create")

    assert get_response.status_code == 302
    assert "/login" in get_response.location
    assert post_response.status_code == 302
    assert "/login" in post_response.location


def test_create_get_renders_template_and_calendar_default(client, app, module):
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append((template.name, context))

    user = create_user(module)
    login(client, user)
    template_rendered.connect(record, app)
    try:
        response = client.get("/create?scheduled_date=2026-07-14")
    finally:
        template_rendered.disconnect(record, app)

    assert response.status_code == 200
    assert recorded[0][0] == "create_post.html"
    assert recorded[0][1]["default_scheduled_time"] == "2026-07-14T09:00"


def test_single_upload_creates_one_post_without_real_cloudinary(client, app, module, monkeypatch):
    user = create_user(module)
    calls = []

    def fake_upload(file, force_jpeg=False):
        calls.append((file.filename, force_jpeg))
        return {"secure_url": "https://cdn.test/uploaded.jpg"}

    set_create_helper(app, monkeypatch, "upload_to_cloudinary", fake_upload)
    login(client, user)

    response = client.post(
        "/create",
        data={
            "media": (BytesIO(b"image"), "single.png"),
            "prompt": "  Prompt  ",
            "caption": "  Caption  ",
            "platforms": ["instagram", "pinterest"],
        },
        content_type="multipart/form-data",
    )
    post = Post.query.one()

    assert response.status_code == 302
    assert response.location.endswith(f"/post/{post.id}")
    assert calls == [("single.png", True)]
    assert post.file_url == "https://cdn.test/uploaded.jpg"
    assert post.file_type == "image"
    assert post.prompt == "Prompt"
    assert post.caption == "Caption"
    assert post.platforms == "instagram,pinterest"
    assert post.status == "draft"
    assert post.post_type == "single"
    assert post.group_id is None
    assert post.sort_order == 0
    assert post.is_cover is False
    assert post.user_id == user.id


def test_single_ai_generation_creates_one_post_without_real_openai(client, app, module, monkeypatch):
    user = create_user(module)

    set_create_helper(app, monkeypatch, "build_brand_context", lambda user_id: "BRAND")
    set_create_helper(app, monkeypatch, "apply_image_style", lambda prompt, style: f"{prompt}\nSTYLE:{style}")
    set_create_helper(
        app,
        monkeypatch,
        "generate_multiple_openai_images",
        lambda prompt, count: ["https://cdn.test/generated.jpg"],
    )
    login(client, user)

    response = client.post(
        "/create",
        data={
            "prompt": "Make a launch image",
            "caption": "Launch caption",
            "platforms": ["facebook"],
            "image_style": "minimal",
        },
    )
    post = Post.query.one()

    assert response.status_code == 302
    assert response.location.endswith(f"/post/{post.id}")
    assert post.file_url == "https://cdn.test/generated.jpg"
    assert post.file_type == "image"
    assert "Make a launch image" in post.prompt
    assert "STYLE:minimal" in post.prompt
    assert post.caption == "Launch caption"
    assert post.platforms == "facebook"
    assert post.status == "draft"
    assert post.post_type == "single"
    assert post.group_id is None
    assert post.sort_order == 0
    assert post.is_cover is True
    assert post.user_id == user.id


def test_carousel_upload_creates_grouped_posts_in_cover_order(client, app, module, monkeypatch):
    user = create_user(module)
    upload_calls = []

    def fake_upload(file, force_jpeg=False):
        upload_calls.append((file.filename, force_jpeg))
        return {"secure_url": f"https://cdn.test/{file.filename}.jpg"}

    set_create_helper(app, monkeypatch, "upload_to_cloudinary", fake_upload)
    login(client, user)

    response = client.post(
        "/create",
        data={
            "media": [
                (BytesIO(b"one"), "one.png"),
                (BytesIO(b"two"), "two.png"),
                (BytesIO(b"three"), "three.png"),
            ],
            "caption": "Carousel caption",
            "prompt": "Carousel prompt",
            "platforms": ["instagram", "facebook"],
            "make_carousel": "on",
            "carousel_order": "[0,1,2]",
            "cover_index": "1",
        },
        content_type="multipart/form-data",
    )
    posts = Post.query.order_by(Post.sort_order).all()

    assert response.status_code == 302
    assert response.location.endswith("/")
    assert [call[0] for call in upload_calls] == ["two.png", "one.png", "three.png"]
    assert all(call[1] is True for call in upload_calls)
    assert len(posts) == 3
    assert len({post.group_id for post in posts}) == 1
    assert [post.sort_order for post in posts] == [0, 1, 2]
    assert [post.is_cover for post in posts] == [True, False, False]
    assert {post.post_type for post in posts} == {"carousel"}
    assert {post.caption for post in posts} == {"Carousel caption"}
    assert {post.prompt for post in posts} == {"Carousel prompt"}
    assert {post.platforms for post in posts} == {"instagram,facebook"}
    assert {post.status for post in posts} == {"draft"}
    assert {post.user_id for post in posts} == {user.id}


def test_carousel_ai_generation_creates_requested_group_without_real_openai(
    client,
    app,
    module,
    monkeypatch,
):
    user = create_user(module)

    set_create_helper(app, monkeypatch, "build_brand_context", lambda user_id: "")
    set_create_helper(app, monkeypatch, "apply_image_style", lambda prompt, style: prompt)
    set_create_helper(
        app,
        monkeypatch,
        "generate_multiple_openai_images",
        lambda prompt, count: [f"https://cdn.test/generated-{i}.jpg" for i in range(count)],
    )
    login(client, user)

    response = client.post(
        "/create",
        data={
            "prompt": "Carousel idea",
            "caption": "AI carousel caption",
            "make_carousel": "on",
        },
    )
    posts = Post.query.order_by(Post.sort_order).all()

    assert response.status_code == 302
    assert response.location.endswith("/")
    assert len(posts) == 3
    assert len({post.group_id for post in posts}) == 1
    assert [post.file_url for post in posts] == [
        "https://cdn.test/generated-0.jpg",
        "https://cdn.test/generated-1.jpg",
        "https://cdn.test/generated-2.jpg",
    ]
    assert [post.sort_order for post in posts] == [0, 1, 2]
    assert [post.is_cover for post in posts] == [True, False, False]
    assert {post.post_type for post in posts} == {"carousel"}
    assert {post.platforms for post in posts} == {"instagram,facebook"}


def test_missing_prompt_and_file_preserves_validation_response(client, module):
    user = create_user(module)
    login(client, user)

    response = client.post("/create", data={}, follow_redirects=True)

    assert response.status_code == 200
    assert "Upload a file or enter a prompt." in response.get_data(as_text=True)
    assert Post.query.count() == 0


def test_unsupported_upload_type_preserves_error_response(client, app, module, monkeypatch):
    user = create_user(module)
    upload_calls = []

    set_create_helper(
        app,
        monkeypatch,
        "upload_to_cloudinary",
        lambda *args, **kwargs: upload_calls.append(args) or {"secure_url": "nope"},
    )
    login(client, user)

    response = client.post(
        "/create",
        data={
            "media": (BytesIO(b"text"), "notes.txt"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Failed: Unsupported file type: txt" in response.get_data(as_text=True)
    assert upload_calls == []
    assert Post.query.count() == 0


def test_invalid_carousel_image_count_does_not_create_posts(client, app, module, monkeypatch):
    user = create_user(module)
    upload_calls = []

    set_create_helper(
        app,
        monkeypatch,
        "upload_to_cloudinary",
        lambda *args, **kwargs: upload_calls.append(args) or {"secure_url": "nope"},
    )
    login(client, user)

    media = [(BytesIO(str(index).encode()), f"{index}.png") for index in range(11)]
    response = client.post(
        "/create",
        data={
            "media": media,
            "make_carousel": "on",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Instagram carousel posts can only contain up to 10 images." in response.get_data(as_text=True)
    assert upload_calls == []
    assert Post.query.count() == 0


def test_upload_failure_preserves_current_error_response(client, app, module, monkeypatch):
    user = create_user(module)

    def fail_upload(*args, **kwargs):
        raise RuntimeError("upload failed")

    set_create_helper(app, monkeypatch, "upload_to_cloudinary", fail_upload)
    login(client, user)

    response = client.post(
        "/create",
        data={
            "media": (BytesIO(b"image"), "single.png"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Failed: upload failed" in response.get_data(as_text=True)
    assert Post.query.count() == 0


def test_create_helper_bridge_and_model_compatibility(module):
    helpers = module.app.extensions["smu_post_create_helpers"]

    for name in {
        "convert_uk_time_to_utc",
        "build_brand_context",
        "apply_image_style",
        "generate_multiple_openai_images",
        "get_file_type",
        "upload_to_cloudinary",
        "is_instagram_selected",
    }:
        assert callable(helpers[name])

    assert smu_app.Post is Post


def test_existing_post_endpoints_and_bridges_remain_registered(module):
    assert "smu_post_detail_helpers" in module.app.extensions
    assert "smu_post_edit_helpers" in module.app.extensions
    assert "smu_post_delete_duplicate_helpers" in module.app.extensions
    assert "smu_post_create_helpers" in module.app.extensions

    expected = {
        "/post/<int:post_id>": "view_post",
        "/edit-post/<int:post_id>": "edit_post",
        "/edit-carousel/<group_id>": "edit_carousel",
        "/delete/<int:post_id>": "delete_post",
        "/delete-carousel/<group_id>": "delete_carousel",
        "/duplicate-post/<int:post_id>": "duplicate_post",
        "/duplicate-carousel/<group_id>": "duplicate_carousel",
        "/rewrite-caption/<int:post_id>": "rewrite_caption",
        "/rewrite-carousel-caption/<group_id>": "rewrite_carousel_caption",
        "/send/<int:post_id>": "send_to_make",
        "/send-carousel/<group_id>": "send_carousel_to_make",
        "/schedule/<int:post_id>": "schedule_post",
        "/post/<int:post_id>/studio": "post_studio",
    }

    for path, endpoint in expected.items():
        rules = rules_for(module.app, path)

        assert len(rules) == 1
        assert rules[0].endpoint == endpoint
