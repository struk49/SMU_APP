# SMU text-model migration audit

Audit date: 21 October 2026. Worktree: `C:/Users/andre/smu_app-restore-complete-image`.
Branch: `codex/content-pack-complete-image-restore`.
Base HEAD: `06a913f774cae78fcfb036aa2381d26041297dfd`.

## Verified official documentation

- [Deprecations](https://developers.openai.com/api/docs/deprecations): the 14 October 2026 notice schedules GPT-4.1 and GPT-4.1 mini snapshots for shutdown on **14 January 2027**, recommending `gpt-6-luna`.
- [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna): supports Responses and structured outputs; `reasoning.effort=none` is supported, while the documented default is `medium`.
- [Migration compatibility](https://developers.openai.com/api/docs/guides/latest-model): preserve effective reasoning behavior where supported; sampling parameters are restricted when reasoning is enabled.
- [Pricing](https://developers.openai.com/api/docs/pricing), [GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini), [GPT-4.1](https://developers.openai.com/api/docs/models/gpt-4.1): rates compared below.

The requested markdown migration-guide URL was retried but the browsing tool rejected
its content type. The corresponding official HTML guide and Luna-specific model page
provided the compatibility facts used here. No account/model-list provider call was
made; account availability and real output quality remain unverified by this change.

## Actual active selections at the base revision

All eight production text request sites use `openai_client.responses.create` (or its
bounded `request_client` clone). All eight hard-code **gpt-4.1-mini**, not GPT-4.1.
They share the global client through app wrappers, but use separate prompts and contracts.

| Production request | Consumer and output contract | Original settings |
| --- | --- | --- |
| `content.generate_content_pack` | `/content-pack`; ten editable labelled sections, now validated before success | mini; 35s timeout; zero SDK retries |
| `content.repair_carousel_structure` | Existing create-carousel repair after deterministic repair declines; labelled slides or UNREPAIRABLE | mini; 35s timeout; zero SDK retries |
| `app.rewrite_caption_with_ai` | Caption rewrite/regenerate routes; rewritten plain caption | mini; shared SDK defaults |
| `app.improve_post_with_ai` | Post improvement; plain caption based on grade/brand | mini; shared SDK defaults |
| `app.evaluate_brand_match` | Brand Coach updates; JSON requested in the prompt and parsed locally | mini; shared SDK defaults |
| `captions.rewrite_caption_with_action` | Studio rewrite actions through app wrapper; plain caption | mini; shared SDK defaults |
| `captions.grade_post_with_ai` | Studio grading through app wrapper; labelled scores/feedback | mini; shared SDK defaults |
| `tiktok.repurpose_tiktok_content` | TikTok repurpose wrapper/route; strict JSON schema with five required strings and local validation | mini; shared SDK defaults |

No active text-model environment override existed in Config or these services. No
model-named override key was present in this worktree's local .env or the auditing
process's OpenAI environment. Deployment environment/production telemetry was not
accessed. No active full GPT-4.1 selection was found. Historical acceptance scripts,
responses and review documents are evidence, not active model configuration, and were
not rewritten.

A UI action need not equal one text call: improving a post can also refresh Brand
Coach, and Studio rewrite/regrade/save and AI-editor save can trigger Brand Coach.
Those use the separate evaluation request above. Content Pack platform formatting in
`social_text.py` is deterministic and adds no text-provider request. TikTok transcript
extraction can use caption metadata or a separate audio transcription call before text
repurposing. Shared SDK defaults outside the bounded pack/repair paths include SDK
retries of the same model; this patch does not change that existing behavior.

## Prepared migration and explicit compatibility decisions

`smu_core/services/text_models.py` owns the default `gpt-6-luna` and returns the model
and `reasoning={"effort": "none"}` for all eight request sites. This preserves the
former model's non-reasoning workload rather than inheriting Luna's medium default.

`Config.OPENAI_TEXT_MODEL` follows the existing os.getenv/Flask configuration convention;
`.env.example` documents it. In Flask requests the app configuration wins. Standalone
service calls use the same environment variable. Whitespace around a supplied model ID
is trimmed. Blank/non-string settings fail; retiring GPT-4.1/GPT-4o family overrides
are rejected before transport. There is no model fallback. Other explicit overrides
must support Responses and `reasoning.effort=none`; provider incompatibility is surfaced
through the existing caller failure behavior, never handled by silently removing settings
or retrying a retiring model. No separate sampling/reasoning override existed to migrate.

The endpoint remains POST `/v1/responses`. Installed and pinned **openai 2.37.0** accepts
`model`, `input`, `reasoning`, `text` and `timeout`; actual SDK serialization and response
parsing are exercised through `httpx.MockTransport`, not just fake service objects.
No SDK upgrade or requirements change is needed for these parameters. No current text
request sends `temperature`, `top_p`, `max_tokens`, Chat Completions' `reasoning_effort`,
function tools or streaming settings, so there are no unsupported existing parameters
to discard. TikTok's existing `text.format` JSON schema is retained exactly.

All eight prompt expressions were compared with HEAD using Python AST and are unchanged.
No prompt rewriting or output-format migration was introduced. TikTok logs now report
the selected model instead of stale hard-coded metadata.

## Preserved safeguards and excluded scope

The Content Pack completion/section/label/count/exact-copy/pair-multiplicity gate is
unchanged. Successful provider text remains editable and unchanged. Its saved prose-only
failure and other invalid outputs are still rejected before success; route regressions
retain exactly one release-helper call and a zero final pack balance. Signed intent
and create-carousel validation remain independent. Repair eligibility, deterministic
repair, bounded external repair, pair preservation and subsequent image/post checks are
unchanged. Existing Brand Coach exception handling is also unchanged.

Image model `gpt-image-2.5-flare`, its explicit benchmark overrides, and transcription
model `gpt-4o-mini-transcribe` remain unchanged. No image/audio SDK options, design,
image prompts, worker/polling, publishing, scheduling, database schema or template
changes are included. Audio retirement planning is outside this requested text migration.

The diff is limited to Config/.env.example, one new text-settings helper, the four
existing modules containing eight text calls, four existing model-assertion test
modules, a new HTTP-mocked migration test module, and this document. Existing acceptance
artifacts remain untracked and untouched. Original main remains at
`cd80f46e187209d21f3a02908aeeee1eb968644e`. No commit, push or deployment was performed.

## Official pricing comparison

USD per 1 million tokens, Standard processing, ordinary context length:

| Model | Input | Cached input | Output | Active SMU usage before migration |
| --- | ---: | ---: | ---: | --- |
| GPT-4.1 mini | $0.40 | $0.10 | $1.60 | All eight text request sites |
| GPT-4.1 | $2.00 | $0.50 | $8.00 | None found |
| GPT-6 Luna | $0.10 | $0.01 | $0.50 | Prepared default for all eight |

Versus actual mini usage, equal token volumes cost **75% less input, 90% less cached
input and 68.75% less output**. Versus full GPT-4.1, the reductions are 95%, 98% and
93.75%, respectively; that second comparison is not SMU's current baseline. Token
volumes/quality can change between models; these are rate comparisons, not a guaranteed
per-pack bill. Luna documents higher rates above 272K input tokens, cache-write charges,
and processing-mode/regional adjustments; those are not requested by this patch.
Application pack/image credit prices and reserve/release rules are unchanged.

## Mocked verification

The new SDK-transport tests cover each of the eight app/service paths with the default
and an explicit test override; POST Responses serialization, effort=none, pack/repair
timeouts, unchanged free-text/strict-JSON contracts, typed provider rejection with no
retiring-model fallback, invalid settings failing before transport, standalone env
selection and Flask configuration precedence. All transport uses a test key and
MockTransport. Existing production-route regressions exercise credit release, valid
business/gardening packs reaching normal carousel creation, malformed and saved prose
output, exact copy and multiplicity, and independent signed-intent safeguards.


### Results

Using `C:/Users/andre/smu_app/venv/Scripts/python.exe`:

- `-m pytest tests/test_text_model_migration.py tests/test_content_service.py tests/test_content_pack_blueprint.py tests/test_generation_contract.py tests/test_usage_credit_limits.py tests/test_caption_service.py tests/test_caption_utilities_blueprint.py tests/test_post_studio_brand_coach.py tests/test_tiktok_service.py tests/test_tiktok_blueprint.py tests/test_tiktok_workflow.py tests/test_tiktok_transcript_extraction.py tests/test_image_generation_service.py -q`: **611 passed**, 17 existing SQLAlchemy Query.get deprecation warnings, 46.39 seconds.
- Initial focused SDK/service run: 194 passed. The first broader run exposed one remaining old-model expectation in a create-carousel repair wiring test; only that assertion was updated before the clean final run.
- `-m compileall -q app.py config.py smu_core tests`: passed.
- `git diff --check`: passed; new helper, test and audit files also checked using no-index whitespace comparison, with no whitespace errors.
- Python AST comparison of all eight prompt expressions against HEAD: unchanged.
- Tracked non-Python application/configuration files were also searched for model selectors; no additional active selector was found.
- Complete production and test diff reviewed: 13 files in scope (10 modified, 3 new); existing acceptance artifacts excluded and unchanged.

The preparation is complete for review. Real target-model account availability,
latency and content quality have not been tested, as instructed. No production access,
real provider calls, commit, push or deployment occurred. The branch HEAD remains
`06a913f774cae78fcfb036aa2381d26041297dfd`.


## Final pre-commit review

The complete 13-file diff was reviewed again before the user-authorized commit.
The declared requirements pin and installed SDK both equal openai 2.37.0; its actual
Responses request signatures and HTTP-mocked serialization support the migrated calls.
AST inspection confirms all eight active text request sites use the shared model
setting. The full mocked selection/validation/credit/TikTok/image/transcription suite
passed again: 611 tests, 17 existing deprecation warnings, 46.16 seconds. Compile and
whitespace checks passed. No blockers or unrelated source changes remain.

The user authorized one isolated Luna Content Pack request after committing, with
SDK retries disabled and no repair/regeneration. Its output, actual model, attempt
count, latency and in-memory credit outcome will be saved separately under excluded
acceptance artifacts. No production database or credit ledger will be accessed.
