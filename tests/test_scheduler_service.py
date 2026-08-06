import ast
from datetime import datetime, timedelta
from pathlib import Path

import app as smu_app
from conftest import MockMakeResponse, create_accounts, create_carousel, create_post, create_user
from smu_core.models import Post
from smu_core.services import scheduler as scheduler_service


class RecordingSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def job_ids(scheduler):
    try:
        return sorted(job.id for job in scheduler.get_jobs())
    except Exception:
        return []


def app_scheduler_add_job_calls(module):
    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "add_job":
            continue
        if not isinstance(node.func.value, ast.Name):
            continue
        if node.func.value.id != "scheduler":
            continue

        job_id = None
        for keyword in node.keywords:
            if keyword.arg == "id" and isinstance(keyword.value, ast.Constant):
                job_id = keyword.value.value

        func_arg = node.args[0] if node.args else None
        func_name = func_arg.id if isinstance(func_arg, ast.Name) else None

        calls.append({"id": job_id, "func": func_name})

    return calls


def test_scheduler_service_exports_and_app_compatibility(module):
    assert callable(scheduler_service.check_scheduled_posts)
    assert callable(module.check_scheduled_posts)
    assert module.check_scheduled_posts.__name__ == "check_scheduled_posts"
    assert module.scheduler is smu_app.scheduler
    assert module.scheduler is not None


def test_app_wrapper_delegates_to_scheduler_service(app, module, monkeypatch):
    calls = []

    def fake_service(**kwargs):
        calls.append(kwargs)
        return "done"

    monkeypatch.setattr(module.scheduler_service, "check_scheduled_posts", fake_service)

    assert module.check_scheduled_posts() == "done"
    assert calls
    assert calls[0]["publish_post"] is module.publish_post_to_make
    assert calls[0]["log_event"] is module.log_event
    assert calls[0]["post_model"] is module.Post
    assert calls[0]["db_session"] is module.db.session
    assert callable(calls[0]["now_provider"])


def test_apscheduler_jobs_remain_registered_in_app(module):
    calls = app_scheduler_add_job_calls(module)
    ids = [call["id"] for call in calls]

    assert "check_scheduled_posts" in ids
    assert "generate_pending_images" in ids
    assert ids.count("check_scheduled_posts") == 1
    assert ids.count("generate_pending_images") == 1
    assert module.scheduler is smu_app.scheduler


def test_scheduler_job_uses_app_compatible_callable(module):
    jobs = {
        call["id"]: call["func"]
        for call in app_scheduler_add_job_calls(module)
    }

    assert jobs["check_scheduled_posts"] == "check_scheduled_posts"
    assert module.check_scheduled_posts.__module__ == "app"


def test_due_query_finds_due_single_and_ignores_future_draft_and_sent(app, module):
    user = create_user(module)
    now = datetime(2026, 7, 10, 12, 0)
    due = create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=now - timedelta(minutes=1),
    )
    future = create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=now + timedelta(minutes=1),
    )
    draft = create_post(
        module,
        user,
        status="draft",
        scheduled_time=now - timedelta(minutes=2),
    )
    sent = create_post(
        module,
        user,
        status="sent_to_make",
        scheduled_time=now - timedelta(minutes=3),
    )
    calls = []

    def fake_publish(post, user_id):
        calls.append((post.id, user_id))
        post.status = "sent_to_make"

    scheduler_service.check_scheduled_posts(
        publish_post=fake_publish,
        log_event=lambda *args, **kwargs: None,
        now_provider=lambda: now,
    )

    assert calls == [(due.id, user.id)]
    assert module.db.session.get(Post, due.id).status == "sent_to_make"
    assert module.db.session.get(Post, future.id).status == "scheduled"
    assert module.db.session.get(Post, draft.id).status == "draft"
    assert module.db.session.get(Post, sent.id).status == "sent_to_make"


def test_due_query_ordering_is_preserved(app, module):
    user = create_user(module)
    now = datetime(2026, 7, 10, 12, 0)
    later = create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=now - timedelta(minutes=1),
        sort_order=0,
    )
    earlier = create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=now - timedelta(minutes=5),
        sort_order=1,
    )
    same_time_first = create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=now - timedelta(minutes=3),
        sort_order=0,
    )
    same_time_second = create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=now - timedelta(minutes=3),
        sort_order=1,
    )
    calls = []

    def fake_publish(post, user_id):
        calls.append(post.id)
        post.status = "sent_to_make"

    scheduler_service.check_scheduled_posts(
        publish_post=fake_publish,
        log_event=lambda *args, **kwargs: None,
        now_provider=lambda: now,
    )

    assert calls == [
        earlier.id,
        same_time_first.id,
        same_time_second.id,
        later.id,
    ]


