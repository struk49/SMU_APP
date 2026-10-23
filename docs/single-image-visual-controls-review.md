# Single-image visual controls: audit and review

Base: current `origin/main`, `83e90a6f96f4e8a6613965e1a6b7a0ae1ba28f05`.
Feature branch: `codex/single-image-visual-controls`. Finalization authorizes a local commit of the reviewed feature, tests, documentation and CI changes. Push, merge, deployment and provider calls remain unauthorized.

## Production-path audit

| Path | Before | Credit lifecycle retained |
| --- | --- | --- |
| Create Post, AI single image | User artwork brief plus legacy style wrapper, synchronous image generation; failures redirected away from submitted text. | Reserve one image before generation; release on provider/processing/database failure. Uploaded media bypasses AI credits. |
| Content Pack, platform draft | Editable platform caption plus generated IMAGE_PROMPT, legacy style wrapper, queued plain prompt. Generated suggestion was promoted to artwork authority. | Enqueue without charging; existing worker reserves one image at execution, releases on failure, marks `generation_failed` and does not select that row again. |
| TikTok Repurpose, single image | Structured repurpose result; single-image route used IMAGE_PROMPT plus legacy style wrapper synchronously. Service already treated transcripts as source and supported separate user instructions, but route/UI/wrapper did not expose them. | Reserve one image before generation; release on provider/processing/database failure. Existing captions and carousel route remain. |

## Implementation

- Extract the accepted provider-complete prompt builder, style grammars, palette directions and treatment directions into `provider_complete.py`. The existing carousel import name remains available. Builder AST and shared directions were compared with the accepted base and are unchanged; only a free-canvas direction is added for single images.
- `single_image.py` validates controls and bounded text, then composes one deterministic prompt. It makes no provider calls and changes no credits. Existing registry IDs and treatment/style compatibility are reused; registry declarations now include the implemented Create Post/TikTok style, palette and treatment support.
- A shared Jinja component exposes Auto, Infographic, Photography, Illustration and Text-led, compatible artwork styles, an independent palette, exact optional image wording and optional visual instructions. Text-led requires visible wording; other treatments support an empty image-text field.
- User visual instructions and selections override compatible generated suggestions and defaults. Captions and generated suggestions remain source data; the exact image-text field alone populates the authorized visible-copy block. There is no automatic caption/transcript-to-image-text conversion, truncation, or extra text generation/repair call. Ordinary briefs need no technical labels.
- Single images use a free canvas. No template, upload editing, restyling, reference-image or placement capability is offered. Unsupported modes/templates and incompatible selections fail before image calls. Legacy `image_style` submissions map explicitly to supported canonical artwork styles; unknown or conflicting styles fail.
- Content Pack section ambiguity/duplicates, missing captions/suggestions, oversized text and control characters fail before queued rows/image work. Inline editable section labels remain supported. This validation is independent of the existing structured generation, exact-copy/count validation, signed carousel intent and create-carousel validation.
- Validation/provider errors re-render submitted text and controls with actionable messages and HTTP 422. Uploaded files must be reselected after an error; browsers cannot repopulate file inputs. Existing upload processing is not changed. Scheduling conversion is unchanged; invalid time input now preserves the form.
- TikTok exposes separate user instructions through the existing structured text service and keeps the transcript source-only. Its single-image visual form and existing carousel-style/action are clearly separate. Instagram caption plus hashtags remains the existing single-draft caption behavior; this change does not generate TikTok videos or alter caption platform consumers.

## Scope and safeguards

Implementation: `app.py` (TikTok keyword forwarding only), the three affected blueprints, `generation_contract.py`, two shared image-prompt services, the three forms and one shared form component. Tests: the four existing affected suites and new single-image production-path regressions. This document records review.

No database migration, dependency/model/provider configuration, price/limit, scheduler/worker/polling, publishing, upload processor or stored-payload parser changes. Content Pack single-image prompts retain the existing queued plain-prompt representation. Existing overlay payloads and provider-complete carousel payloads remain supported. No fallback between image-generation modes is introduced. Existing provider retry configuration is unchanged.

