import logging
from datetime import timedelta

from conftest import create_post, create_user
from smu_core.services import carousel_generation
from smu_core.services.time_utils import utc_now


def make_pending(module, user, *, sort_order=0, group_id="group", minutes_ago=0):
    post = create_post(
        module,
        user,
        status="generating",
        group_id=group_id,
        sort_order=sort_order,
        file_url=f"https://cdn.test/placeholder-{group_id}-{sort_order}.jpg",
    )
    post.prompt = f"Prompt {group_id} {sort_order}"
    post.caption = f"Caption {group_id} {sort_order}"
    post.created_at = utc_now() - timedelta(minutes=minutes_ago)
    module.db.session.commit()
    return post


def run_worker(module, image_generator, *, batch_size=None):
    kwargs = {
        "post_model": module.Post,
        "db_session": module.db.session,
        "image_generator": image_generator,
    }

    if batch_size is not None:
        kwargs["batch_size"] = batch_size

    return carousel_generation.generate_pending_carousel_images(**kwargs)


def test_zero_pending_rows_do_nothing(app, module):
    calls = []

    result = run_worker(module, lambda prompt: calls.append(prompt))

    assert result == {
        "selected_count": 0,
        "processed_count": 0,
        "succeeded_count": 0,
        "failed_count": 0,
    }
    assert calls == []


def test_one_pending_row_processes_once_and_becomes_draft(app, module):
    user = create_user(module)
    post = make_pending(module, user)
    calls = []

    result = run_worker(
        module,
        lambda prompt: calls.append(prompt) or "https://cdn.test/generated.jpg",
    )
    module.db.session.refresh(post)

    assert result["selected_count"] == 1
    assert result["succeeded_count"] == 1
    assert calls == ["Prompt group 0"]
    assert post.file_url == "https://cdn.test/generated.jpg"
    assert post.status == "draft"
    assert post.file_type == "image"
    assert post.group_id == "group"
    assert post.sort_order == 0
    assert post.caption == "Caption group 0"

    second = run_worker(module, lambda prompt: calls.append(prompt))

    assert second["selected_count"] == 0
    assert calls == ["Prompt group 0"]


def test_batch_processes_five_rows_and_second_run_continues(app, module):
    user = create_user(module)
    posts = [
        make_pending(module, user, group_id="batch", sort_order=index)
        for index in range(7)
    ]
    calls = []

    first = run_worker(
        module,
        lambda prompt: calls.append(prompt) or f"https://cdn.test/{len(calls)}.jpg",
    )
    statuses_after_first = [
        module.db.session.get(module.Post, post.id).status for post in posts
    ]

    second = run_worker(
        module,
        lambda prompt: calls.append(prompt) or f"https://cdn.test/{len(calls)}.jpg",
    )
    statuses_after_second = [
        module.db.session.get(module.Post, post.id).status for post in posts
    ]

    assert first == {
        "selected_count": 5,
        "processed_count": 5,
        "succeeded_count": 5,
        "failed_count": 0,
    }
    assert statuses_after_first == ["draft", "draft", "draft", "draft", "draft", "generating", "generating"]
    assert second == {
        "selected_count": 2,
        "processed_count": 2,
        "succeeded_count": 2,
        "failed_count": 0,
    }
    assert statuses_after_second == ["draft"] * 7
    assert calls == [f"Prompt batch {index}" for index in range(7)]


def test_ordering_uses_created_group_sort_and_id(app, module):
    user = create_user(module)
    newer = make_pending(module, user, group_id="b", sort_order=0, minutes_ago=1)
    older_second = make_pending(module, user, group_id="a", sort_order=1, minutes_ago=10)
    older_first = make_pending(module, user, group_id="a", sort_order=0, minutes_ago=10)
    older_time = utc_now() - timedelta(minutes=10)
    older_first.created_at = older_time
    older_second.created_at = older_time
    module.db.session.commit()
    calls = []

    run_worker(
        module,
        lambda prompt: calls.append(prompt) or "https://cdn.test/generated.jpg",
        batch_size=3,
    )

    assert calls == [
        older_first.prompt,
        older_second.prompt,
        newer.prompt,
    ]