def test_due_single_calls_publish_with_owner_id_and_commits(app, module):
    user = create_user(module)
    now = datetime(2026, 7, 10, 12, 0)
    post = create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=now - timedelta(minutes=1),
    )
    session = RecordingSession()
    events = []
    calls = []

    def fake_publish(published_post, user_id):
        calls.append((published_post, user_id))
        published_post.status = "sent_to_make"

    scheduler_service.check_scheduled_posts(
        publish_post=fake_publish,
        log_event=lambda *args, **kwargs: events.append((args, kwargs)),
        now_provider=lambda: now,
        db_session=session,
    )

    assert calls == [(post, user.id)]
    assert session.commits == 1
    assert session.rollbacks == 0
    assert events == [
        (
            ("publishing_success",),
            {
                "post_id": post.id,
                "post_type": "single",
                "user_id": user.id,
                "source": "scheduler",
            },
        )
    ]


def test_single_failure_rolls_back_marks_failed_and_continues(app, module):
    user = create_user(module)
    now = datetime(2026, 7, 10, 12, 0)
    failing = create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=now - timedelta(minutes=2),
    )
    succeeding = create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=now - timedelta(minutes=1),
    )
    events = []
    calls = []

    def fake_publish(post, user_id):
        calls.append(post.id)
        if post.id == failing.id:
            raise RuntimeError("publish failed")
        post.status = "sent_to_make"

    scheduler_service.check_scheduled_posts(
        publish_post=fake_publish,
        log_event=lambda *args, **kwargs: events.append((args, kwargs)),
        now_provider=lambda: now,
    )

    assert calls == [failing.id, succeeding.id]
    assert module.db.session.get(Post, failing.id).status == "schedule_failed"
    assert module.db.session.get(Post, succeeding.id).status == "sent_to_make"
    assert events[0][0] == ("publishing_failure",)
    assert events[0][1]["post_id"] == failing.id
    assert events[0][1]["error_type"] == "RuntimeError"
    assert events[1][0] == ("publishing_success",)
    assert events[1][1]["post_id"] == succeeding.id


def test_second_run_does_not_republish_successful_post(app, module):
    user = create_user(module)
    now = datetime(2026, 7, 10, 12, 0)
    post = create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=now - timedelta(minutes=1),
    )
    calls = []

    def fake_publish(published_post, user_id):
        calls.append(published_post.id)
        published_post.status = "sent_to_make"

    scheduler_service.check_scheduled_posts(
        publish_post=fake_publish,
        log_event=lambda *args, **kwargs: None,
        now_provider=lambda: now,
    )
    scheduler_service.check_scheduled_posts(
        publish_post=fake_publish,
        log_event=lambda *args, **kwargs: None,
        now_provider=lambda: now,
    )

    assert calls == [post.id]


def test_due_carousel_publishes_once_per_group_with_representative(app, module):
    user = create_user(module)
    now = datetime(2026, 7, 10, 12, 0)
    group_id, posts = create_carousel(module, user, status="scheduled", scheduled=True)
    for post in posts:
        post.scheduled_time = now - timedelta(minutes=1)
    module.db.session.commit()
    calls = []

    def fake_publish(post, user_id):
        calls.append((post.id, post.group_id, user_id))
        for group_post in Post.query.filter_by(group_id=post.group_id, user_id=user_id):
            group_post.status = "sent_to_make"

    scheduler_service.check_scheduled_posts(
        publish_post=fake_publish,
        log_event=lambda *args, **kwargs: None,
        now_provider=lambda: now,
    )

    assert calls == [(posts[0].id, group_id, user.id)]
    assert {post.status for post in posts} == {"sent_to_make"}


