# SMU Flask Architecture Refactor Plan

## Phase 0 Status

This document is the Phase 0 audit and plan. No code has been refactored yet.

Baseline testing is currently blocked in this environment because `pytest` is not installed and the attempted dependency installation is blocked by Windows socket policy:

```text
No module named pytest
WinError 10013: An attempt was made to access a socket in a way forbidden by its access permissions
```

Do not begin Phase 1 until the test baseline is runnable and reviewed.

## 1. Current app.py Responsibilities

`app.py` is currently the application entry point and contains nearly all runtime responsibilities:

- Flask app construction and configuration.
- SQLAlchemy and Flask-Login extension setup.
- Cloudinary and OpenAI client configuration.
- Structured logging setup.
- SQLAlchemy model definitions.
- Ad-hoc database table creation and post-table column patching.
- Template filters.
- Timezone conversion helpers.
- File type and image normalisation helpers.
- Cloudinary upload helpers.
- AI image and caption generation helpers.
- Brand Coach and caption grading helpers.
- Make.com payload construction and webhook delivery.
- User-specific connected-account and webhook resolution.
- Publishing orchestration for manual and scheduled posts.
- Scheduler jobs and APScheduler startup.
- TikTok transcript extraction and repurposing.
- Content Pack generation and draft creation.
- Dashboard, public, auth, post, studio, calendar, content, settings, admin and feedback routes.
- Error handlers.

This creates a high circular-import risk for any direct split. The first implementation phase should extract only configuration and extension instances, while keeping `app.py` as the compatibility entry point.

## 2. Route Inventory Grouped By Domain

### Public And Beta

| URL | Endpoint | Notes |
| --- | --- | --- |
| `/landing` | `landing_page` | Public landing page |
| `/beta/apply` | `beta_apply` | Public beta application, GET/POST |
| `/privacy` | `privacy_policy` | Public legal page |
| `/terms` | `terms_of_service` | Public legal page |
| `/contact` | `contact` | Public contact form, GET/POST |
| `/maintenance` | `maintenance` | Public maintenance page, 503 |

### Dashboard

| URL | Endpoint | Notes |
| --- | --- | --- |
| `/` | `index` | Authenticated dashboard, search, filters, stats and onboarding |

### Posts And Publishing

| URL | Endpoint | Notes |
| --- | --- | --- |
| `/create` | `create_post` | Single and carousel creation |
| `/post/<int:post_id>` | `view_post` | Existing post detail route |
| `/edit-post/<int:post_id>` | `edit_post` | Edit single post |
| `/edit-carousel/<group_id>` | `edit_carousel` | Edit carousel group |
| `/schedule/<int:post_id>` | `schedule_post` | Schedule single or carousel representative |
| `/send/<int:post_id>` | `send_to_make` | Manual single-post publishing |
| `/send-carousel/<group_id>` | `send_carousel_to_make` | Manual carousel publishing |
| `/delete/<int:post_id>` | `delete_post` | Delete post |
| `/delete-carousel/<group_id>` | `delete_carousel` | Delete carousel group |
| `/duplicate-post/<int:post_id>` | `duplicate_post` | Duplicate single post as draft |
| `/duplicate-carousel/<group_id>` | `duplicate_carousel` | Duplicate carousel group as draft |

### AI Studio And Caption Tools

| URL | Endpoint | Notes |
| --- | --- | --- |
| `/rewrite-caption/<int:post_id>` | `rewrite_caption` | Rewrite single caption |
| `/rewrite-carousel-caption/<group_id>` | `rewrite_carousel_caption` | Rewrite carousel caption |
| `/post/<int:post_id>/studio` | `post_studio` | AI Content Studio |
| `/post/<int:post_id>/studio/action/<action>` | `studio_action` | Studio quick actions |
| `/post/<int:post_id>/studio/regrade` | `studio_regrade` | Regrade post |
| `/post/<int:post_id>/ai-editor` | `ai_editor` | AI editor |
| `/post/<int:post_id>/improve` | `improve_post` | Improve caption |
| `/post/<int:post_id>/use-improved` | `use_improved_caption` | Accept improved caption |
| `/post/<int:post_id>/custom-caption` | `use_custom_caption` | Save custom caption |
| `/post/<int:post_id>/discard-improved` | `discard_improved_caption` | Discard improved caption |
| `/post/<int:post_id>/revision/<int:revision_id>/restore` | `restore_revision` | Restore revision |

