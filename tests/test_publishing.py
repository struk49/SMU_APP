from datetime import timedelta
from io import BytesIO

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from conftest import (
    MockMakeResponse,
    create_accounts,
    create_carousel,
    create_post,
    create_user,
    login,
)
from smu_core.services.time_utils import utc_now


def test_manual_single_post_publishing(client, module, monkeypatch):
    user = create_user(module)
    create_accounts(module, user, facebook=True)
    post = create_post(module, user)
    login(client, user)

    sent = []

    def fake_post(url, json, timeout):
        sent.append((url, json, timeout))
        return MockMakeResponse()

    monkeypatch.setattr(module.requests, "post", fake_post)

    response = client.post(f"/send/{post.id}")

    assert response.status_code == 302
    assert len(sent) == 1
    assert sent[0][0] == "https://make.test/single"
    assert sent[0][1]["post_type"] == "single"
    assert sent[0][1]["post_id"] == post.id
    assert sent[0][1]["platforms"] == ["instagram", "facebook"]
    assert module.db.session.get(module.Post, post.id).status == "sent_to_make"


def test_scheduled_single_post_publishing(app, module, monkeypatch):
    user = create_user(module)
    create_accounts(module, user, facebook=True)
    post = create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=utc_now() - timedelta(minutes=1),
    )

    sent = []

    def fake_post(url, json, timeout):
        sent.append((url, json, timeout))
        return MockMakeResponse()

    monkeypatch.setattr(module.requests, "post", fake_post)

    module.check_scheduled_posts()
    module.db.session.expire_all()

    assert len(sent) == 1
    assert sent[0][0] == "https://make.test/single"
    assert sent[0][1]["post_type"] == "single"
    assert sent[0][1]["post_id"] == post.id
    assert sent[0][1]["platforms"] == ["instagram", "facebook"]
    assert module.db.session.get(module.Post, post.id).status == "sent_to_make"


def test_manual_carousel_publishing(client, module, monkeypatch):
    user = create_user(module)
    create_accounts(module, user)
    group_id, posts = create_carousel(module, user)
    login(client, user)

    sent = []

    def fake_post(url, json, timeout):
        sent.append((url, json, timeout))
        return MockMakeResponse()

    monkeypatch.setattr(module.requests, "post", fake_post)

    response = client.post(f"/send-carousel/{group_id}")

    assert response.status_code == 302
    assert len(sent) == 1
    assert sent[0][0] == "https://make.test/carousel"
    assert sent[0][1]["post_type"] == "carousel"
    assert sent[0][1]["group_id"] == group_id
    assert len(sent[0][1]["media"]) == 2
    assert [item["post_id"] for item in sent[0][1]["media"]] == [
        post.id for post in posts
    ]
    assert {
        module.db.session.get(module.Post, post.id).status for post in posts
    } == {"sent_to_make"}


def test_scheduled_carousel_publishing(app, module, monkeypatch):
    user = create_user(module)
    create_accounts(module, user)
    group_id, posts = create_carousel(module, user, status="scheduled", scheduled=True)

    sent = []

    def fake_post(url, json, timeout):
        sent.append((url, json, timeout))
        return MockMakeResponse()

    monkeypatch.setattr(module.requests, "post", fake_post)

    module.check_scheduled_posts()
    module.db.session.expire_all()

    assert len(sent) == 1
    assert sent[0][0] == "https://make.test/carousel"
    assert sent[0][1]["post_type"] == "carousel"
    assert sent[0][1]["group_id"] == group_id
    assert len(sent[0][1]["media"]) == 2
    assert {
        module.db.session.get(module.Post, post.id).status for post in posts
    } == {"sent_to_make"}


def test_missing_webhook_does_not_call_make(client, module, monkeypatch):
    user = create_user(module)
    create_accounts(module, user, single_webhook="", carousel_webhook="")
    post = create_post(module, user)
    login(client, user)

    def fake_post(url, json, timeout):
        pytest.fail("requests.post should not be called without a webhook")

    monkeypatch.setattr(module.requests, "post", fake_post)

    response = client.post(f"/send/{post.id}")

    assert response.status_code == 302
    assert module.db.session.get(module.Post, post.id).status == "draft"