Authentication, subscription, current-user ownership and CSRF decorators/registration remain intact. New CSRF tests reject generation before image credits or rows. Errors do not expose raw provider details.

## Verification

- Final review suite: **797 passed**, including the proposed CI preflight tests and existing smoke harness. Coverage includes route integration, accepted carousel behavior and legacy payloads, structured Content Pack output, exact-copy/count/signed-intent checks, model selection, image generation, usage accounting and authenticated post/status routes. External connections and scheduler startup were blocked with the committed smoke guard; database and credentials were isolated/dummy.
- Focused single-image coverage: **70 cases passed**, including all five treatments across all three production paths; business infographics; gardening photography; text-free images; exact Unicode wording; invalid selections and preserved state; missing/invalid generated text; duplicate sections without silent removal; uploaded-media bypass; real local usage-ledger success/failure; reserve/release call counts; worker failure not retried; CSRF; separate TikTok user instructions; and their preservation after single-image validation failure.
- Shared carousel builder AST and existing style/palette/treatment direction constants match the accepted merge. No carousel prompt rewrite was introduced.
- Python compile checks and `git diff --check` passed. New files were also checked for trailing whitespace. The tests ran locally on Python 3.13; this feature has not been rerun in Linux CI.
- Rendered Create Post, Content Pack and TikTok forms were inspected in the browser using static snapshots produced by authenticated local Flask test clients, isolated SQLite, dummy credentials and blocked external connections. Preserved invalid controls, caption/image-copy separation and the separate TikTok carousel action were inspected. Snapshots are outside the repository.

## Limitations

No real provider or production acceptance test was run. Mocked tests verify validated inputs, request composition, consumers and accounting, not rendered image pixels. Image text fidelity, absence of incidental text and scene quality remain image-provider behavior, as with the accepted provider-complete carousel approach; there is no OCR verification or automatic regeneration. Users must explicitly enter the wording they authorize in the image-text field, including wording mentioned in an ordinary brief. Existing synchronous/worker lifecycles, concurrency behavior and provider retries are retained.

Original main and its 57 dirty files, the restoration worktree and its acceptance artifacts are preserved. Generated snapshots and acceptance artifacts are excluded from the release scope.

## Final review and Linux CI preparation

Final review confirmed shared controls on all three paths, distinct caption/source data, exact image wording and visual instructions, unchanged credit lifecycles, upload bypass, ownership, CSRF, signed intent and legacy payload support. Only two confirmed feature blockers required correction:

- The shared UI now explicitly says wording requested in a brief or visual instructions must also be entered in the image-text field. Leaving that field blank intentionally requests a text-free image. Rendered responses on all three paths are checked for this guidance.
- TikTok now carries the separate user-instructions value through the single-image form and validation/provider-error re-rendering, including edits made before submission. A mocked production-route regression verifies preservation.

No AI calls or broader image behavior were added. The extracted carousel builder AST and original style/palette/treatment directions were rechecked against the accepted release base and match. Local final review ran 797 tests successfully on Python 3.13; it does not establish Linux/Python 3.14.3 compatibility. Python compile checks passed. Inserted-line ending fixes passed `git diff --check` before the final documentation update.

The original Linux workflow accepted only the restoration branch and would reject this feature branch. The reviewed CI changes modify `.github/workflows/restoration-linux-smoke.yml`, add `scripts/ci/single_image_checks.py` and `tests/test_single_image_ci.py`, and adjust the existing `tests/test_linux_smoke_harness.py` Git revision probe for the root-owned network-namespace process:

