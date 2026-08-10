# Test Plan

## Purpose

The SMU regression suite exists to protect behavior while the application keeps
evolving. It is especially important because the refactor preserved public
interfaces while moving routes, models and business logic into clearer layers.

The suite protects:

- user-visible behavior
- endpoint names and URL compatibility
- publishing payload contracts
- scheduler and publishing reliability
- ownership and authentication boundaries
- compatibility wrappers and helper bridges

Behavioral compatibility is more important than implementation details. Tests
should make refactoring safer without freezing internal structure unnecessarily.

## Testing Philosophy

Testing principles used during the refactor:

- make small incremental changes
- move one model, route domain or service at a time
- add focused regression tests before moving on
- preserve public interfaces, route URLs and endpoint names
- preserve Make.com payload contracts
- mock external services
- avoid real network calls in unit and regression tests
- run focused tests first, then the full suite at checkpoints

## Test Structure

The suite is organized around application architecture rather than only file
names.

Blueprint tests protect route registration, endpoint compatibility, login
requirements, ownership checks, redirects, templates, context and response
behavior.

Service tests protect reusable business logic independently from routes wherever
possible. They cover publishing, scheduler, captions, content, media, images and
time utilities.

Model tests protect table names, columns, relationships, defaults, compatibility
exports and behavior that depends on SQLAlchemy metadata.

Publishing tests protect manual publishing, scheduled publishing, payload shape,
webhook selection, platform filtering, carousel behavior, status updates and
failure handling.

Scheduler tests protect due-post selection, carousel de-duplication, per-post
failure isolation, commit/rollback behavior and app-compatible job wiring.

Media tests protect file type detection, JPEG normalization, transparency
handling, Cloudinary wrapper contracts and Instagram-safe URLs.

AI helper tests protect caption rewriting, grading, Brand Coach updates, image
generation wrappers, TikTok transcript helpers and Content Pack helpers while
mocking external providers.

Calendar and dashboard tests protect user-owned data visibility, filters,
summaries, event payloads, onboarding state and navigation behavior.

Authentication tests protect registration, login, logout, protected route
redirects and Flask-Login user loading.

## Blueprint Regression Tests

Blueprint regression tests verify that moving routes out of `app.py` does not
change public behavior.

They check:

- routes remain registered once
- endpoint names remain unchanged
- `url_for(...)` compatibility is preserved
- login protection is still applied
- cross-user access is rejected
- redirects and status codes remain stable
- templates and context keys remain available
- route-specific response behavior is preserved

These tests are intentionally behavior-focused. A route can move internally as
long as the user-visible contract remains the same.

## Service Tests

Service tests verify reusable logic without depending on route rendering.

Covered service areas:

- `publishing`: platform filtering, webhook resolution, payloads and Make.com
  delivery behavior
- `scheduler`: due-post processing, carousel de-duplication and transaction
  handling
- `captions`: Brand Brief context, revisions, Brand Coach, caption rewrites and
  grading helpers
- `content`: TikTok transcript extraction, transcript cleanup, fallback behavior
  and Content Pack helpers
- `media`: JPEG normalization, file type detection, Cloudinary wrappers and
  Instagram-safe URL behavior
- `images`: OpenAI image generation wrapper behavior
- `time_utils`: shared naive-UTC helper behavior

Dependencies such as HTTP clients, OpenAI clients and upload functions are
injected or mocked where possible.

## Publishing Tests

Publishing coverage protects:

- selected-platform parsing
- Connected Accounts filtering
- platform order preservation
- user-specific webhook resolution
- global webhook fallback behavior
- single-post payload generation
- carousel payload generation
- manual single publishing
- manual carousel publishing
- scheduled single publishing
- scheduled carousel publishing
- TikTok carousel compatibility
- duplicate-send prevention
- `sent_to_make` and `sent_at` updates
- missing webhook behavior
- no-enabled-platform behavior
- non-2xx Make responses
- database rollback behavior
- cross-user carousel access protection

Tests mock `requests.post`. They must not send real Make.com webhook requests.

## Scheduler Tests

Scheduler tests protect:

- due-post selection
- future, draft and already-sent exclusion
- deterministic due-post ordering
- carousel group de-duplication
- publish invocation with the post owner ID
- commit timing after successful publish
- rollback after failed publish
- continuation after one scheduled post fails
- `schedule_failed` handling
- app-compatible job registration expectations

The scheduler service is tested as orchestration logic. Tests should not start
real long-running scheduler threads.

## Media Tests

Media tests cover:

- supported image and video file type detection
- unsupported file type errors
- RGB JPEG preservation
- PNG/WebP conversion to JPEG
- transparent PNG flattening onto a white background
- non-progressive baseline JPEG output
- Cloudinary upload wrapper return contracts
- Instagram-safe URL transformation
- no accidental real Cloudinary upload

These tests protect Instagram publishing requirements without changing the
Make.com payload contract.

## AI Tests

AI-related tests cover:

- caption rewriting
- caption grading
- Brand Coach updates
- post revision creation and restoration
- OpenAI image generation wrappers
- TikTok transcript extraction helpers
- TikTok subtitle/caption parsing
- Content Pack generation helpers

External AI providers are mocked. Tests should verify prompt/result handling and
wrapper contracts without making real OpenAI, TikTok or yt-dlp network calls.

## Compatibility Layer Tests

Compatibility tests exist because the refactor preserved old call sites while
moving implementations.

They protect:

- `app.py` wrapper functions
- helper bridges stored in `app.extensions`
- late-bound dependency injection
- existing monkeypatch support
- `import app as smu_app` compatibility
- shared `db` and `login_manager` identity
- unqualified endpoint names

These tests ensure internal architecture improvements do not break existing
callers or regression fixtures.

## External Services

Unit and regression tests must not call real external services.

Do not call:

- OpenAI
- Cloudinary
- Make.com
- TikTok
- yt-dlp network extraction
- arbitrary HTTP APIs

Use mocks, fakes or injected callables. When testing HTTP behavior, assert the
request shape and response handling without sending the request externally.

## Manual Smoke Tests

After major refactors or deployment changes, run manual smoke checks in addition
to the automated suite.

Recommended manual checks:

- application starts
- login and logout work
- dashboard loads after login
- Connected Accounts loads and saves
- create a post
- generate an image
- upload media
- schedule a post
- scheduler publishes a due post
- manual publish reaches Make in a safe test scenario
- calendar loads and shows scheduled items
- Studio opens and saves expected changes
- TikTok transcript flow works with a real test URL
- Content Pack flow works

These are not all automatic tests. They are practical end-to-end checks for
runtime wiring and third-party integrations.

## Current Verification Status

Current verified checkpoint:

- regression suite passes
- 545 tests passing
- architecture documentation completed
- publishing contract documented

This count should be updated when a newer verified full-suite result is recorded.

## Adding New Features

Expected workflow for new features:

1. Implement the smallest safe change.
2. Add or update regression tests for the behavior.
3. Run the relevant focused subset.
4. Run the full test suite.
5. Update documentation when behavior, architecture or operations change.

For bug fixes, add a regression test that fails before the fix and passes after
the fix wherever practical.

## Testing Principles

- Test behavior, not private implementation details.
- Keep tests deterministic.
- Mock external dependencies.
- Preserve endpoint and payload compatibility.
- Keep refactors incremental.
- Prefer focused tests near the changed behavior.
- Every meaningful bug fix should include regression coverage.
- Do not weaken assertions simply to make a refactor easier.

## Related Documentation

- `ARCHITECTURE.md`
- `PUBLISHING_CONTRACT.md`
- `ROADMAP.md`
- `ENVIRONMENT.md`
