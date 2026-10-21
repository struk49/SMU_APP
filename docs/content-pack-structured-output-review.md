# Content Pack structured-output correction

Base HEAD: `8516c496d63f953af15120929fe4c5a58fb3330f`.
Worktree: `C:/Users/andre/smu_app-restore-complete-image`.
Branch: `codex/content-pack-complete-image-restore`.

## Saved failure: provider emission, not response-handling duplication

The saved `provider-response.json` contains one message item with one output_text
content block. That single raw provider block contains two LINKEDIN_POST headings,
including the same LinkedIn copy twice. SDK 2.37.0's Response.output_text property
concatenates output_text blocks; with this single block it returns exactly the saved
provider text. The saved provider-output.txt equals that property. The duplication
therefore exists inside the provider emission, before SMU processing.

The saved harness arguments were used to reconstruct the production request offline
at the base HEAD, using a capture-only fake client. Its parameters were model=gpt-6-luna,
reasoning.effort=none, input=the assembled prompt, timeout=35.0. Client options were
max_retries=0 and timeout=35.0. The prompt's return template contains LINKEDIN_POST once.
There was no text.format schema in the request; the saved response also records
text.format.type=text. The reconstruction is saved separately as
acceptance-runs/content-pack-luna-one-shot-20261021/reconstructed-production-request.json;
it is an offline reconstruction, not a previously recorded HTTP request.

The existing output validator correctly rejected the duplicate section. The failed
acceptance run made one request, released its isolated reserved pack credit once,
created no post rows and used no image credits. Nothing here salvages or rewrites that
failed output into a success.

## Verified support and necessary correction

Official documentation checked on 21 October 2026:

- [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) lists Responses and structured-output support.
- [Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses) documents text.format with type=json_schema and strict=true. Every object property must be required, unused optional values may be null, and objects must set additionalProperties=false. Refusals/incomplete responses still require explicit handling.

The installed/declared OpenAI SDK remains 2.37.0. The existing pinned Pydantic 2.13.4
provides the schema and local strict validation; no dependency change is needed.

Only Content Pack generation changes its wire contract. The new module
smu_core/services/content_pack_output.py defines ten required section properties,
including one CAROUSEL_IDEA array containing 2–6 typed slide objects. Each slide has
one title, nullable supported copy/visual fields, and an ordered phrase_pairs array
with explicit phrase and translation fields. All properties are required at the
wire level and all object types forbid extras. The model must send null for absent
optional fields; non-null empty fields fail locally.

The generation request supplies this schema through Responses text.format with
strict=true. The plain-text return template and marker/pair emission instructions are aligned
with JSON transport; labelled examples describe serializer output only; source fidelity, brand, design, copy and requested-count
instructions remain. This is a request contract plus local enforcement, not another
prompt-only duplicate warning.

Response handling requires completed status, rejects any refusal, parses exactly
one JSON value and rejects duplicate object keys at every level before validation.
It does not use ordinary last-key-wins parsing, select one of multiple JSON messages,
strip code fences, remove duplicated content, infer prose boundaries, repair output,
or substitute the user's request. Invalid types, missing/extra properties, invalid
JSON constants, concatenated JSON and truncated JSON fail.

A deterministic serializer emits the existing ten labelled editable sections in a
fixed order and Slide 1 through Slide N from the explicit ordered array. Every
supported non-null field and every phrase pair is emitted without rewriting its
copy. Null fields represent intentional absence. Field/section order is independent
of JSON object key order; pair order and multiplicity are preserved. No arbitrary
prose is split to invent slides. The failed free-text response is rejected unchanged.

The serialized text then passes the existing generation contract and Content Pack
output validator, including explicit counts, supported range, labelled slides,
required exact-copy values and phrase-pair multiplicity. Any section/slide delimiters
in field text that violate the editable contract are rejected, not removed.

## Safeguards, accounting and scope

The shared gpt-6-luna setting and reasoning.effort=none are unchanged. All eight
migrated text paths still use that setting. The other seven request sites—including
existing create-carousel repair and TikTok's own schema—are unchanged. Independent
signed-intent and create-carousel validation, editable-text behavior, image/audio
models, image prompts/design, workers, publishing and scheduling are unchanged.

