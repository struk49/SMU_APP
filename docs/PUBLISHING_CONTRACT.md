# Publishing Contract

## Overview

This document defines the publishing contract between SMU, Make.com and direct
platform adapters. Payload compatibility is treated as a stable interface:
internal implementation can move between modules, but the payload shape sent to
Make must remain predictable.

The current implementation lives mainly in services. `app.py` remains the
compatibility and startup entry point, exposing wrappers used by existing routes,
tests and helper bridges.

## Current Publishing Architecture

Publishing-related responsibilities are split as follows:

- `app.py`: compatibility wrappers, configured Make webhook fallback values,
  helper bridge registration, scheduler startup and app-compatible scheduler
  wrappers.
- `smu_core.services.publishing`: connected-account lookup, enabled-platform
  filtering, webhook resolution, Make.com payload creation, Make.com delivery,
  direct LinkedIn orchestration and publish status updates.
- `smu_core.services.linkedin_publishing`: LinkedIn text/single-image
  validation, media download checks, account checks and adapter invocation for
  personal profiles.
- `smu_core.services.scheduler`: scheduled-post orchestration, due-post lookup,
  carousel de-duplication, scheduled publish invocation and rollback handling.
- `smu_core.services.media`: JPEG normalization, Cloudinary upload orchestration
  and Instagram-safe URL transformation.

Manual publishing routes call publishing through the `smu_manual_publish_helpers`
bridge. Scheduled publishing calls the scheduler service through the
app-compatible `check_scheduled_posts()` wrapper.

## Publishing Flow

Typical publishing lifecycle:

1. User creates or schedules a post.
2. Media is uploaded or generated.
3. Media is normalized and stored in Cloudinary where required.
4. Selected platforms are read from the post.
5. Connected Accounts filter the selected platforms.
6. Make-supported platforms are sent through Make.com.
7. LinkedIn text-only and single-image personal-profile posts are sent through
   LinkedIn's direct API.
8. SMU updates `status` and `sent_at` after successful delivery.
9. Manual or scheduled publishing completes.

## Manual Publishing Flow

Manual publishing flow:

```text
User
-> Post detail page
-> manual publish route in the Posts blueprint
-> smu_manual_publish_helpers
-> publishing service
-> Make webhook
-> database status update
```

Manual routes preserve existing redirects, flash messages and duplicate-send
guards. The publishing service performs the payload and webhook work.

## Scheduled Publishing Flow

Scheduled publishing flow:

```text
APScheduler
-> app.py check_scheduled_posts wrapper
-> smu_core.services.scheduler
-> due scheduled posts
-> representative carousel detection
-> publishing service
-> status update
-> commit
```

The scheduler service is responsible for orchestration only. It finds due posts,
ensures carousel groups are processed once, invokes the supplied publishing
function, commits on success and rolls back on failure.

## Connected Accounts

Publishing includes a platform only when:

1. the user selected the platform on the post, and
2. the user's Connected Accounts record marks that platform as connected.

The selected platform order is preserved after filtering. Supported connected
flags in the current publishing service are:

- `instagram`
- `facebook`
- `linkedin`
- `pinterest`
- `reddit`
- `x`

If no selected platform is connected, publishing fails before contacting Make.

## Webhook Resolution

Webhook resolution is based on post type.

For carousel posts:

```text
user-specific carousel webhook
-> global carousel webhook
-> None
```

For single posts:

```text
user-specific single webhook
-> global single webhook
-> None
```

If no webhook is available for the post type, the publishing service raises an
error and no Make request is sent.

## Payload Contracts

### Single-Post Payload

Current single-post payload:

```json
{
  "post_type": "single",
  "post_id": 123,
  "caption": "Caption text",
  "prompt": "Optional prompt",
  "file_url": "https://public-media-url.example/image.jpg",
  "file_type": "image",
  "platforms": ["facebook", "instagram"]
}
```

Fields:

- `post_type`: always `"single"` for single-post publishing.
- `post_id`: database ID of the post.
- `caption`: current post caption.
- `prompt`: current post prompt.
- `file_url`: public media URL stored on the post.
- `file_type`: stored file type, usually `"image"` or `"video"`.
- `platforms`: selected platforms after Connected Accounts filtering.

Instagram single-image posts require a non-empty `file_url`.

### Carousel Payload

Current carousel payload:

```json
{
  "post_type": "carousel",
  "group_id": "uuid",
  "caption": "Caption text",
  "prompt": "Optional prompt",
  "platforms": ["instagram"],
  "media": [
    {
      "post_id": 1,
      "file_url": "https://public-media-url.example/1.jpg",
      "file_type": "image",
      "sort_order": 0,
      "is_cover": true
    }
  ]
}
```

Top-level fields:

