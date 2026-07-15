# Current Bugs and Risks

## P0 — Publishing reliability
- Manual single-image publishing has recently failed before reaching Make.
- Scheduled publishing has recently failed or not selected due posts.
- Corrected helpers may have been removed or overwritten by duplicate definitions.

## P0 — Duplicate definitions
Audit `app.py` for duplicate definitions, especially:
- `convert_uk_time_to_utc`
- `get_user_connected_accounts`
- `get_enabled_platforms_for_user`
- `get_user_make_webhook`
- `grade_post_with_ai`
- `extract_overall_score`

## P1 — Scheduler
- Ensure `scheduler.start()` is called.
- Ensure `check_scheduled_posts` is registered.
- Ensure only one scheduler instance starts per process.
- Scheduled jobs must use `post.user_id`, never `current_user`.
- Failed scheduled posts should record a useful status and error without poisoning the SQLAlchemy session.

## P1 — Timezone
- Current implementation assumes `Europe/London`.
- Future beta users require a per-user IANA timezone.
- UTC should remain the storage and scheduler comparison standard.

## P1 — Deployment
- Running APScheduler inside a multi-worker web service can start duplicate scheduler instances.
- Before beta, decide whether to use one dedicated Render worker/cron process for scheduled jobs.

## P2 — Legacy code
- Old AI Editor and improved-caption routes/templates may still exist.
- Remove only after reference searches and regression tests.
