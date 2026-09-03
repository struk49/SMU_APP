from datetime import datetime, timedelta
from pathlib import Path

import app as smu_app
from conftest import MockMakeResponse, create_accounts, create_carousel, create_post, create_user
from smu_core.models import Post
from smu_core.services import scheduler as scheduler_service
from smu_core.services.time_utils import utc_now
from config import Config


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


class RecordingScheduler:
    def __init__(self):
        self.running = False
        self.jobs = {}
        self.start_calls = 0
        self.shutdown_calls = 0

    def add_job(self, func, **kwargs):
        self.jobs[kwargs["id"]] = func

    def start(self):
        self.start_calls += 1
        self.running = True

    def shutdown(self, wait=True):
        self.shutdown_calls += 1
        self.running = False

    def get_jobs(self):
        return [type("Job", (), {"id": job_id})() for job_id in self.jobs]


class RecordingLease:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = 0
        self.releases = 0

    def acquire_or_renew(self):
        self.calls += 1
        return next(self.results)

    def release(self):
        self.releases += 1
        return True


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
    assert calls[0]["publish_post"] is module.publish_post
    assert calls[0]["log_event"] is module.log_event
    assert calls[0]["post_model"] is module.Post
    assert calls[0]["db_session"] is module.db.session
    assert callable(calls[0]["now_provider"])


def test_importing_app_does_not_start_scheduler(module):
    assert module.scheduler is smu_app.scheduler
    assert module.scheduler.running is False
    assert job_ids(module.scheduler) == []


def test_scheduler_owner_starts_once_and_registers_existing_jobs(app, module):
    app.config["SMU_SCHEDULER_ENABLED"] = True
    scheduler = RecordingScheduler()
    lease = RecordingLease([True])

    first = module.start_background_scheduler(scheduler, lease)
    second = module.start_background_scheduler(scheduler, lease)

    assert first is True
    assert second is False
    assert scheduler.start_calls == 1
    assert sorted(scheduler.jobs) == [
        "check_scheduled_posts",
        "generate_pending_images",
        "scheduler_lease_heartbeat",
    ]


def test_scheduler_disabled_process_does_not_register_or_start_jobs(app, module):
    app.config["SMU_SCHEDULER_ENABLED"] = False
    scheduler = RecordingScheduler()

    assert module.start_background_scheduler(scheduler) is False
    assert scheduler.start_calls == 0
    assert scheduler.jobs == {}


def test_scheduler_defaults_on_for_local_development():
    assert Config.SMU_SCHEDULER_ENABLED is True


def test_jobs_execute_only_while_lease_is_owned(module):
    calls = []
    lease = RecordingLease([True, False])

    assert module._run_scheduler_job_if_owner(lambda: calls.append("first"), lease) is None
    assert module._run_scheduler_job_if_owner(lambda: calls.append("second"), lease) is None

    assert calls == ["first"]


def test_local_web_and_scheduler_entry_points_are_separated(module):
    app_source = Path(module.__file__).read_text(encoding="utf-8")
    gunicorn_source = Path(module.__file__).with_name("gunicorn.conf.py").read_text(
        encoding="utf-8"
    )
    worker_source = Path(module.__file__).with_name("scheduler_worker.py").read_text(
        encoding="utf-8"
    )

    main_block = app_source.split('if __name__ == "__main__":', 1)[1]
    assert "start_background_scheduler()" in main_block
    assert "use_reloader=False" in main_block
    assert "when_ready" not in gunicorn_source
    assert "start_background_scheduler" not in gunicorn_source
    assert "start_background_scheduler()" in worker_source


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
        scheduled_time=utc_now() - timedelta(minutes=1),
    )
    calls = []

    def fake_publish(published_post, user_id):
        calls.append((published_post.id, user_id))
        published_post.status = "sent_to_make"

    monkeypatch.setattr(module, "publish_post", fake_publish)

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
        scheduled_time=utc_now() - timedelta(minutes=1),
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