### TikTok And Content Packs

| URL | Endpoint | Notes |
| --- | --- | --- |
| `/tiktok` | `tiktok_repurpose` | TikTok repurposing page |
| `/tiktok/create-draft` | `create_tiktok_draft` | Create draft from TikTok output |
| `/tiktok/create-carousel-draft` | `create_tiktok_carousel_draft` | Create TikTok carousel draft |
| `/content-pack` | `content_pack` | Content Pack generation |
| `/content-pack/create-carousel` | `create_content_pack_carousel` | Create carousel from pack |
| `/content-pack/create-platform-draft` | `create_content_pack_platform_draft` | Create platform draft from pack |

### Brand, Calendar And Accounts

| URL | Endpoint | Notes |
| --- | --- | --- |
| `/brand-brief` | `brand_brief` | Brand Brief create/update |
| `/calendar` | `calendar_view` | Calendar UI |
| `/calendar/events` | `calendar_events` | Calendar events JSON |
| `/calendar/summary` | `calendar_summary` | Calendar monthly summary JSON |
| `/calendar/events/<int:post_id>/reschedule` | `calendar_reschedule_event` | Drag/drop reschedule JSON |
| `/calendar/events/<int:post_id>/duplicate` | `calendar_duplicate_event` | Duplicate calendar event JSON |
| `/settings/accounts` | `connected_accounts` | Connected platform and webhook settings |

### Authentication, Admin, Help And Feedback

| URL | Endpoint | Notes |
| --- | --- | --- |
| `/register` | `register` | Registration |
| `/login` | `login` | Login |
| `/logout` | `logout` | Logout |
| `/help` | `help_centre` | Help Centre |
| `/admin/beta` | `admin_beta` | Admin-only beta applications and feedback |
| `/feedback` | `submit_feedback` | Authenticated feedback submission |

## 3. Model Inventory

| Model | Table | Key responsibilities |
| --- | --- | --- |
| `User` | `user` | Login identity, email, password hash, relationships |
| `Post` | `post` | Media URL/type, caption, status, schedule, grouping, platforms, user ownership, grading and studio fields |
| `PostRevision` | `post_revision` | Caption revision history |
| `BrandBrief` | `brand_brief` | User-specific brand context |
| `ConnectedAccount` | `connected_account` | User platform toggles and Make webhook URLs |
| `Feedback` | `feedback` | Authenticated user feedback |
| `BetaApplication` | `beta_application` | Public beta applications |
| `ContactMessage` | `contact_message` | Public contact form messages |

Schema preservation is critical. Moving models must retain class names, table names, columns, defaults, relationships and imported compatibility from `app.py`.

## 4. Extension And Global-State Inventory

### Extensions And Clients

- `db = SQLAlchemy(app)`
- `login_manager = LoginManager()`
- `BackgroundScheduler(timezone="UTC")`
- `openai_client = OpenAI(api_key=OPENAI_API_KEY)`
- `cloudinary.config(...)`
- `smu_logger` from `configure_logging()`

### Important Globals

- `BASE_DIR`
- `DATABASE_URL`
- `MAKE_WEBHOOK_SINGLE`
- `MAKE_WEBHOOK_CAROUSEL`
- `OPENAI_API_KEY`
- `UK_TIMEZONE`
- `UTC_TIMEZONE`
- `CALENDAR_STATUS_COLORS`

### Current Startup Side Effects

- The Flask app is created at import time.
- The database is created and patched at import time.
- Cloudinary and OpenAI clients are configured at import time.
- The scheduler starts at import time.
- Tests currently shut down the scheduler after importing `app.py`.

These side effects are the main reason Phase 1 should be conservative.

## 5. Scheduler Lifecycle

Current scheduler startup is at the bottom of `app.py`:

- Prints startup diagnostics.
- Creates `BackgroundScheduler(timezone="UTC")`.
- Registers `generate_pending_carousel_images` every 20 seconds.
- Registers `check_scheduled_posts` every 30 seconds.
- Starts the scheduler immediately.
- Prints registered jobs.

