from datetime import timedelta

from conftest import create_accounts, create_user, login
from smu_core.services.tiktok import TikTokRepurposeResult
from smu_core.services.time_utils import utc_now


def set_tiktok_helper(app, monkeypatch, name, helper):
    monkeypatch.setitem(app.extensions["smu_tiktok_helpers"], name, helper)


def set_studio_helper(app, monkeypatch, name, helper):
    monkeypatch.setitem(app.extensions["smu_studio_helpers"], name, helper)


def tiktok_result(**overrides):
    values = {
        "instagram_caption": "Instagram caption from transcript",
        "facebook_caption": "Facebook caption from transcript",
        "carousel_idea": "\n".join(
            [
                "Slide 1: Hook",
                "Slide 2: Proof",
                "Slide 3: Tip",
                "Slide 4: Example",
                "Slide 5: Action",
                "Slide 6: CTA",
            ]
        ),
        "image_prompt": "Bright social image prompt",
        "hashtags": "#smu #social",
    }
    values.update(overrides)
    return TikTokRepurposeResult(**values)


def test_tiktok_single_draft_full_offline_workflow(client, app, module, monkeypatch):
    user = create_user(module)
    login(client, user)
    calls = {}

    def fake_extract(url):
        calls["url"] = url
        return "Mock transcript"

    def fake_generate_image(prompt):
        calls["image_prompt"] = prompt
        return "https://cdn.test/tiktok-single.jpg"

    set_tiktok_helper(
        app,
        monkeypatch,
        "extract_tiktok_transcript",
        fake_extract,
    )
    set_tiktok_helper(app, monkeypatch, "build_brand_context", lambda user_id: "Brand")
    set_tiktok_helper(app, monkeypatch, "repurpose_tiktok_content", lambda *_: tiktok_result())
    set_tiktok_helper(
        app,
        monkeypatch,
        "apply_image_style",
        lambda prompt, style: f"{style}:{prompt}",
    )
    set_tiktok_helper(
        app,
        monkeypatch,
        "generate_openai_image",
        fake_generate_image,
    )

    repurpose_response = client.post(
        "/tiktok",
        data={"tiktok_url": "https://www.tiktok.com/@creator/video/123"},
    )
    repurpose_html = repurpose_response.get_data(as_text=True)

    assert repurpose_response.status_code == 200
    assert "Instagram caption from transcript" in repurpose_html
    assert "Facebook caption from transcript" in repurpose_html
    assert "Bright social image prompt" in repurpose_html
    assert "#smu #social" in repurpose_html

    draft_response = client.post(
        "/tiktok/create-draft",
        data={
            "caption": "Instagram caption from transcript\n\n#smu #social",
            "image_prompt": "Bright social image prompt",
            "image_style": "viral_carousel",
        },
    )
    post = module.Post.query.one()

    assert draft_response.status_code == 302
    assert draft_response.location.endswith(f"/post/{post.id}")
    assert post.user_id == user.id
    assert post.file_url == "https://cdn.test/tiktok-single.jpg"
    assert post.file_type == "image"
    assert post.prompt == "viral_carousel:Bright social image prompt"
    assert post.caption == "Instagram caption from transcript\n\n#smu #social"
    assert post.platforms == "instagram,facebook"
    assert post.post_type == "single"
    assert post.status == "draft"

    detail_response = client.get(f"/post/{post.id}")
    studio_response = client.get(f"/post/{post.id}/studio")

    assert detail_response.status_code == 200
    assert "Post #" in detail_response.get_data(as_text=True)
    assert studio_response.status_code == 200


def test_tiktok_carousel_workflow_and_batched_worker(client, app, module, monkeypatch):
    user = create_user(module)
    login(client, user)

    set_tiktok_helper(app, monkeypatch, "build_brand_context", lambda user_id: "Brand")
    set_tiktok_helper(app, monkeypatch, "apply_image_style", lambda prompt, style: prompt)
    set_tiktok_helper(
        app,
        monkeypatch,
        "get_placeholder_image_url",
        lambda: "https://cdn.test/placeholder.jpg",
    )

    response = client.post(
        "/tiktok/create-carousel-draft",
        data={
            "caption": "Carousel caption",
            "image_prompt": "Carousel visual prompt",
            "image_style": "viral_carousel",
            "carousel_idea": tiktok_result().carousel_idea,
        },
    )
    posts = module.Post.query.order_by(module.Post.sort_order.asc()).all()
    group_id = posts[0].group_id
    generated_prompts = []

    assert response.status_code == 302
    assert len(posts) == 6
    assert {post.status for post in posts} == {"generating"}
    assert {post.group_id for post in posts} == {group_id}
    assert [post.sort_order for post in posts] == list(range(6))
    assert [post.is_cover for post in posts] == [True, False, False, False, False, False]

    def fake_generate_image(prompt):
        generated_prompts.append(prompt)
        return f"https://cdn.test/generated-{len(generated_prompts)}.jpg"

    monkeypatch.setattr(module, "generate_openai_image", fake_generate_image)

    first_batch = module.generate_pending_carousel_images()
    statuses_after_first = [
        module.db.session.get(module.Post, post.id).status for post in posts
    ]
    second_batch = module.generate_pending_carousel_images()
    refreshed = [
        module.db.session.get(module.Post, post.id)
        for post in posts
    ]

    assert first_batch["selected_count"] == 5
    assert first_batch["succeeded_count"] == 5
    assert statuses_after_first == ["draft", "draft", "draft", "draft", "draft", "generating"]
    assert second_batch["selected_count"] == 1
    assert second_batch["succeeded_count"] == 1
    assert [post.status for post in refreshed] == ["draft"] * 6
    assert [post.sort_order for post in refreshed] == list(range(6))
    assert {post.group_id for post in refreshed} == {group_id}
    assert len(generated_prompts) == 6


