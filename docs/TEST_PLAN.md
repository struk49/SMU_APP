# Test Plan

## Smoke tests
- Flask starts without traceback.
- Login and logout.
- Dashboard loads.
- Connected Accounts loads and saves.
- AI Studio loads.

## Manual publishing
1. Create a single-image draft.
2. Enable Facebook only.
3. Confirm single webhook configured.
4. Click Send to Make.
5. Confirm one request reaches Make.
6. Confirm payload contains the public image URL and `platforms: ["facebook"]`.
7. Confirm post status changes after success.

## Scheduled publishing
1. Create a fresh single-image draft.
2. Schedule two minutes ahead.
3. Confirm database stores UTC.
4. Confirm UI shows user-local time.
5. Confirm scheduler logs one due post.
6. Confirm one request reaches the owner's single webhook.
7. Confirm status becomes `sent_to_make`.

## Carousel regression
- Generate a TikTok carousel.
- Confirm all images complete.
- Send manually.
- Schedule a fresh carousel.
- Confirm exactly one carousel payload per group.

## Failure tests
- No enabled platform.
- Missing webhook.
- Make returns HTTP 400/500.
- Invalid media URL.
- Database exception followed by a successful request, proving rollback works.