Scheduler processing is user-scoped through `post.user_id` and `publish_post_to_make(post, post.user_id)`. Carousel groups are de-duplicated with `processed_groups`.

Risks:

- Importing `app.py` starts the scheduler unless tests immediately shut it down.
- A future app factory could accidentally start multiple schedulers in debug reloads or worker imports.
- Scheduler jobs depend on app context and database state.
- Background jobs must never rely on `current_user`.

The first scheduler change should be a named startup function such as `start_scheduler(app)`, but not until the baseline test suite is runnable.

## 6. Publishing Flow

### Manual Single

`POST /send/<post_id>` -> ownership-filtered `Post` lookup -> duplicate-send guard -> `publish_post_to_make(post, current_user.id)` -> `build_single_payload(post)` -> enabled platform filtering -> user webhook resolution -> `send_payload_to_make()` -> status update -> commit.

### Manual Carousel

`POST /send-carousel/<group_id>` -> ownership-filtered `get_ordered_carousel_posts()` -> duplicate-send guard -> `publish_post_to_make(posts[0], current_user.id)` -> `build_carousel_payload(group_id, user_id)` -> enabled platform filtering -> user webhook resolution -> `send_payload_to_make()` -> mark every group item sent -> commit.

### Scheduled

`check_scheduled_posts()` -> due query for `Post.status == "scheduled"` and `scheduled_time <= now_utc` -> process each single post or first unprocessed carousel group -> `publish_post_to_make(post, post.user_id)` -> commit or mark failed.

Do not alter payload keys, webhook selection, platform filtering, status transitions, duplicate-send guards or carousel de-duplication during structural moves.

## 7. External Integrations

- Make.com webhooks through `requests.post()`.
- Cloudinary upload and transformation through `cloudinary.uploader`.
- OpenAI image/caption/grading calls through `OpenAI`.
- TikTok transcript extraction through `yt_dlp`.
- APScheduler background jobs.
- SQLite or PostgreSQL through SQLAlchemy.

Secrets and webhook URLs must stay out of logs and reports.

## 8. Environment Variables Referenced

- `SECRET_KEY`
- `DATABASE_URL`
- `SMU_ADMIN_EMAILS`
- `MAKE_WEBHOOK_SINGLE`
- `MAKE_WEBHOOK_CAROUSEL`
- `OPENAI_API_KEY`
- `CLOUDINARY_CLOUD_NAME`
- `CLOUDINARY_API_KEY`
- `CLOUDINARY_API_SECRET`

Do not rename these during the refactor.

## 9. Circular-Import Risks

Likely risk points:

- Models currently depend on the `db` object created in `app.py`.
- Login manager user loading imports `User`.
- Services will need models, database sessions, configuration and external clients.
- Routes currently call helpers defined in the same module.
- Scheduler jobs need app context, models and publishing helpers.
- Tests import `app as smu_app` and access models/functions directly from the module.

Mitigation:

- Extract extension instances first to `app/extensions.py`.
- Keep compatibility re-exports from `app.py` during transition.
- Use `current_app.config` inside Flask contexts rather than importing an app instance.
- Keep high-risk services dependency-light and avoid importing route modules from services.
- Move low-risk routes before high-risk publishing, scheduler and AI functions.

## 9a. app.py And app Package Naming Risk

The repository currently has `app.py` and no `app/` package. A scratch import-resolution test showed that if both `app.py` and an `app/` package with `__init__.py` exist in the same import path, Python resolves `import app` to the package directory, not the existing `app.py` module.

That is ambiguous and unsafe for this project because the existing tests use:

```python
import app as smu_app
```

The transitional target package name should therefore be `smu_core/`, not `app/`, while `app.py` remains the compatibility entry point. During the transition:

- `import app as smu_app` must continue to import `app.py`.
- `app.py` may import implementation pieces from `smu_core`.
- New factory and extension modules should live under `smu_core/`.
- Tests should not be forced to change their existing `import app as smu_app` pattern during early phases.
- A future rename from `smu_core/` to another package name should happen only after `app.py` compatibility and deployment startup commands are explicitly updated.

## 10. Proposed Migration Sequence

