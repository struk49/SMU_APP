# SMU Architecture

## Current Package Structure

SMU is still a compatibility-first Flask application. The working startup entry
point remains `app.py`, and existing commands such as `python app.py` continue
to target that module.

Current core structure:

- `app.py`: Flask compatibility entry point, route definitions, helper
  functions, database creation and patching, scheduler jobs, scheduler startup,
  external client setup, and development server startup.
- `config.py`: configuration object and database URL normalisation helper.
- `smu_core/extensions.py`: shared Flask extension instances.
- `smu_core/models/`: extracted SQLAlchemy model classes.

## Shared Extensions

The shared extension instances are:

- `db`
- `login_manager`

Both live in `smu_core/extensions.py`. Modules must import these shared objects
instead of creating new extension instances.

## Extracted Models

All SQLAlchemy models now live under `smu_core/models`:

- `User`
- `Post`
- `PostRevision`
- `BrandBrief`
- `ConnectedAccount`
- `Feedback`
- `ContactMessage`
- `BetaApplication`

`app.py` imports and re-exports these names so existing code and tests that use
`import app as smu_app` continue to work.

## app.py Compatibility Role

`app.py` remains responsible for the current runtime behavior:

- exposing `app`, `db`, `login_manager`, and model classes
- registering `login_manager.user_loader`
- defining routes and endpoint names
- running `db.create_all()`
- running the manual `post` table patching block
- defining scheduler jobs
- starting the scheduler
- running the Flask development server when invoked directly

No route modules or blueprints exist yet.

## Proposed Factory Lifecycle

The next factory phase introduces `smu_core.create_app(config_object=None)` with
minimal responsibilities only:

1. Create a `Flask` application.
2. Load the same configuration currently used by `app.py`.
3. Apply optional test configuration overrides.
4. Initialise the shared `db` object.
5. Initialise the shared `login_manager` object and preserve login settings.
6. Return the Flask app.

For this phase, the factory must not:

- register blueprints
- move route code
- run `db.create_all()`
- run manual database patching
- create or start the scheduler
- move external client setup

## Scheduler Lifecycle Risks

The scheduler currently starts in `app.py` after routes and helpers are defined.
Moving scheduler startup into the factory would risk duplicate startup when
tests or scripts call `create_app()`. Therefore scheduler startup remains in
`app.py` for this phase.

The factory must be side-effect-light: creating a test app must not register or
start background jobs.

## Database Creation And Patching Order

The current order must be preserved:

1. Shared `db` exists.
2. Flask app is created and configured.
3. `db.init_app(app)` runs.
4. All model classes are imported before metadata creation.
5. `login_manager.user_loader` is registered.
6. `db.create_all()` runs inside `app.app_context()`.
7. The manual `post` table patching block checks existing columns.
8. Missing legacy columns are added with `ALTER TABLE post ...`.

The factory must not move `db.create_all()` or the patch block in this phase.

## Why Routes Remain In app.py

Routes remain in `app.py` because publishing, scheduling, calendar, AI Studio,
authentication, and database patching are still tightly coupled in one module.
Moving routes before a minimal factory is verified would make failures harder to
diagnose. Blueprint extraction should be a later, separately approved phase.

## Rollback Strategy

If the minimal factory phase fails:

1. Restore direct `app = Flask(__name__)` construction in `app.py`.
2. Restore direct `app.config[...]` assignments in `app.py`.
3. Restore direct `db.init_app(app)` and `login_manager.init_app(app)` calls in
   `app.py`.
4. Remove `create_app()` from `smu_core/__init__.py`.
5. Remove factory-specific tests.
6. Rerun the full local test suite and import smoke checks.
