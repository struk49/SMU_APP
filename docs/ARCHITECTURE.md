# SMU Architecture

## 1. Overview

SMU is an AI-powered social content and publishing application built on Flask.
The current architecture is layered: routes live in domain blueprints, business
logic is extracted into services, persistence lives in SQLAlchemy models, and
`app.py` remains the compatibility and startup entry point.

The refactor preserved existing URLs, endpoint names, startup commands and test
imports while moving most route and business responsibilities out of the old
monolithic shape.

## 2. High-Level Architecture

Typical request flow:

```text
Browser / Client
-> Blueprint
-> Compatibility helper bridge where required
-> Service
-> Model / Database
-> External integration
-> Response
```

Not every route uses every layer. Public pages may render directly from a
blueprint. Publishing, scheduling, Studio, TikTok, Content Pack, media and image
flows usually pass through helper bridges and services.

## 3. Application Entry Point

`app.py` is still the runtime compatibility and startup entry point. Existing
commands such as `python app.py` and existing imports such as
`import app as smu_app` continue to work.

Current `app.py` responsibilities:

- create the application by calling `smu_core.create_app()`
- expose compatibility symbols such as `app`, `db`, `login_manager` and models
- load external client configuration for OpenAI, Cloudinary and Make.com values
- provide compatibility wrapper functions for service-backed helpers
- register internal `app.extensions` helper bridges used by blueprints
- register the Flask-Login user loader
- run database creation and compatibility patching
- configure template filters
- define app-level error handlers
- register and start APScheduler jobs
- preserve development-server startup when executed directly

`app.py` is no longer the owner of most routes or business logic. Route domains
live in blueprints, and reusable behavior lives in services.

## 4. Application Factory

`smu_core/__init__.py` provides `create_app(config_object=None)`.

The factory:

- loads environment variables
- creates the Flask app with the repository templates and static folders
- loads `config.Config`
- applies optional test or caller configuration overrides
- initializes the shared `db` extension
- initializes the shared `login_manager` extension
- registers the current blueprints
- returns the configured Flask app

The factory does not start the scheduler and does not run database patching.
Those startup side effects remain in `app.py`.

## 5. Blueprint Map

Blueprints live under `smu_core/blueprints/`. They use `add_url_rule` during
registration to preserve the original endpoint names.

| Blueprint | Module | Domain responsibility |
| --- | --- | --- |
| Accounts | `smu_core/blueprints/accounts` | Connected platform and webhook settings |
| Auth | `smu_core/blueprints/auth` | Register, login and logout |
| Beta | `smu_core/blueprints/beta` | Beta application and admin beta view |
| Brand | `smu_core/blueprints/brand` | Brand Brief create/update |
| Calendar | `smu_core/blueprints/calendar` | Calendar UI, events, summary, reschedule and duplicate actions |
| Content Pack | `smu_core/blueprints/content_pack` | Content Pack generation and draft creation |
| Dashboard | `smu_core/blueprints/dashboard` | Authenticated dashboard, onboarding and platform cards |
| Feedback | `smu_core/blueprints/feedback` | Authenticated feedback submission |
| Posts | `smu_core/blueprints/posts` | Post create, detail, edit, delete, duplicate, schedule, manual publish, caption utilities, AI editor and Studio |
| Public | `smu_core/blueprints/public` | Landing, privacy, terms, maintenance, help and contact |
| TikTok | `smu_core/blueprints/tiktok` | TikTok repurposing and TikTok draft creation |

The Posts blueprint is intentionally broad because it preserves many existing
post endpoints while the application remains compatibility-first.

## 6. Service Layer

Services live under `smu_core/services/`. They own reusable business logic and
avoid depending on route modules.

### `publishing.py`

Owns:

- connected-account lookup for publishing
- enabled platform filtering
- user-specific webhook selection
- single-post payload construction
- carousel payload construction
- Make.com webhook delivery
- publishing orchestration and status updates

Does not own:

- route redirects or flash messages
- scheduler job registration
- Cloudinary upload behavior

Major callers:

- manual publishing routes in the Posts blueprint through `app.py` wrappers
- scheduled publishing through the scheduler service and `app.py` wrapper
- publishing service tests

### `scheduler.py`

Owns:

- finding scheduled posts due for publishing
- grouping carousel jobs so a carousel is sent once
- invoking the supplied publishing function
- commit and rollback behavior around scheduled publishing

Does not own:

- APScheduler construction or startup
- manual publishing routes
- Make.com HTTP implementation

Major callers:

- `app.py` `check_scheduled_posts()` wrapper
- scheduler tests

### `captions.py`

Owns:

- Brand Brief context assembly
- caption revision saving
- Brand Coach updates
- caption rewrite helpers
- AI grading helpers
- overall score extraction

Does not own:

- Studio route rendering
- OpenAI client construction
- post ownership checks

Major callers:

- Posts blueprint caption, AI editor and Studio routes through helper bridges
- caption service tests