def test_no_enabled_platforms_does_not_call_make(client, module, monkeypatch):
    user = create_user(module)
    create_accounts(module, user, instagram=False, facebook=False)
    post = create_post(module, user)
    login(client, user)

    def fake_post(url, json, timeout):
        pytest.fail("requests.post should not be called without enabled platforms")

    monkeypatch.setattr(module.requests, "post", fake_post)

    response = client.post(f"/send/{post.id}")

    assert response.status_code == 302
    assert module.db.session.get(module.Post, post.id).status == "draft"


def test_make_non_2xx_keeps_post_unsent(client, module, monkeypatch):
    user = create_user(module)
    create_accounts(module, user)
    post = create_post(module, user)
    login(client, user)

    sent = []

    def fake_post(url, json, timeout):
        sent.append((url, json, timeout))
        return MockMakeResponse(status_code=500)

    monkeypatch.setattr(module.requests, "post", fake_post)

    response = client.post(f"/send/{post.id}")

    assert response.status_code == 302
    assert len(sent) == 1
    assert module.db.session.get(module.Post, post.id).status == "draft"


def test_cross_user_carousel_access_does_not_publish(client, module, monkeypatch):
    owner = create_user(module, email="owner@example.com")
    attacker = create_user(module, email="attacker@example.com")
    create_accounts(module, owner)
    create_accounts(module, attacker)
    group_id, posts = create_carousel(module, owner)
    login(client, attacker)

    def fake_post(url, json, timeout):
        pytest.fail("cross-user carousel publish should not call requests.post")

    monkeypatch.setattr(module.requests, "post", fake_post)

    response = client.post(f"/send-carousel/{group_id}")

    assert response.status_code == 302
    assert {
        module.db.session.get(module.Post, post.id).status for post in posts
    } == {"draft"}


def test_sent_single_post_cannot_be_sent_again(client, module, monkeypatch):
    user = create_user(module)
    create_accounts(module, user)
    post = create_post(module, user, status="sent_to_make")
    login(client, user)

    def fake_post(url, json, timeout):
        pytest.fail("requests.post should not be called for an already sent post")

    monkeypatch.setattr(module.requests, "post", fake_post)

    response = client.post(f"/send/{post.id}")

    assert response.status_code == 302
    assert module.db.session.get(module.Post, post.id).status == "sent_to_make"

    with client.session_transaction() as session:
        assert (
            "message",
            "This post has already been sent to Make.",
        ) in session["_flashes"]


def test_sent_carousel_cannot_be_sent_again(client, module, monkeypatch):
    user = create_user(module)
    create_accounts(module, user)
    group_id, posts = create_carousel(module, user, status="sent_to_make")
    login(client, user)

    def fake_post(url, json, timeout):
        pytest.fail("requests.post should not be called for an already sent carousel")

    monkeypatch.setattr(module.requests, "post", fake_post)

    response = client.post(f"/send-carousel/{group_id}")

    assert response.status_code == 302
    assert {
        module.db.session.get(module.Post, post.id).status for post in posts
    } == {"sent_to_make"}

    with client.session_transaction() as session:
        assert (
            "message",
            "This post has already been sent to Make.",
        ) in session["_flashes"]


def test_fresh_scheduled_single_post_publishes_once(client, module, monkeypatch):
    user = create_user(module)
    create_accounts(module, user, facebook=True)
    post = create_post(module, user)
    login(client, user)
    due_local = utc_now() - timedelta(minutes=2)

    sent = []

    def fake_post(url, json, timeout):
        sent.append((url, json, timeout))
        return MockMakeResponse()

    monkeypatch.setattr(module.requests, "post", fake_post)

    response = client.post(
        f"/schedule/{post.id}",
        data={"scheduled_time": due_local.strftime("%Y-%m-%dT%H:%M")},
    )

    scheduled_post = module.db.session.get(module.Post, post.id)

    assert response.status_code == 302
    assert scheduled_post.status == "scheduled"
    assert scheduled_post.scheduled_time is not None
    assert scheduled_post.user_id == user.id

    module.check_scheduled_posts()
    module.db.session.expire_all()

    assert len(sent) == 1
    assert sent[0][1]["post_type"] == "single"
    assert sent[0][1]["post_id"] == post.id
    assert module.db.session.get(module.Post, post.id).status == "sent_to_make"


