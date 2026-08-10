# SMU Refactor Progress

This document records the refactor journey and the current verified checkpoint.
It complements `ARCHITECTURE.md`, which is the canonical architecture reference.

## 1. Refactor Goal

The refactor goal was to reduce the old monolithic `app.py` while preserving
working behavior. The work separated routes, models and business logic into
clearer layers, kept publishing and scheduling stable, preserved endpoint
compatibility, and added regression tests around each extraction.

## 2. Starting Point

Historical starting point:

- most routes lived directly in `app.py`
- models were coupled to `app.py`
- publishing and scheduling helpers were mixed with route logic
- AI, media, TikTok and Content Pack helpers were not separated into services
- broad changes carried high regression risk because many workflows depended on
  shared module-level helpers

## 3. Refactor Strategy

The refactor used an incremental compatibility-first approach:

- move one model, route, domain or service at a time
- preserve existing URLs and endpoint names
- add targeted regression tests for each extraction
- run the full pytest suite at verified checkpoints
- keep compatibility wrappers where existing callers or tests required them
- defer broad redesigns until behavior was protected

## 4. Completed Blueprint Phases

Major route domains now live under `smu_core/blueprints/`.

Completed blueprint/domain moves:

- `public`
- `auth`
- `beta`
- `feedback`
- `brand`
- `accounts`
- `content_pack`
- `tiktok`
- `calendar`
- `dashboard`
- `posts`

The Posts blueprint now owns the main post workflow groups:

- detail
- create
- edit
- delete and duplicate
- schedule
- manual publishing
- caption utilities
- AI editor
- Studio and revision restore

## 5. Completed Model Refactor

All application models now live under `smu_core.models`:

- `BetaApplication`
- `BrandBrief`
- `ConnectedAccount`
- `ContactMessage`
- `Feedback`
- `Post`
- `PostRevision`
- `User`

Shared extensions live under `smu_core.extensions`:

- `db`
- `login_manager`

`app.py` still imports and re-exports models and extensions where needed for
compatibility with existing imports such as `import app as smu_app`.

## 6. Completed Service Extractions

Current service modules live under `smu_core/services/`.

- `publishing`: connected-account lookup, platform filtering, webhook selection,
  payload building, Make.com delivery and publish status updates.
- `scheduler`: scheduled-post processing, due-post lookup, carousel
  de-duplication and scheduled publish transaction handling.
- `captions`: Brand Brief context, caption revisions, Brand Coach updates,
  caption rewrites, grading and score extraction.
- `content`: TikTok transcript extraction/cleaning, transcript fallbacks,
  Content Pack generation and section extraction.
- `media`: file type detection, JPEG normalization, Cloudinary upload
  orchestration and Instagram-safe URL handling.
- `images`: OpenAI image generation and multiple-image orchestration.
- `time_utils`: shared naive-UTC helpers.

See `ARCHITECTURE.md` for implementation details and lifecycle diagrams.

## 7. Compatibility Work Preserved

The compatibility layer is intentional, not an unfinished mistake.

Preserved boundaries include:

- `app.py` compatibility wrappers around service-backed helpers
- unqualified endpoint names such as `url_for("calendar_view")`
- `app.extensions` helper bridges used by blueprints
- monkeypatch compatibility for existing tests
- scheduler registration and startup remaining in `app.py`
- external client setup remaining in `app.py`
- database creation and compatibility patching remaining in `app.py`

These boundaries can be reduced later only when tests and callers prove it is
safe.

## 8. Major Regressions Found And Resolved

Important examples from the refactor:

- some database-backed regression tests bypassed the pytest app/database fixture
  and were corrected to use the shared fixture pattern
- TikTok and Content Pack transcript flows exposed helper bridge timing issues
  after route moves
- TikTok subtitle extraction was improved to use caption metadata and better
  candidate selection
- scheduler registration tests were adjusted to account for the fixture
  environment intentionally suppressing live jobs
- a media wrapper test double returned the wrong Cloudinary result shape and was
  corrected to match the helper contract
- a SQLAlchemy callable-default test needed to call the wrapped default with a
  context argument

These regressions are the reason the refactor stayed incremental and test-led.

## 9. Technical Debt Cleaned Up

Completed cleanup includes:

- production `datetime.utcnow()` usage removed
- test and fixture `datetime.utcnow()` usage removed
- shared naive-UTC `utc_now()` and `utc_now_iso_z()` helpers introduced
- production direct `Query.get()` usage replaced with `db.session.get()`
- duplicate legacy TikTok transcript implementation removed

## 10. Current Verification Checkpoint

Current verified status:

- 545 tests passing
- current architecture documented in `docs/ARCHITECTURE.md`
- no production `datetime.utcnow()` matches
- no test or fixture `datetime.utcnow()` matches
- no production direct `.query.get()` matches
- scheduler and manual publishing smoke tests passed where verified
- AI image/create flows were manually smoke-tested where verified

Remaining warnings are primarily third-party or Flask-SQLAlchemy
`Query.get()`-related warnings from library internals/helper behavior rather
than production direct `Query.get()` calls.

## 11. Remaining Engineering Work

Remaining engineering cleanup should stay incremental:

- add typing where it clarifies service contracts
- continue structured logging improvements
- improve authentication/API boundaries if a separate API surface is introduced
- reduce compatibility wrappers only when safe
- perform a final warning audit and library upgrade review where useful

Product roadmap items belong in `ROADMAP.md`.

## 12. Refactor Status

Core architectural refactor: complete.

Compatibility cleanup: largely complete.

Technical debt cleanup: substantially complete.

Future work: incremental hardening and feature development.

## Related Documents

- `ARCHITECTURE.md`
- `ROADMAP.md`
- `PUBLISHING_CONTRACT.md`
- `TEST_PLAN.md`
- `ENVIRONMENT.md`