No retries/repair calls were added. Content Pack keeps a 35-second timeout and zero
SDK retries. Invalid structured output, refusal or incomplete output maps to the
existing ContentPackGenerationError(invalid_output) route path, preserving user input,
showing the existing actionable error and releasing the reserved pack credit once.
Failures precede successful pack presentation, intent registration, image credits,
image workers and post rows. Valid output retains the usual one-pack charge. Provider
billing, if incurred, is separate from application-credit release.

Mock fixtures for successful historical editable-text tests now explicitly construct
the new provider JSON format in a test-only helper. Production has no text-to-JSON
fallback. Invalid historical text fixtures, including saved prose and duplicated
LinkedIn output, stay raw and fail. The saved duplicate's complete text is copied to
tests/fixtures/content_pack_luna_duplicate.txt; both LinkedIn sections remain intact.
Acceptance artifacts remain excluded and otherwise preserved.

The planned diff is limited to the content service, the new structured-output module,
three existing regression modules, a test-only fixture adapter, the full failed-text
fixture, new schema/SDK/route regressions, and this review document. No shared model
configuration, other text services, routes/templates or unrelated sources changed.

## Verification and limits

Initial adapted service/route/migration suites: 439 passed. The first broad run found
one new test response missing three required SDK metadata fields; the fixture was
corrected, without a production behavior change.

The new regressions cover the saved single-block provider duplication and unchanged
SDK text, schema required/extra-field rules, valid deterministic serialization and
multiline/Unicode copy, repeated phrase pairs, duplicate JSON keys at top/nested
levels, extra/missing/wrong-type fields, empty fields, truncation, concatenated JSON,
multiple output messages, refusal even alongside valid JSON, incomplete/missing
status, and exactly-once real local credit release for saved failure/refusal/partial
responses before image credits, workers, repairs or post rows. Existing tests cover
exact requested counts/copy/multiplicity and valid business/gardening packs reaching
the normal carousel route without editing. All eight text paths retain SDK-mocked
migration coverage.

No real provider calls or production access were made. Target-model output quality
and live schema acceptance have not been retested; only official compatibility and
mocked SDK/contract behavior are verified. No commit, push or deployment was performed.


### Final results and diff review

Using `C:/Users/andre/smu_app/venv/Scripts/python.exe`:

- `-m pytest tests/test_content_pack_output.py tests/test_text_model_migration.py tests/test_content_service.py tests/test_content_pack_blueprint.py tests/test_generation_contract.py tests/test_usage_credit_limits.py tests/test_caption_service.py tests/test_caption_utilities_blueprint.py tests/test_post_studio_brand_coach.py tests/test_tiktok_service.py tests/test_tiktok_blueprint.py tests/test_tiktok_workflow.py tests/test_tiktok_transcript_extraction.py tests/test_image_generation_service.py -q`: **632 passed**, 17 existing SQLAlchemy Query.get deprecation warnings, 47.24 seconds.
- After the final prompt wire-format alignment, `-m pytest tests/test_content_service.py tests/test_content_pack_output.py -q`: **106 passed** in 3.11 seconds.
- `-m compileall -q app.py config.py smu_core tests`: passed.
- Tracked and new files checked for whitespace errors; complete production, test and fixture diff reviewed.
- Saved provider-output.txt and regression fixture SHA256 both equal `b9c60c4c2661f66d0f373dc9b28c7c9e33c00d8b912b535d52e85cbb155133b6`.
- Nine files in scope: two production modules, six test/fixture files and this document (four modified files and five new files in total).

HEAD remains `8516c496d63f953af15120929fe4c5a58fb3330f`. Original main remains
`cd80f46e187209d21f3a02908aeeee1eb968644e`. All changes remain uncommitted for review;
acceptance artifacts are excluded from the proposed source diff.


## Final commit review

The complete nine-file source/test/fixture/document diff was reviewed again. The
final full mocked suite passed 632 tests (17 existing SQLAlchemy deprecation warnings)
in 46.39 seconds. Existing exact-copy/count and signed-intent checks, credit release,
all eight text paths and unchanged image/transcription selection remain covered.
Only the reviewed nine files are to be committed; acceptance-run artifacts are excluded.
The user authorized one subsequent real ordinary-brief text attempt with retries
and repairs disabled, followed by isolated normal carousel creation with mocked images.
Those acceptance results will be recorded separately without production access.