def test_failure_isolated_and_later_rows_continue(app, module, monkeypatch):
    user = create_user(module)
    first = make_pending(module, user, group_id="group", sort_order=0)
    second = make_pending(module, user, group_id="group", sort_order=1)
    third = make_pending(module, user, group_id="group", sort_order=2)
    calls = []
    rollback_calls = []
    original_rollback = module.db.session.rollback

    def image_generator(prompt):
        calls.append(prompt)
        if prompt == second.prompt:
            raise RuntimeError("raw image failure detail")
        return f"https://cdn.test/{prompt.replace(' ', '-')}.jpg"

    def rollback_spy():
        rollback_calls.append("rollback")
        return original_rollback()

    monkeypatch.setattr(module.db.session, "rollback", rollback_spy)
    result = run_worker(module, image_generator, batch_size=3)

    refreshed = [
        module.db.session.get(module.Post, post.id)
        for post in [first, second, third]
    ]

    assert result == {
        "selected_count": 3,
        "processed_count": 3,
        "succeeded_count": 2,
        "failed_count": 1,
    }
    assert rollback_calls == ["rollback"]
    assert calls == [first.prompt, second.prompt, third.prompt]
    assert [post.status for post in refreshed] == [
        "draft",
        "generation_failed",
        "draft",
    ]
    assert refreshed[0].file_url.endswith("Prompt-group-0.jpg")
    assert refreshed[1].file_url.startswith("https://cdn.test/placeholder")
    assert refreshed[2].file_url.endswith("Prompt-group-2.jpg")

    remaining_user = create_user(module, email="after@example.com")
    remaining = make_pending(module, remaining_user, group_id="after", sort_order=0)
    assert module.db.session.get(module.Post, remaining.id).status == "generating"


def test_row_failure_log_includes_diagnostics_without_row_secrets(
    app, module, caplog
):
    user = create_user(module)
    post = make_pending(module, user, group_id="diagnostic-logs", sort_order=0)
    post.prompt = "prompt containing OPENAI_API_KEY=do-not-log"
    post.caption = "caption containing access_token=do-not-log"
    module.db.session.commit()
    caplog.set_level(logging.ERROR, logger="smu_core.services.carousel_generation")

    def failing_generator(prompt):
        raise RuntimeError("safe diagnostic failure")

    result = run_worker(module, failing_generator)

    assert result["failed_count"] == 1
    records = [
        record
        for record in caplog.records
        if record.message.startswith("carousel_generation_row_failed")
    ]
    assert len(records) == 1
    record = records[0]
    assert f"post_id={post.id}" in record.message
    assert "error_type=RuntimeError" in record.message
    assert "error=safe diagnostic failure" in record.message
    assert record.exc_info[0] is RuntimeError
    assert "Traceback (most recent call last)" in caplog.text
    assert "OPENAI_API_KEY=do-not-log" not in caplog.text
    assert "access_token=do-not-log" not in caplog.text


def test_tiktok_and_content_pack_carousel_rows_share_worker(app, module):
    user = create_user(module)
    tiktok = make_pending(module, user, group_id="tiktok-group", sort_order=0)
    content_pack = make_pending(module, user, group_id="content-pack-group", sort_order=0)

    result = run_worker(
        module,
        lambda prompt: f"https://cdn.test/{prompt.replace(' ', '-')}.jpg",
        batch_size=2,
    )

    assert result["succeeded_count"] == 2
    assert module.db.session.get(module.Post, tiktok.id).status == "draft"
    assert module.db.session.get(module.Post, content_pack.id).status == "draft"


def test_worker_logs_safe_context_without_prompt_caption_or_api_key(
    app, module, caplog
):
    user = create_user(module)
    post = make_pending(module, user, group_id="safe-logs", sort_order=0)
    post.prompt = "Prompt with OPENAI_API_KEY secret"
    post.caption = "Caption that should not be logged"
    module.db.session.commit()
    caplog.set_level(logging.INFO, logger="smu_core.services.carousel_generation")

    result = run_worker(
        module,
        lambda prompt: "https://cdn.test/generated.jpg",
    )

    assert result["succeeded_count"] == 1
    assert "Prompt with OPENAI_API_KEY secret" not in caplog.text
    assert "Caption that should not be logged" not in caplog.text
    contexts = [
        getattr(record, "smu_context", {})
        for record in caplog.records
        if record.message == "carousel_generation_row_succeeded"
    ]
    assert contexts
    assert contexts[0]["post_id"] == post.id
    assert contexts[0]["group_id"] == "safe-logs"