def test_separate_carousel_groups_each_publish_once(app, module):
    user = create_user(module)
    now = datetime(2026, 7, 10, 12, 0)
    first_group_id, first_posts = create_carousel(
        module,
        user,
        status="scheduled",
        scheduled=True,
    )
    second_group_id = "second-group"
    second_posts = [
        create_post(
            module,
            user,
            status="scheduled",
            scheduled_time=now - timedelta(minutes=1),
            group_id=second_group_id,
            sort_order=0,
            is_cover=True,
        ),
        create_post(
            module,
            user,
            status="scheduled",
            scheduled_time=now - timedelta(minutes=1),
            group_id=second_group_id,
            sort_order=1,
            is_cover=False,
        ),
    ]
    for post in first_posts + second_posts:
        post.scheduled_time = now - timedelta(minutes=1)
    module.db.session.commit()
    calls = []

    def fake_publish(post, user_id):
        calls.append((post.group_id, user_id))
        for group_post in Post.query.filter_by(group_id=post.group_id, user_id=user_id):
            group_post.status = "sent_to_make"

    scheduler_service.check_scheduled_posts(
        publish_post=fake_publish,
        log_event=lambda *args, **kwargs: None,
        now_provider=lambda: now,
    )

    assert calls == [(first_group_id, user.id), (second_group_id, user.id)]


def test_tiktok_carousel_path_remains_compatible(app, module):
    user = create_user(module)
    now = datetime(2026, 7, 10, 12, 0)
    group_id, posts = create_carousel(module, user, status="scheduled", scheduled=True)
    for post in posts:
        post.prompt = "TikTok carousel slide"
        post.platforms = "instagram,facebook"
        post.scheduled_time = now - timedelta(minutes=1)
    module.db.session.commit()
    calls = []

    def fake_publish(post, user_id):
        calls.append((post.group_id, post.prompt, post.platforms, user_id))
        for group_post in Post.query.filter_by(group_id=post.group_id, user_id=user_id):
            group_post.status = "sent_to_make"

    scheduler_service.check_scheduled_posts(
        publish_post=fake_publish,
        log_event=lambda *args, **kwargs: None,
        now_provider=lambda: now,
    )

    assert calls == [(group_id, "TikTok carousel slide", "instagram,facebook", user.id)]


def test_diagnostics_report_current_counts_and_empty_state(app, module, capsys):
    now = datetime(2026, 7, 10, 12, 0)

    scheduler_service.check_scheduled_posts(
        publish_post=lambda *args, **kwargs: None,
        log_event=lambda *args, **kwargs: None,
        now_provider=lambda: now,
    )
    output = capsys.readouterr().out

    assert "Scheduler check: 2026-07-10 12:00:00 UTC" in output
    assert "Scheduler diagnostics:" in output
    assert "'current_utc_time': datetime.datetime(2026, 7, 10, 12, 0)" in output
    assert "'scheduled_row_count': 0" in output
    assert "'earliest_scheduled_time': None" in output
    assert "'due_row_count': 0" in output


def test_diagnostics_report_scheduled_count_earliest_and_due_count(app, module, capsys):
    user = create_user(module)
    now = datetime(2026, 7, 10, 12, 0)
    earliest = create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=now - timedelta(minutes=5),
    )
    create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=now + timedelta(minutes=5),
    )

    def fake_publish(post, user_id):
        post.status = "sent_to_make"

    scheduler_service.check_scheduled_posts(
        publish_post=fake_publish,
        log_event=lambda *args, **kwargs: None,
        now_provider=lambda: now,
    )
    output = capsys.readouterr().out

    assert "'scheduled_row_count': 2" in output
    assert f"'earliest_scheduled_time': {repr(earliest.scheduled_time)}" in output
    assert "'due_row_count': 1" in output


def test_app_scheduler_wrapper_preserves_publish_monkeypatch(app, module, monkeypatch):
    user = create_user(module)
    post = create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=datetime.utcnow() - timedelta(minutes=1),
    )
    calls = []

    def fake_publish(published_post, user_id):
        calls.append((published_post.id, user_id))
        published_post.status = "sent_to_make"

    monkeypatch.setattr(module, "publish_post_to_make", fake_publish)

    module.check_scheduled_posts()

    assert calls == [(post.id, user.id)]
    assert module.db.session.get(Post, post.id).status == "sent_to_make"


def test_existing_publishing_service_scheduler_caller_still_works(app, module, monkeypatch):
    user = create_user(module)
    create_accounts(module, user, instagram=True)
    post = create_post(
        module,
        user,
        status="scheduled",
        scheduled_time=datetime.utcnow() - timedelta(minutes=1),
        platforms="instagram",
    )
    sent = []

    monkeypatch.setattr(
        module.requests,
        "post",
        lambda url, json, timeout: sent.append((url, json, timeout)) or MockMakeResponse(),
    )

    module.check_scheduled_posts()
    module.db.session.expire_all()

    assert len(sent) == 1
    assert sent[0][1]["post_id"] == post.id
    assert module.db.session.get(Post, post.id).status == "sent_to_make"
