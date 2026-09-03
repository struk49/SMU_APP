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
- Scheduler startup is explicit: local `python app.py` owns it, while production
  uses the dedicated `scheduler_worker.py` process.
- Importing `app.py`, the Gunicorn master and ordinary Gunicorn workers do not
  start it.
- `check_scheduled_posts` and `generate_pending_images` remain registered by the
  designated owner.
- Scheduled jobs must use `post.user_id`, never `current_user`.
- Failed scheduled posts should record a useful status and error without poisoning the SQLAlchemy session.

## P1 — Timezone
- Current implementation assumes `Europe/London`.
- Future beta users require a per-user IANA timezone.
- UTC should remain the storage and scheduler comparison standard.

## P1 — Deployment
- Render needs a Web Service with `SMU_SCHEDULER_ENABLED=false` and one Background
  Worker with `SMU_SCHEDULER_ENABLED=true`, both using the same PostgreSQL database.
- The database lease prevents overlapping worker instances from executing jobs
  simultaneously during replacement or failover.

## P2 — Legacy code
- Old AI Editor and improved-caption routes/templates may still exist.
- Remove only after reference searches and regression tests.
