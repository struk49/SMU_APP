# Content Pack text validation review

Worktree: `C:/Users/andre/smu_app-restore-complete-image`
Branch: `codex/content-pack-complete-image-restore`
Base HEAD: `25f56959deb9024082b0074e2b80c8d78c75dd94`

## Root cause and production trace

`app.generate_content_pack` delegates to `smu_core.services.content.generate_content_pack`.
The `/content-pack` route extracts explicit user intent, builds a generation request,
reserves one Content Pack credit, then invokes that helper. The service uses OpenAI
Responses `responses.create(model="gpt-4.1-mini", input=prompt, timeout=35)` with SDK
retries disabled (`max_retries=0`). It reads `response.output_text`.

At HEAD, `validate_generation_output` checks only contract version, locally assigned
request identity/output format, and non-empty text. It does not inspect the Content
Pack sections, labelled slide blocks, requested count or supplied copy. Consequently,
the saved customer-enquiries CAROUSEL_IDEA prose summary passed the generation success
boundary. Signed intent and create-carousel validation occur later and cannot prevent
that earlier false success. They remain independent and unchanged.

The count extractor also missed "exactly N carousel slides"; it now accepts that
wording alongside the existing "exactly N slides" and "N-slide" forms.

## Enforcement choice

Official model documentation lists structured-output support for gpt-4.1-mini:
https://developers.openai.com/api/docs/models/gpt-4.1-mini
Responses supports `text.format` JSON schema:
https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses

This patch uses strict local validation of the existing editable text, avoiding a new
provider JSON contract and serializer. No prompt sentence or provider configuration
was changed. Successful text is returned unchanged, with no prose splitting, inferred
boundaries, truncation, discarded slides or substitution of the original request.

Before returning success, require all ten standard non-empty sections exactly once;
2–6 sequential standalone `Slide N:` blocks; exactly one non-empty Title per slide;
well-formed phrase/translation pairing; the explicit requested slide count; exact
phrase-pair values and multiplicity; and supplied labelled copy at its requested slide
when the instructions explicitly require verbatim preservation. Check provider
completion status even if partial output already contains every section. Missing text
and non-completed responses follow the same typed invalid-output failure path.

Adding REDDIT_POST to the shared section boundary list also prevents its content from
being absorbed into the preceding Pinterest description.

## Failure handling and credits

No automatic retries or repairs were added. Each submission makes at most one text
provider call. Invalid output raises `ContentPackGenerationError("invalid_output")`;
the route releases its reserved Content Pack credit using the existing accounting
helper, shows an actionable retry error, retains the input and source type in the
editable form, and presents no successful pack or carousel action. No post rows,
image credits or image-worker calls occur on this path. A deliberate user retry is a
new submission with the same reserve/release rules. Valid generation retains its
normal one-pack charge. Any upstream provider cost of a rejected text response is not
refunded by the application credit release.

## Regression coverage and preserved evidence

Mocked tests replay the full saved failed provider response, exercise valid business
and gardening packs through generation and normal carousel creation without editing,
and reject explicit count mismatch, changed/missing exact copy, missing/extra repeated
pairs, malformed sections/slides and truncated/non-completed responses. Failure route
tests assert actual database-backed pack credit release, zero image credits, no post
rows and no successful result field. Service tests cover supported range, sequential
numbering, pair ordering/multiplicity and multiline exact copy.

`tests/fixtures/content_pack_prose_failure.txt` preserves the full text of
`acceptance-runs/provider-complete-20261012-174801/generated-content-pack.txt`,
with CRLF normalized to LF for Git whitespace checks. The untouched acceptance
source SHA256 is
`717d1f9f3097e4435ce3451a0bfc6b24029288ba49982ff3b4830108b2f95bda`.
Acceptance artifacts were preserved. Existing worktree changes were retained and
completed. No real provider calls, production access, image regeneration, commit,
push or deployment were performed.

## Limits and scope

The gate enforces structure and explicit machine-readable requirements; it does not
prove factual quality or interpret every possible natural-language copy/count request.
Verbatim checks operate on labelled fields in labelled requested slides and recognize
explicit preservation directives; surrounding line indentation is treated as format.
New provider output using inline/Markdown slide headings is rejected rather than
silently rewritten. Users can still edit successful pack text; create-carousel intent
and validation checks continue to guard later submissions independently.

Infographic design, image prompts, visual controls, grounding, workers/polling,
publishing and scheduling code are unchanged. The only template integration change
retains retry input/source selection; the existing generated-text editing flow remains.


## Verification results

Using `C:/Users/andre/smu_app/venv/Scripts/python.exe`:

- `-m pytest tests/test_content_service.py tests/test_content_pack_blueprint.py tests/test_generation_contract.py tests/test_usage_credit_limits.py tests/test_carousel_generation_service.py tests/test_local_carousel_worker.py tests/test_image_generation_service.py tests/test_social_text_service.py tests/test_post_manual_publish_blueprint.py tests/test_post_schedule_blueprint.py -q`: 756 passed; 14 existing SQLAlchemy Query.get deprecation warnings.
- After the final zero-index copy check and additional copy-field aliases, `-m pytest tests/test_content_service.py tests/test_generation_contract.py -q`: 117 passed, including the new boundary regression.
- `-m compileall -q app.py smu_core tests`: passed.
- `git diff --check`: passed.
- Complete production and test diff reviewed. Branch and HEAD remain as specified above.

No live login/webhook/database/provider checks were run; all application checks used
mocked dependencies and the isolated local test database.


## Pre-commit follow-up review

The complete implementation, template, test and fixture diff was reviewed again.
Valid general-business and gardening outputs are returned unchanged and reach the
normal carousel route without editing. Each invalid-output route regression asserts
exactly one release-helper call and a zero final pack-credit balance using the real
local accounting helper. The exception branches are mutually exclusive; validation
occurs before a successful pack or signed intent is returned. Existing signed-intent
registration, verification and create-carousel validation have no implementation
changes. The small count-extraction extension supplies the same independently checked
intent contract.

No blockers remain within the documented labelled-input contract. The commit scope
is limited to the content service, Content Pack route/template, two regression test
modules, their saved-failure text fixture, and this review. Acceptance-run directories,
databases, images and unrelated files are excluded. The original main reference is
`cd80f46e187209d21f3a02908aeeee1eb968644e` and will remain unchanged.

The user subsequently authorized one isolated real text invocation after committing.
Its result will be kept separately as an excluded acceptance artifact. SDK retries,
automatic repair, image generation and publishing are disabled for that test; only an
in-memory local credit ledger is used. The earlier no-real-provider statement records
the original mocked verification phase.

Pre-commit recheck: 406 mocked content-service, Content Pack route, generation-contract
and usage-credit tests passed. Compile checks passed. The staged fixture line endings
were normalized without modifying the original acceptance evidence.