- `post_type`: always `"carousel"` for carousel publishing.
- `group_id`: carousel group identifier.
- `caption`: caption from the first ordered carousel post.
- `prompt`: prompt from the first ordered carousel post.
- `platforms`: selected platforms after Connected Accounts filtering.
- `media`: ordered carousel media items.

Media item fields:

- `post_id`: database ID of the child post.
- `file_url`: media URL transformed through Instagram-safe URL handling.
- `file_type`: stored file type.
- `sort_order`: carousel ordering value.
- `is_cover`: whether the child post is the cover item.

Carousel ordering is cover first, then `sort_order`, then post ID.

## Media Processing

Media handling lives in `smu_core.services.media`.

Current responsibilities:

- detect supported file types
- normalize images to real JPEG when required
- flatten transparent images onto a white background
- upload media to Cloudinary through injected upload functions
- store Instagram-safe uploads as JPEG
- transform Cloudinary URLs with the Instagram-safe image transformation

Supported image extensions:

- `png`
- `jpg`
- `jpeg`
- `gif`
- `webp`

Supported video extensions:

- `mp4`
- `mov`
- `avi`
- `webm`

The media service does not send publishing payloads and does not generate OpenAI
images. OpenAI image generation lives in `smu_core.services.images`.

## Publishing Status

The publishing service updates SMU database state after Make accepts the request.

Current successful Make transitions:

```text
draft or scheduled
-> sent_to_make
```

Current successful LinkedIn-only text or single-image transition:

```text
draft or scheduled
-> published
```

On success:

- single posts are marked `sent_to_make`
- single posts receive `sent_at`
- all posts in a successfully sent carousel group are marked `sent_to_make`
- all posts in a successfully sent carousel group receive `sent_at`
- LinkedIn-only text and single-image posts are marked `published`, not
  `sent_to_make`
- mixed Make + LinkedIn single posts remain `sent_to_make` because Make delivery
  occurred

The application does not currently confirm final platform publication status
from Make in this service. Any later platform-side state such as published
analytics is outside this contract.

LinkedIn text-only and single-image publishing use the direct LinkedIn Posts and
Images APIs for personal profiles. LinkedIn MultiImage, video and organization
publishing remain outside this contract.

Scheduled failures are marked `schedule_failed` by the scheduler service after a
rollback and failed-post isolation attempt.

## Failure Handling

Failure behavior:

- missing enabled platforms raises before contacting Make
- missing webhook raises before contacting Make
- missing Instagram single-image URL raises before contacting Make
- missing carousel payload raises before contacting Make
- unsupported LinkedIn image media raises before contacting Make or LinkedIn
  upload/post creation in mixed-platform publishing
- non-2xx Make responses raise through `response.raise_for_status()`
- network failures from `requests.post` propagate as publish failures
- scheduler processing rolls back the session for failed posts
- scheduler failures are isolated per due post where possible
- scheduled failed posts are marked `schedule_failed` when that status update can
  be committed
- Cloudinary/media failures occur before publishing and should prevent payload
  delivery

Webhook URLs and secrets must not be printed or logged. Diagnostics should report
safe facts such as post type, webhook configured true/false, platform list and
media count.

## Compatibility Layer

`app.py` intentionally keeps publishing compatibility wrappers:

- `send_payload_to_make`
- `build_single_payload`
- `build_carousel_payload`
- `get_user_connected_accounts`
- `get_enabled_platforms_for_user`
- `get_user_make_webhook`
- `publish_post_to_make`
- `prepare_linkedin_post`
- `publish_prepared_linkedin_post`
- `publish_linkedin_post`
- `publish_linkedin_text_post`

These wrappers preserve:

- existing imports
- existing monkeypatches
- existing tests
- helper bridge behavior
- configured runtime dependency injection

The wrappers are a compatibility boundary while publishing remains stable across
route and service extraction.

## Testing

Current regression coverage includes:

- publishing service behavior
- scheduler service behavior
- single payload construction
- carousel payload construction
- manual single publishing
- manual carousel publishing
- scheduled single publishing
- scheduled carousel publishing
- duplicate-send protection
- no enabled platforms
- missing webhooks
- non-2xx Make responses
- cross-user carousel access protection
- TikTok and Content Pack helper compatibility
- media normalization and Cloudinary wrapper contracts
- LinkedIn text and single-image personal-profile publishing

Tests mock external HTTP/API calls. Unit and regression tests must not call real
Make webhooks, Cloudinary, OpenAI or TikTok network paths.

## Future Work

Publishing-related future work:

- LinkedIn carousel publishing
- Pinterest scheduling
- analytics
- bulk publishing
- AI publishing improvements

Broader product planning belongs in `ROADMAP.md`.

## Related Documentation

- `ARCHITECTURE.md`
- `TEST_PLAN.md`
- `ROADMAP.md`
- `ENVIRONMENT.md`
