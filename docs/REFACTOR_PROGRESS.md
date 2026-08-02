# SMU Refactor Progress

## Verified Checkpoint

The current compatibility refactor has completed model extraction while keeping
`app.py` as the runtime entry point.

Moved models:

- `User`
- `Post`
- `PostRevision`
- `BrandBrief`
- `ConnectedAccount`
- `Feedback`
- `ContactMessage`
- `BetaApplication`

Shared extensions:

- `smu_core.extensions.db`
- `smu_core.extensions.login_manager`

Compatibility preserved:

- `import app as smu_app`
- `smu_app.app`
- `smu_app.db`
- `smu_app.login_manager`
- `smu_app.User`
- `smu_app.Post`

## Current Phase

Introduce a minimal `create_app(config_object=None)` factory in `smu_core`.

Factory responsibilities for this phase:

- create the Flask app
- load the same configuration currently used by `app.py`
- accept test config overrides
- initialise the shared `db`
- initialise the shared `login_manager`
- return the app

Responsibilities intentionally left in `app.py`:

- route definitions
- `login_manager.user_loader`
- `db.create_all()`
- manual `post` table patching
- external client setup
- scheduler job registration and startup
- `python app.py` development server behavior

## Order To Preserve

1. Load environment variables.
2. Read configuration values.
3. Create and configure the Flask app through the factory.
4. Initialise shared extensions.
5. Import models before `db.create_all()`.
6. Register `user_loader`.
7. Run `db.create_all()`.
8. Run manual `post` table patching.
9. Define routes and helpers.
10. Register and start scheduler jobs.
11. Run development server when `app.py` is executed directly.

## Current Risks

- Starting the scheduler in the factory would start background jobs during
  factory-only tests.
- Moving `db.create_all()` into the factory would change database patching
  timing.
- Moving routes now would make endpoint changes hard to diagnose.
- Full `git diff --check` may still report unrelated template whitespace from
  existing working-tree changes.

## Rollback

The factory phase can be rolled back by restoring direct app construction and
extension initialisation in `app.py`, removing `create_app()` and its focused
tests, then rerunning the local test suite.