- Permit exactly the restoration and single-image feature branches in same-repository PRs targeting main; other branches/forks skip the job. Draft PRs remain eligible. No manual dispatch or `pull_request_target`.
- Retain Ubuntu 24.04, exact Python 3.14.3, pinned checkout/setup actions, read-only repository permissions, no secrets, credential-free checkout and clean committed-dependency installation. Install pytest 9.1.1 separately for feature regressions; its installed metadata declares Python 3.14 support. Production requirements remain unchanged.
- Verify and report the full PR head SHA, and require the accepted merge as an ancestor for this feature. Test the explicit head rather than a synthetic merge.
- Retain the existing isolated Gunicorn/public/authenticated post/status/worker-import smoke. Run the 15 listed regression suites with separate fresh SQLite state, a loopback-only Linux network namespace, the existing fail-closed Python guard, dummy credentials and scheduling disabled.
- Reject a missing guard, wrong OS/Python, external network interfaces, reused SQLite state or unexpected application revision before pytest/application import. Seven preflight tests pass locally; simulated Linux checks do not claim a real Linux run.
- Record revision/result JSON and JUnit evidence in temporary runner state and append result JSON to the Actions summary. Do not fall back to unrestricted checks if isolation fails.

Actionlint passed for the prepared workflow. All seven workflow shell blocks and both embedded namespace scripts passed `bash -n` without executing them. Python compile and final diff whitespace checks passed. The harness revision probe now supplies `git -c safe.directory=<the exact checkout>` just as the existing smoke helper does; this avoids Git ownership rejection when a root process in the isolated namespace reads a runner-owned checkout. No global Git safety setting or credentials are changed.

After that narrow test-harness adjustment, all 13 existing smoke-harness and new CI preflight tests passed again locally. The prior 797-test review run remains the broad feature/regression evidence; Linux execution remains pending.

The earlier automatic-approval usage-limit interruption was resolved by a subsequently approved finalization run; no approval control was bypassed. The complete reviewed commit scope is 21 files: the 17 feature/test/document files plus four CI files. Current origin/main and the feature base were verified as `83e90a6f96f4e8a6613965e1a6b7a0ae1ba28f05` before committing. No Actions run, push, PR creation, merge, provider call or Render operation was performed. Linux/Python 3.14.3 execution remains pending the separately authorized branch push and draft PR.

## Running isolated Linux CI after separate publication authorization

1. Confirm repository-linked Render services, previews and external automation will not deploy from the branch push or PR. Live dashboard settings have not been inspected by this finalization. Keep auto-deploy disabled. Skip markers are additional defenses, not verification of external settings.
2. Push only `codex/single-image-visual-controls`, without force, from its feature worktree: `git push -u origin HEAD:refs/heads/codex/single-image-visual-controls`. Do not push main.
3. Open a draft PR in `struk49/SMU_APP`, base `main`, compare `codex/single-image-visual-controls`, with title `[skip preview] Add single-image visual controls`. Do not merge it.
4. Check `SMU Linux startup and image checks / linux-smoke`. Verify the Actions summary reports the exact new feature commit SHA, Ubuntu 24.04, Python 3.14.3, successful startup smoke and mocked image/carousel regressions. The workflow tests the PR head, not a synthetic merge. A conflict with main can prevent pull-request workflows from running and requires separate review.

## Exact reviewed file manifest

```text
.github/workflows/restoration-linux-smoke.yml
app.py
docs/single-image-visual-controls-review.md
scripts/ci/single_image_checks.py
smu_core/blueprints/content_pack/routes.py
smu_core/blueprints/posts/routes.py
smu_core/blueprints/tiktok/routes.py
smu_core/services/generation_contract.py
smu_core/services/provider_complete.py
smu_core/services/single_image.py
templates/components/single_image_controls.html
templates/content_pack.html
templates/create_post.html
templates/tiktok.html
tests/test_content_pack_blueprint.py
tests/test_generation_contract.py
tests/test_linux_smoke_harness.py
tests/test_post_create_blueprint.py
tests/test_single_image_ci.py
tests/test_single_image_workflows.py
tests/test_tiktok_blueprint.py
```