def test_fresh_scheduled_manual_carousel_publishes_once(client, module, monkeypatch):
    user = create_user(module)
    create_accounts(module, user)
    group_id, posts = create_carousel(module, user)
    login(client, user)
    due_local = utc_now() - timedelta(minutes=2)

    sent = []

    def fake_post(url, json, timeout):
        sent.append((url, json, timeout))
        return MockMakeResponse()

    monkeypatch.setattr(module.requests, "post", fake_post)

    response = client.post(
        f"/schedule/{posts[0].id}",
        data={"scheduled_time": due_local.strftime("%Y-%m-%dT%H:%M")},
    )

    assert response.status_code == 302

    for post in posts:
        scheduled_post = module.db.session.get(module.Post, post.id)
        assert scheduled_post.status == "scheduled"
        assert scheduled_post.scheduled_time is not None
        assert scheduled_post.user_id == user.id
        assert scheduled_post.group_id == group_id

    module.check_scheduled_posts()
    module.db.session.expire_all()

    assert len(sent) == 1
    assert sent[0][1]["post_type"] == "carousel"
    assert sent[0][1]["group_id"] == group_id
    assert {
        module.db.session.get(module.Post, post.id).status for post in posts
    } == {"sent_to_make"}


def test_scheduled_tiktok_carousel_publishes_once(client, module, monkeypatch):
    user = create_user(module)
    create_accounts(module, user)
    group_id, posts = create_carousel(module, user)
    login(client, user)
    due_local = utc_now() - timedelta(minutes=2)

    for post in posts:
        post.prompt = "TikTok carousel slide"
        post.platforms = "instagram,facebook"

    module.db.session.commit()

    sent = []

    def fake_post(url, json, timeout):
        sent.append((url, json, timeout))
        return MockMakeResponse()

    monkeypatch.setattr(module.requests, "post", fake_post)

    response = client.post(
        f"/schedule/{posts[0].id}",
        data={"scheduled_time": due_local.strftime("%Y-%m-%dT%H:%M")},
    )

    assert response.status_code == 302

    module.check_scheduled_posts()
    module.db.session.expire_all()

    assert len(sent) == 1
    assert sent[0][1]["post_type"] == "carousel"
    assert sent[0][1]["group_id"] == group_id


def test_single_instagram_payload_contains_required_image_url_field(
    client,
    module,
    monkeypatch,
):
    user = create_user(module)
    create_accounts(module, user, instagram=True, facebook=False)
    post = create_post(module, user, platforms="instagram")
    login(client, user)

    sent = []

    def fake_post(url, json, timeout):
        sent.append(json)
        return MockMakeResponse()

    monkeypatch.setattr(module.requests, "post", fake_post)

    client.post(f"/send/{post.id}")

    assert len(sent) == 1
    assert sent[0]["post_type"] == "single"
    assert sent[0]["file_url"] == post.file_url
    assert sent[0]["file_url"].startswith("https://")


def test_instagram_remains_in_enabled_platforms_when_selected_and_connected(
    client,
    module,
    monkeypatch,
):
    user = create_user(module)
    create_accounts(module, user, instagram=True, facebook=False)
    post = create_post(module, user, platforms="instagram,facebook")
    login(client, user)

    sent = []

    def fake_post(url, json, timeout):
        sent.append(json)
        return MockMakeResponse()

    monkeypatch.setattr(module.requests, "post", fake_post)

    client.post(f"/send/{post.id}")

    assert len(sent) == 1
    assert sent[0]["platforms"] == ["instagram"]


def test_no_empty_image_url_is_sent_for_instagram_single_post(
    client,
    module,
    monkeypatch,
):
    user = create_user(module)
    create_accounts(module, user, instagram=True, facebook=False)
    post = create_post(module, user, platforms="instagram")
    post.file_url = ""
    module.db.session.commit()
    login(client, user)

    def fake_post(url, json, timeout):
        pytest.fail("requests.post should not be called with an empty image URL")

    monkeypatch.setattr(module.requests, "post", fake_post)

    response = client.post(f"/send/{post.id}")

    assert response.status_code == 302
    assert module.db.session.get(module.Post, post.id).status == "draft"