### `content.py`

Owns:

- TikTok transcript cleaning
- TikTok subtitle/caption extraction through injected dependencies
- transcript fallback selection
- Content Pack generation helpers
- Content Pack section extraction
- placeholder image URL and prompt style helpers

Does not own:

- TikTok route responses
- Content Pack route redirects
- OpenAI client construction
- Whisper fallback

Major callers:

- TikTok blueprint through `smu_tiktok_helpers`
- Content Pack blueprint through `smu_content_pack_helpers`
- content and transcript tests

### `media.py`

Owns:

- file type detection
- image normalization to real JPEG
- transparent image flattening
- Cloudinary upload orchestration through injected upload functions
- Instagram-safe media URL handling
- safe image diagnostics

Does not own:

- OpenAI image generation prompts
- publishing payload delivery
- route form handling

Major callers:

- post creation/edit helpers through `app.py` wrappers
- image generation service through upload wrappers
- media service tests

### `images.py`

Owns:

- OpenAI image generation service behavior
- multiple-image generation orchestration

Does not own:

- Cloudinary upload implementation
- route behavior
- Content Pack/TikTok prompting

Major callers:

- `app.py` compatibility wrappers
- post creation/edit and TikTok helper bridges
- image service tests

### `time_utils.py`

Owns:

- `utc_now()`
- `utc_now_iso_z()`

Does not own:

- UK local-time parsing or display conversion
- per-user timezone policy

Major callers:

- scheduler and publishing logic
- app-level structured timestamps
- model defaults
- tests that need naive UTC values

## 7. Model Layer

Models live under `smu_core/models/` and use the shared `db` extension from
`smu_core.extensions`.

Current models:

- `BetaApplication`
- `BrandBrief`
- `ConnectedAccount`
- `ContactMessage`
- `Feedback`
- `Post`
- `PostRevision`
- `User`

`smu_core/models/__init__.py` exports the model classes for package-level
imports. `app.py` also imports and re-exports the models to preserve legacy
compatibility.

The shared extension instances are:

- `smu_core.extensions.db`
- `smu_core.extensions.login_manager`

No model module should create a separate SQLAlchemy or LoginManager instance.

## 8. Compatibility Layer

`app.py` exposes compatibility wrappers so existing imports, monkeypatches and
startup commands continue to work while implementations move into services.

Examples include wrappers around:

- publishing helpers
- scheduler helpers
- caption and Studio helpers
- TikTok transcript extraction
- Content Pack generation
- media upload and JPEG normalization
- OpenAI image generation
- time utilities

Wrappers are intentionally thin. They delegate to service functions and inject
runtime dependencies such as configured clients, API keys, global fallback
webhooks and upload functions.

The late-bound wrapper strategy means blueprints and tests can resolve the
current callable at execution time rather than importing `app.py` directly.

## 9. Helper Bridges

Blueprints must not import `app.py`. When a blueprint still needs a compatibility
helper that lives behind `app.py`, it resolves the callable through an internal
`app.extensions` bridge.

Current helper bridges and keys:

| Bridge | Keys |
| --- | --- |
| `smu_calendar_helpers` | `parse_platforms`, `convert_utc_to_uk`, `get_ordered_carousel_posts` |
| `smu_post_detail_helpers` | `get_ordered_carousel_posts` |
| `smu_post_edit_helpers` | `get_ordered_carousel_posts`, `generate_openai_image` |
| `smu_post_delete_duplicate_helpers` | `get_ordered_carousel_posts` |
| `smu_post_create_helpers` | `convert_uk_time_to_utc`, `build_brand_context`, `apply_image_style`, `generate_openai_image`, `generate_multiple_openai_images`, `get_file_type`, `upload_to_cloudinary`, `is_instagram_selected` |
| `smu_post_schedule_helpers` | `convert_uk_time_to_utc`, `get_ordered_carousel_posts`, `log_scheduled_post_diagnostics` |
| `smu_manual_publish_helpers` | `publish_post_to_make`, `get_ordered_carousel_posts`, `log_event` |
| `smu_caption_helpers` | `rewrite_caption_with_ai`, `get_ordered_carousel_posts`, `build_brand_context`, `improve_post_with_ai`, `update_brand_coach`, `save_post_revision` |
| `smu_ai_editor_helpers` | `save_post_revision`, `build_brand_context`, `update_brand_coach` |
| `smu_studio_helpers` | `save_post_revision`, `build_brand_context`, `update_brand_coach`, `rewrite_caption_with_action`, `grade_post_with_ai`, `extract_overall_score` |
| `smu_content_pack_helpers` | `extract_tiktok_transcript`, `build_brand_context`, `generate_content_pack`, `extract_content_pack_section`, `apply_image_style`, `get_placeholder_image_url` |
| `smu_tiktok_helpers` | `extract_tiktok_transcript`, `build_brand_context`, `repurpose_tiktok_content`, `apply_image_style`, `generate_openai_image`, `get_placeholder_image_url` |
| `smu_dashboard_helpers` | `build_onboarding_progress`, `build_connected_platform_cards` |

