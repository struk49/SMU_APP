# SMU Codex Instructions

## Mission
Stabilise and refactor SMU without removing working functionality.

## Working rules
1. Inspect before editing.
2. Prefer small, reversible changes.
3. Do not rewrite the whole application in one pass.
4. Preserve existing routes, templates, database data and environment-variable names unless a migration plan is approved.
5. Never expose secrets, webhook URLs, API keys or database credentials.
6. Do not print full webhook URLs in logs.
7. Keep Bootstrap 5 styling unless specifically asked to redesign.
8. Add `db.session.rollback()` after failed database operations.
9. Background jobs must not depend on Flask `current_user`.
10. Scheduled publishing must resolve the post owner's `user_id`.
11. Store scheduled times in UTC; display them in the user's configured IANA timezone.
12. Add or update tests before large refactors.
13. Run the documented checks before declaring a task complete.
14. Summarise changed files, commands run, test results and remaining risks.

## Current priority
1. Restore reliable single-post manual publishing.
2. Restore reliable scheduled publishing.
3. Confirm carousel publishing remains working.
4. Remove duplicate functions that silently override corrected definitions.
5. Extract one reusable publishing service.
6. Continue modularisation only after the publishing tests pass.

## Architecture direction
- `app.py`: application setup and blueprint registration.
- `models.py`: SQLAlchemy models.
- `routes/`: Flask blueprints.
- `services/`: publishing, scheduling, AI, grading, media and brand services.
- `helpers/`: timezone, parsing and formatting helpers.
- `templates/components/`: reusable Jinja components.
- `tests/`: unit and integration tests.

## Definition of done
A change is complete only when:
- Flask starts locally.
- Existing database loads.
- Login works.
- Manual single-post publishing reaches the configured single webhook.
- Scheduled single-post publishing reaches the correct user's webhook.
- Carousel generation and publishing still work.
- Failed jobs produce clear logs and do not leave SQLAlchemy in a failed transaction.
- No secrets appear in output.