1. Establish a runnable test baseline and fix the environment blocker.
2. Phase 1: create `config.py` and `smu_core/extensions.py`; keep `app.py` working.
3. Phase 2: introduce `smu_core.create_app(config_object=None)` behind compatibility startup.
4. Phase 3: move models into `smu_core/models/` with re-exports and no schema changes.
5. Phase 4: move low-risk public, help, feedback, beta, admin and auth routes to blueprints, preserving endpoint names.
6. Phase 5: move dashboard, Brand Brief, Content Pack and Connected Accounts routes.
7. Phase 6: move calendar routes and helpers after calendar tests are green.
8. Phase 7: move post and studio routes after ownership and studio tests are green.
9. Phase 8: extract services one at a time, starting with low-risk calendar/content helpers and ending with publishing/scheduling.
10. Phase 9: reduce `app.py` to a compatibility entry point.
11. Phase 10: full automated and manual smoke verification.

Each phase should be reviewed and reversible.

## 11. Existing Test Coverage

Current test files:

- `tests/test_publishing.py`
- `tests/test_calendar.py`
- `tests/test_beta_prep.py`
- `tests/test_release_candidate.py`
- `tests/test_studio_ui.py`
- `tests/conftest.py`

Covered areas include:

- Manual single publishing.
- Scheduled single publishing.
- Manual carousel publishing.
- Scheduled carousel publishing.
- Missing webhook handling.
- No enabled platforms handling.
- Make non-2xx handling.
- Cross-user carousel access.
- Duplicate-send blocking.
- Scheduler due-post behaviour.
- Instagram image payload expectations.
- JPEG normalisation.
- Calendar route protection, ownership, events, summaries, filtering, colors, tooltips, rescheduling and duplication.
- Onboarding checklist.
- Empty states.
- Help page.
- Feedback endpoint.
- Public landing/legal/contact/beta application/admin RC1 routes.
- AI Studio layout.

Baseline status: not runnable in this environment due missing pytest and blocked dependency install.

## 12. Missing Regression Tests

Add or confirm before moving related code:

- Application factory creation without scheduler startup in tests.
- Extension initialisation against a test config.
- Endpoint-name preservation after blueprint registration.
- Login/logout through form submission with real password hashes.
- Register flow still creates and logs in users.
- Connected-account settings persistence.
- Brand Brief create/update persistence.
- Content Pack generation with OpenAI mocked.
- TikTok transcript and draft creation with external calls mocked.
- Error handlers for 404 and 500.
- Scheduler startup single-instance guard.
- App import smoke test in testing mode.
- Route inventory regression that confirms all critical endpoints still exist.

## 13. Highest-Risk Areas

- `normalize_image_to_jpeg()`
- `upload_jpeg_to_cloudinary()`
- `upload_to_cloudinary()`
- `generate_openai_image()`
- `generate_pending_carousel_images()`
- `send_payload_to_make()`
- `build_single_payload()`
- `build_carousel_payload()`
- `publish_post_to_make()`
- `check_scheduled_posts()`
- `convert_uk_time_to_utc()`
- `convert_utc_to_uk()`
- Calendar filtering, rescheduling and duplication helpers.
- User ownership filters around posts and carousel groups.
- Connected account and webhook resolution.
- The import-time `db.create_all()` and manual `ALTER TABLE` block.
- Scheduler startup at module import.

Before moving any of these, describe what is being moved, why it is safe, which tests cover it and how to roll it back.

## 14. Rollback Strategy

- Keep each phase small and commit separately.
- Do not delete the old implementation until the moved code is tested.
- Use compatibility imports/re-exports while tests and startup paths are updated.
- Keep route URLs and endpoint names unchanged.
- Do not change database schema during structural phases.
- For any failed phase, revert that phase's commit only.
- If scheduler or publishing behaviour becomes unclear, stop before modifying and return to the last known-good commit.

## Proposed First Implementation Phase

Phase 1 should only extract configuration and extension instances:

- Create `config.py`.
- Create `smu_core/extensions.py`.
- Move `db` and `login_manager` construction into `smu_core/extensions.py`.
- Keep `app.py` as the working entry point.
- Avoid moving models, scheduler jobs, publishing helpers or routes in Phase 1.
- Add a small test that imports the extension objects and confirms the existing app still exposes `db` and `login_manager`.

Do not begin Phase 1 until this plan and the blocked baseline are reviewed.