These bridges exist to:

- avoid blueprint imports from `app.py`
- preserve old helper names while internals move to services
- keep existing monkeypatch-based tests working
- support gradual extraction without changing route behavior

They are internal compatibility mechanisms, not public APIs.

## 10. Publishing Lifecycle

Manual publishing starts from Posts blueprint routes. Scheduled publishing starts
from the scheduler wrapper in `app.py`.

Typical publishing flow:

```text
manual or scheduled trigger
-> publishing service
-> connected account lookup for the owning user
-> enabled platform filtering
-> webhook selection
-> single or carousel payload building
-> Make.com webhook request
-> status and sent_at update after success
```

Carousel publishing uses the existing group convention. The scheduler service
deduplicates carousel groups so one carousel group is sent once rather than once
per child post row.

Webhook URLs and payload secrets must not be logged.

## 11. Scheduler Lifecycle

APScheduler is configured and started in `app.py`.

Current scheduled flow:

```text
APScheduler job
-> app-compatible check_scheduled_posts wrapper
-> smu_core.services.scheduler.check_scheduled_posts
-> due scheduled post query
-> carousel group de-duplication
-> publishing service
-> commit or rollback
```

The scheduler service owns due-post processing. `app.py` owns the scheduler
instance, job registration and startup timing.

## 12. TikTok / Content Lifecycle

TikTok and Content Pack features share transcript and content helpers through
bridges.

Typical flow:

```text
TikTok blueprint or Content Pack blueprint
-> helper bridge
-> content service
-> yt-dlp subtitle/caption extraction
-> transcript cleaning and fallback selection
-> content generation where required
-> draft or response
```

The content service supports subtitle/caption extraction and description/title
fallbacks. Whisper fallback is not currently documented as an implemented path.

## 13. Caption / Studio Lifecycle

Caption and Studio routes live in the Posts blueprint.

Typical flow:

```text
Posts blueprint
-> caption, AI editor or Studio helper bridge
-> captions service
-> revision, Brand Coach or AI grading helper
-> database update
-> response or redirect
```

The captions service owns reusable caption, grading, revision and Brand Coach
logic. The blueprint owns route behavior, ownership checks, flashes, redirects
and template rendering.

## 14. Media / Image Lifecycle

Image generation and media handling are separate responsibilities.

`smu_core/services/images.py` owns OpenAI image generation orchestration.

`smu_core/services/media.py` owns:

- file type detection
- JPEG normalization
- alpha flattening for transparent images
- Cloudinary upload orchestration
- Instagram-safe URL handling
- safe diagnostics

`app.py` wrappers inject configured OpenAI and Cloudinary dependencies into
these services.

## 15. Time Handling

`smu_core/services/time_utils.py` provides:

- `utc_now()`
- `utc_now_iso_z()`

Persisted database timestamps remain naive UTC for compatibility with the
existing schema and scheduler comparisons.

`utc_now()` returns a naive UTC `datetime`. `utc_now_iso_z()` is used where a
structured UTC log timestamp string is needed.

UK local-time parsing and display conversion remain separate from the shared
clock helper.

## 16. Testing Architecture

The test suite uses `pytest` and fixture-based database setup.

Current coverage includes:

- blueprint registration and endpoint compatibility
- authenticated and unauthenticated route behavior
- ownership and cross-user protections
- model move compatibility
- service contracts
- publishing and scheduler behavior
- media and image generation helpers
- TikTok transcript extraction
- Content Pack flow
- Caption, AI editor and Studio behavior
- compatibility wrappers and helper bridges

Unit and regression tests mock external HTTP/API interactions. Tests should not
call real Make.com webhooks, Cloudinary, OpenAI or TikTok network paths.

## 17. Architecture Principles

SMU refactors follow these principles:

- keep routes thin
- centralize reusable business logic in services
- preserve endpoint names and URLs
- preserve compatibility imports until removal is proven safe
- keep explicit compatibility boundaries
- keep one clear responsibility per service
- avoid duplicate helper implementations
- prefer small, verified changes with regression tests
- do not move startup side effects casually

## 18. Current Technical Boundaries

The following intentionally remain in `app.py`:

- startup and compatibility entry-point behavior
- external client setup where applicable
- scheduler registration and startup
- database creation and compatibility patching
- compatibility wrappers
- helper bridge registration
- template filters
- error handlers
- development server startup

These boundaries are deliberate while SMU remains compatibility-first.

## 19. Future Architecture Direction

Future architecture work should stay incremental.

Likely directions:

- add gradual typing where it improves service contracts
- continue structured logging improvements
- introduce API authentication if a separate API surface is added
- reduce compatibility wrappers only when tests and callers prove they are safe
  to remove

Product roadmap items belong in `ROADMAP.md`, not this architecture document.