def make_png_file(mode="RGBA", color=(255, 0, 0, 128)):
    image = Image.new(mode, (4, 4), color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return FileStorage(stream=buffer, filename="source.png", content_type="image/png")


def test_rgba_png_becomes_rgb_jpeg(module):
    source = make_png_file()

    result = module.normalize_image_to_jpeg(source)

    assert result["source_format"] == "PNG"
    assert result["final_format"] == "JPEG"
    assert result["final_mode"] == "RGB"

    converted = Image.open(BytesIO(result["bytes"]))
    assert converted.format == "JPEG"
    assert converted.mode == "RGB"


def test_transparent_areas_are_safely_flattened(module):
    source = make_png_file(color=(0, 255, 0, 0))

    result = module.normalize_image_to_jpeg(source)
    converted = Image.open(BytesIO(result["bytes"]))

    assert converted.getpixel((0, 0)) == (255, 255, 255)


def test_instagram_single_upload_receives_jpeg_url(
    client,
    module,
    monkeypatch,
):
    user = create_user(module)
    create_accounts(module, user, instagram=True, facebook=False)
    login(client, user)

    def fake_upload(file_or_url, **kwargs):
        image = Image.open(file_or_url)
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert kwargs["resource_type"] == "image"
        assert kwargs["format"] == "jpg"
        return {"secure_url": "https://cdn.test/instagram-single.jpg"}

    monkeypatch.setattr(module.cloudinary.uploader, "upload", fake_upload)

    response = client.post(
        "/create",
        data={
            "media": make_png_file(),
            "platforms": ["instagram"],
            "caption": "Caption",
        },
        content_type="multipart/form-data",
    )

    post = module.Post.query.first()

    assert response.status_code == 302
    assert post.file_url.endswith(".jpg")


def test_instagram_carousel_media_urls_are_jpeg(
    client,
    module,
    monkeypatch,
):
    user = create_user(module)
    create_accounts(module, user, instagram=True, facebook=False)
    login(client, user)
    uploads = []

    def fake_upload(file_or_url, **kwargs):
        image = Image.open(file_or_url)
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        uploads.append(kwargs)
        return {"secure_url": f"https://cdn.test/carousel-{len(uploads)}.jpg"}

    monkeypatch.setattr(module.cloudinary.uploader, "upload", fake_upload)

    response = client.post(
        "/create",
        data={
            "media": [make_png_file(), make_png_file()],
            "make_carousel": "on",
            "platforms": ["instagram"],
            "caption": "Caption",
        },
        content_type="multipart/form-data",
    )

    posts = module.Post.query.order_by(module.Post.sort_order.asc()).all()

    assert response.status_code == 302
    assert len(posts) == 2
    assert {post.file_url.endswith(".jpg") for post in posts} == {True}


def test_facebook_only_upload_preserves_existing_upload_path(
    client,
    module,
    monkeypatch,
):
    user = create_user(module)
    create_accounts(module, user, instagram=False, facebook=True)
    login(client, user)

    def fake_upload(file_or_url, **kwargs):
        assert kwargs["resource_type"] == "auto"
        assert "format" not in kwargs
        return {"secure_url": "https://cdn.test/facebook-only.png"}

    monkeypatch.setattr(module.cloudinary.uploader, "upload", fake_upload)

    response = client.post(
        "/create",
        data={
            "media": make_png_file(),
            "platforms": ["facebook"],
            "caption": "Caption",
        },
        content_type="multipart/form-data",
    )

    post = module.Post.query.first()

    assert response.status_code == 302
    assert post.file_url.endswith(".png")


def test_scheduler_still_publishes_after_jpeg_normalisation(
    app,
    module,
    monkeypatch,
):
    user = create_user(module)
    create_accounts(module, user, instagram=True)
    post = create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=utc_now() - timedelta(minutes=1),
        file_url="https://cdn.test/scheduled.jpg",
        platforms="instagram",
    )
    sent = []

    def fake_post(url, json, timeout):
        sent.append(json)
        return MockMakeResponse()

    monkeypatch.setattr(module.requests, "post", fake_post)

    module.check_scheduled_posts()
    module.db.session.expire_all()

    assert len(sent) == 1
    assert sent[0]["file_url"].endswith(".jpg")
    assert module.db.session.get(module.Post, post.id).status == "sent_to_make"