def test_tiktok_carousel_worker_failure_isolated_in_workflow(
    client, app, module, monkeypatch
):
    user = create_user(module)
    login(client, user)

    set_tiktok_helper(app, monkeypatch, "build_brand_context", lambda user_id: "Brand")
    set_tiktok_helper(app, monkeypatch, "apply_image_style", lambda prompt, style: prompt)
    set_tiktok_helper(
        app,
        monkeypatch,
        "get_placeholder_image_url",
        lambda: "https://cdn.test/placeholder.jpg",
    )

    client.post(
        "/tiktok/create-carousel-draft",
        data={
            "caption": "Carousel caption",
            "image_prompt": "Carousel visual prompt",
            "image_style": "viral_carousel",
            "carousel_idea": "Slide 1: One\nSlide 2: Two\nSlide 3: Three",
        },
    )
    posts = module.Post.query.order_by(module.Post.sort_order.asc()).all()
    failing_prompt = posts[1].prompt

    def fake_generate_image(prompt):
        if prompt == failing_prompt:
            raise RuntimeError("image provider failure")
        return f"https://cdn.test/{len(prompt)}.jpg"

    monkeypatch.setattr(module, "generate_openai_image", fake_generate_image)

    result = module.generate_pending_carousel_images()
    refreshed = [
        module.db.session.get(module.Post, post.id)
        for post in posts
    ]

    assert result["selected_count"] == 3
    assert result["succeeded_count"] == 2
    assert result["failed_count"] == 1
    assert [post.status for post in refreshed] == [
        "draft",
        "generation_failed",
        "draft",
    ]


def test_tiktok_draft_auth_ownership_studio_schedule_and_publish_compatibility(
    client, app, module, monkeypatch
):
    owner = create_user(module, email="owner@example.com")
    attacker = create_user(module, email="attacker@example.com")
    create_accounts(module, owner, instagram=True, facebook=True)
    owner_client = app.test_client()
    login(owner_client, owner)

    set_tiktok_helper(app, monkeypatch, "apply_image_style", lambda prompt, style: prompt)
    set_tiktok_helper(
        app,
        monkeypatch,
        "generate_openai_image",
        lambda prompt: "https://cdn.test/tiktok-owned.jpg",
    )
    set_studio_helper(app, monkeypatch, "build_brand_context", lambda user_id: "Brand")
    set_studio_helper(app, monkeypatch, "update_brand_coach", lambda post, brand: None)

    owner_client.post(
        "/tiktok/create-draft",
        data={
            "caption": "TikTok owned caption",
            "image_prompt": "Owned image prompt",
            "image_style": "",
        },
    )
    post = module.Post.query.one()
    assert post.user_id == owner.id
    assert post.user_id != attacker.id

    login(client, attacker)
    detail_response = client.get(f"/post/{post.id}")
    studio_response = client.get(f"/post/{post.id}/studio")
    module.db.session.refresh(post)

    assert detail_response.status_code == 302
    assert detail_response.location.endswith("/")
    assert studio_response.status_code == 404
    assert post.user_id == owner.id
    assert post.caption == "TikTok owned caption"

    login(owner_client, owner)
    studio_save = owner_client.post(
        f"/post/{post.id}/studio",
        data={"final_caption": "Studio saved TikTok caption"},
        follow_redirects=True,
    )
    module.db.session.refresh(post)

    assert studio_save.status_code == 200
    assert post.caption == "Studio saved TikTok caption"

    scheduled_local = (utc_now() + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M")
    schedule_response = owner_client.post(
        f"/schedule/{post.id}",
        data={"scheduled_time": scheduled_local},
    )
    module.db.session.refresh(post)

    assert schedule_response.status_code == 302
    assert post.status == "scheduled"
    assert post.scheduled_time is not None

    sent = []

    def fake_post(url, json=None, timeout=10):
        sent.append((url, json, timeout))

        class Response:
            status_code = 200
            text = "ok"

            def raise_for_status(self):
                return None

        return Response()

    monkeypatch.setattr(module.requests, "post", fake_post)

    send_response = owner_client.post(f"/send/{post.id}")
    module.db.session.refresh(post)

    assert send_response.status_code == 302
    assert post.status == "sent_to_make"
    assert len(sent) == 1
    assert sent[0][1]["post_id"] == post.id
