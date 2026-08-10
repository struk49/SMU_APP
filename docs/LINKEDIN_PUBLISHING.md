# LinkedIn Publishing

Research date: 2026-08-10.

This document defines the verified LinkedIn publishing contract and proposed SMU
implementation plan before code is written. It is research and planning only.

Primary LinkedIn sources:

- [Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api?view=li-lms-2026-06)
- [Post Schema](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/post-api-schema?view=li-lms-2026-02)
- [Images API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/images-api?view=li-lms-2026-06)
- [MultiImage API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/multiimage-post-api?view=li-lms-2026-03)
- [Videos API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/videos-api?view=li-lms-2026-03)
- [Authorization Code Flow](https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow)
- [Refresh Tokens with OAuth 2.0](https://learn.microsoft.com/en-us/linkedin/shared/authentication/programmatic-refresh-tokens)
- [Organization Access Control by Role](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/organizations/organization-access-control-by-role?view=li-lms-2026-03)
- [Increasing Access](https://learn.microsoft.com/en-us/linkedin/marketing/increasing-access?view=li-lms-2026-05)
- [Rate Limiting](https://learn.microsoft.com/en-us/linkedin/shared/api-guide/concepts/rate-limits)

## 1. API Choice

Use LinkedIn's current versioned REST APIs:

- Posts API for creating organic posts.
- Images API for image upload and image URNs.
- MultiImage API through Posts API content for organic multiple-image posts.
- Videos API for video upload and video URNs.

The Posts API supersedes the old `ugcPosts` API. New SMU work should avoid
building on legacy `ugcPosts` or old share API assumptions unless a specific
backward-compatibility problem is proven.

LinkedIn distinguishes two concepts that SMU must not collapse:

| LinkedIn concept | Organic? | Sponsored? | SMU mapping |
| --- | --- | --- | --- |
| MultiImage | Yes | No | SMU carousel MVP |
| Carousel | No | Yes | Out of scope unless SMU adds sponsored publishing |

SMU's existing "carousel" should map to LinkedIn organic MultiImage unless the
product requirement changes to paid/sponsored carousel ads.

## 2. Supported MVP Content Types

LinkedIn Posts API supports these content types for organic posts:

- text-only
- image
- video
- document
- article
- MultiImage
- poll
- celebration

Recommended SMU MVP support:

| Content type | LinkedIn support | SMU MVP status |
| --- | --- | --- |
| Text-only | Organic supported | MVP 1 |
| Single image | Organic supported after image upload | MVP 1 |
| MultiImage | Organic supported after uploading 2-20 images | MVP 2 |
| Video | Organic supported after video upload/finalize | Later MVP |
| Document | Organic supported | Out of scope initially |
| Article | Organic supported | Out of scope initially |
| Poll | Organic supported | Out of scope initially |
| Celebration | Organic supported | Out of scope initially |

Video is a valid LinkedIn post type, but it is materially more complex than
single image because it uses multipart upload and finalize steps. It should not
be the first LinkedIn publishing milestone.

## 3. MultiImage Limits

Verified LinkedIn MultiImage constraints:

- minimum images: 2
- maximum images: 20
- media type: image URNs, formatted as `urn:li:image:{id}`
- supported formats: JPG, GIF and PNG
- image pixel count: less than 36,152,320 pixels
- GIF support: up to 250 frames
- each image may include optional alt text

SMU mapping:

- each SMU carousel child `Post` row maps to one LinkedIn image upload
- ordered SMU carousel rows become the ordered LinkedIn MultiImage `images`
  array
- current SMU ordering is cover first, then `sort_order`, then ID
- SMU `is_cover` may not have a one-to-one LinkedIn meaning and needs real API
  testing
- SMU must reject or split carousels above 20 LinkedIn images before attempting
  upload

## 4. Authentication

LinkedIn publishing uses 3-legged OAuth so a LinkedIn member explicitly grants
the application permission to act on their behalf.

Personal/member publishing:

- permission: `w_member_social`
- author: member/person URN

Organization/Page publishing:

- permission: `w_organization_social`
- author: organization URN
- the authenticated member must have an appropriate role on that organization

Do not assume every LinkedIn user can publish to every Page. Organization access
must be checked and stored deliberately.

OAuth implementation requirements for later code:

- register absolute HTTPS redirect URLs in the LinkedIn Developer Portal
- generate and validate OAuth `state` for CSRF protection
- exchange the authorization code for an access token
- store token expiry
- request only the scopes required for the chosen MVP

MVP personal-profile OAuth uses `openid profile w_member_social`. The OIDC
`profile` scope provides a subject identifier through LinkedIn userinfo, and
`w_member_social` grants member social posting permission.

## 5. Author Identifiers

LinkedIn Posts API expects the post `author` field to be a Person or
Organization URN.

Expected formats:

- personal/member author: `urn:li:person:{id}`
- organization/page author: `urn:li:organization:{id}`

For a person author, SMU retrieves the OIDC userinfo `sub` claim and constructs
`urn:li:person:{sub}`.

For an organization author, LinkedIn documentation points to finding the member's
organization and validating their organization access role.

## 6. Required HTTP Headers

LinkedIn API calls should use these headers where applicable:

```http
Authorization: Bearer {access_token}
Linkedin-Version: YYYYMM
X-Restli-Protocol-Version: 2.0.0
Content-Type: application/json
```

Upload calls to LinkedIn-provided upload URLs may require binary upload headers
appropriate to the upload step. Do not assume the same JSON headers apply to the
binary upload body.

Future implementation should centralize the LinkedIn API version in one config
point, for example `LINKEDIN_API_VERSION`, rather than scattering a date string
through the codebase.

## 7. Image Upload Flow

LinkedIn image posting requires image upload before post creation.

Conceptual flow:

```text
initialize image upload with owner URN
-> receive upload URL and image URN
-> upload image binary to the upload URL
-> create a Post referencing the image URN
```

Important details:

- initialize upload endpoint: `POST /rest/images?action=initializeUpload`
- request owner is a member or organization URN
- response includes an upload URL and an image URN
- post creation references the image URN in `content.media.id`
- image permissions depend on owner type

SMU currently stores media as Cloudinary URLs. A future LinkedIn adapter must
download or otherwise resolve the binary media from the stored SMU asset before
uploading it to LinkedIn.

## 8. MultiImage Flow

Conceptual flow:

```text
SMU ordered carousel rows
-> validate image count and formats
-> upload every image through Images API
-> collect LinkedIn image URNs
-> build Posts API content.multiImage.images
-> create LinkedIn organic post
```

SMU ordering should be preserved in the order sent to LinkedIn. Cover semantics
and display behavior must be tested against real LinkedIn output because
LinkedIn MultiImage does not expose SMU's `is_cover` field directly.

LinkedIn requires that all images are owned by the author or organization.

## 9. Video Flow

LinkedIn video posting requires the Videos API before post creation.

Conceptual flow:

```text
initialize video upload with owner URN and file size
-> receive one or more upload instructions
-> split/upload video parts as required
-> collect uploaded part IDs from upload responses
-> finalize video upload
-> create a Post referencing the video URN
```

Verified high-level video constraints:

- length: 3 seconds to 30 minutes
- file size: 75 KB to 500 MB
- format: MP4

Video should come after text-only and image MVP work because upload and finalize
behavior is more complex than image publishing.

## 10. Personal Profile vs Organization Page

| Area | Personal profile | Organization page |
| --- | --- | --- |
| Permission | `w_member_social` | `w_organization_social` |
| Author URN | `urn:li:person:{id}` | `urn:li:organization:{id}` |
| Role requirement | token member must match author | authenticated member needs valid Page role |
| Onboarding complexity | lower | higher |
| User experience | good for creator-led beta | better for businesses and agencies |
| Recommended beta order | first | second |

Organization publishing should validate role access before enabling the target.
Relevant roles in current docs include administrator and content-oriented roles
such as `DIRECT_SPONSORED_CONTENT_POSTER` and `CONTENT_ADMIN` /
`CONTENT_ADMINISTRATOR`, depending on the specific API page and endpoint.

## 11. Rate Limits / Access Restrictions

LinkedIn uses rate limits at both application and member levels. Rate-limited
requests return HTTP 429.

LinkedIn does not publish standard endpoint-by-endpoint rate limits in general
documentation. The Developer Portal shows app-specific limits for endpoints the
app has called.

Community Management API access has Development and Standard tiers. Current
LinkedIn documentation states Development tier includes API call restrictions
and Standard tier is the production-oriented tier. Application approval and
access upgrades are subject to LinkedIn review.

SMU should treat access as an approval risk until the LinkedIn developer app,
product access and target scopes are confirmed.

## 12. SMU Architecture Mapping

Current SMU architecture:

- routes live in blueprints
- publishing orchestration lives in `smu_core.services.publishing`
- scheduled-post processing lives in `smu_core.services.scheduler`
- media helpers live in `smu_core.services.media`
- image generation lives in `smu_core.services.images`
- `app.py` remains the compatibility/startup boundary

Proposed future platform adapter location:

```text
smu_core/services/platforms/
    __init__.py
    linkedin.py
```

This should begin as a LinkedIn-only adapter. Do not move Instagram/Facebook
code merely to create symmetry.

## 13. Proposed LinkedIn Adapter Responsibilities

Future `smu_core/services/platforms/linkedin.py` should own:

- LinkedIn request headers
- API version constant/config access
- author URN handling
- image upload initialization
- binary image upload
- video upload steps when implemented
- MultiImage request construction
- LinkedIn-specific post payload construction
- LinkedIn HTTP response parsing
- LinkedIn error translation

It must not own:

- scheduler job registration
- generic `Post` database queries
- Make.com publishing
- Cloudinary upload/storage
- caption generation
- route redirects or flash messages
- app startup side effects

The generic publishing service can call the adapter once the platform decision
and token model are implemented.

## 14. Direct API vs Make.com Decision

| Area | Make.com LinkedIn publishing | Direct LinkedIn API |
| --- | --- | --- |
| OAuth/token ownership | delegated to Make or split between systems | owned by SMU |
| Reliability | depends on Make scenario behavior | depends on SMU adapter and LinkedIn |
| Existing scheduling | fits current Make webhook path | requires adapter integration |
| Media upload | hidden or configured in Make | explicit and testable in SMU |
| Testing | harder to unit test fully | adapter can be mocked precisely |
| Analytics potential | limited by Make flow | better foundation for retrieval and analytics |
| Error handling | Make may obscure LinkedIn-specific failures | SMU can map LinkedIn errors directly |
| Complexity | lower short-term | higher initial build |
| Long-term platform architecture | weaker | stronger |

Recommendation: build LinkedIn as a direct API integration, not as another
Make.com-only path.

Reasoning:

- LinkedIn requires OAuth, author URNs and upload workflows that are core account
  state, not just a webhook payload.
- Direct integration gives SMU clearer token ownership, testing, diagnostics and
  future analytics.
- Make.com can remain the current Instagram/Facebook/Pinterest publishing bridge
  while LinkedIn becomes the first explicit platform adapter.

Do not implement either path until OAuth data requirements and developer access
are approved.

## 15. Connected Account Data Requirements

Current `ConnectedAccount` fields:

- `linkedin_connected`
- generic platform booleans
- single/carousel Make webhook fields
- no LinkedIn token or author fields

Minimum future LinkedIn data concepts:

- LinkedIn connected flag
- access token
- access token expiry
- refresh token only if the app is approved for LinkedIn programmatic refresh
  tokens
- refresh token expiry if refresh tokens are available
- granted scope string
- member/person ID
- member/person URN
- selected publishing target type: `person` or `organization`
- selected organization URN when Page publishing is enabled
- selected organization display name where permitted
- verified organization role/state metadata
- last LinkedIn connection or validation timestamp

Do not assume refresh-token support. LinkedIn documents programmatic refresh
tokens for approved Marketing Developer Platform partners; if unavailable, SMU
must rely on access token expiry and reauthorization.

The OAuth storage slice adds these nullable fields to `ConnectedAccount`:

- `linkedin_access_token`
- `linkedin_access_token_expires_at`
- `linkedin_scopes`
- `linkedin_member_id`
- `linkedin_member_urn`
- `linkedin_display_name`
- `linkedin_refresh_token`
- `linkedin_refresh_token_expires_at`

Refresh token fields remain optional because LinkedIn does not return refresh
tokens to every app.

## 16. Token Security

Security requirements:

- tokens must never be logged
- tokens must not be exposed in templates
- OAuth client secrets must not be sent in URLs
- token values should be encrypted or otherwise protected in production storage
- access token expiry must be tracked
- expired or revoked tokens should trigger reauthorization
- refresh tokens, if available, must be treated as highly sensitive
- OAuth callback must validate `state` to protect against CSRF
- disconnect/reconnect flows should revoke or clear local token state where
  appropriate

## 17. Error Model

Expected LinkedIn failure categories and proposed SMU messages:

| Failure | Proposed user-facing message |
| --- | --- |
| expired or invalid token | "LinkedIn needs to be reconnected before publishing." |
| insufficient permission | "LinkedIn did not grant the required publishing permission." |
| invalid organization role | "Your LinkedIn account does not have permission to publish to this Page." |
| failed media upload | "LinkedIn could not upload this media. Please try another file." |
| rejected media | "LinkedIn rejected this media because it does not meet their requirements." |
| invalid author URN | "The selected LinkedIn publishing target is invalid. Reconnect LinkedIn." |
| rate limiting | "LinkedIn rate limit reached. Please try again later." |
| API version error | "LinkedIn API version configuration needs attention." |
| post creation failure | "LinkedIn could not create the post." |

Internal logs should include safe diagnostics such as status code, LinkedIn error
code/category and request stage, but never tokens or full media contents.

## 18. MVP Recommendation

Recommended smallest safe sequence:

### MVP 1

- LinkedIn developer app configuration
- OAuth connection flow
- personal profile publishing
- text-only post
- single-image post
- scheduled text/single-image post
- adapter tests with mocked LinkedIn HTTP calls

### MVP 2

- organization/page target discovery and role validation
- organization/page publishing
- organic MultiImage publishing for SMU carousels
- carousel scheduling with one LinkedIn post per SMU group

### MVP 3

- video upload and publishing
- analytics retrieval
- richer LinkedIn diagnostics
- retry/backoff policy

This order minimizes initial token/role complexity while proving the direct API
adapter and publishing flow.

## 19. Testing Plan

Future tests should not call real LinkedIn APIs.

OAuth tests:

- state generation and validation
- callback with valid code
- callback missing code
- user denied authorization
- missing required scope
- expired token handling
- reconnect flow

Adapter tests:

- headers include Authorization, LinkedIn version and Rest.li protocol version
- text-only post payload
- image upload initialize request
- binary image upload handling
- single-image post payload
- MultiImage payload and ordering
- video upload sequence when implemented
- LinkedIn API failure mapping
- rate-limit response handling

Publishing integration tests:

- manual LinkedIn single publish invokes adapter
- scheduled LinkedIn single publish invokes adapter once
- LinkedIn MultiImage publishes once per SMU carousel group
- cross-user posts cannot use another user's LinkedIn token
- missing token fails before network call
- insufficient organization role blocks Page publishing

Compatibility tests:

- existing Make.com publishing remains unchanged
- existing Connected Accounts behavior remains compatible
- no real OpenAI, Cloudinary, Make.com, TikTok or LinkedIn calls occur in tests

## 20. Current Known Limitations

Known uncertainties requiring real LinkedIn setup:

- LinkedIn developer app product access must be configured and approved
- available scopes depend on the app's approved products
- Community Management tier and production access must be confirmed
- organization/page publishing requires real Page role validation
- MultiImage ordering and cover display require real LinkedIn verification
- media upload behavior must be tested with real development accounts
- refresh-token availability depends on LinkedIn approval
- exact endpoint rate limits are app/endpoint-specific and must be checked in
  the Developer Portal

Do not present any of these as solved until verified.

## Related Documentation

- `ARCHITECTURE.md`
- `PUBLISHING_CONTRACT.md`
- `ENVIRONMENT.md`
- `TEST_PLAN.md`
- `ROADMAP.md`
