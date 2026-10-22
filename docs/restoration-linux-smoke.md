# Pull-request restoration Linux smoke

Reviewed on 22 October 2026 for `codex/content-pack-complete-image-restore`, descending from restoration base `608391b1c2e03dc166f1042a11706cfcca389c27`. The application revision tested is the full PR head SHA, verified against the checkout and reported in the Actions summary and result JSON. No Linux workflow run, push, PR creation or deployment was performed during preparation.

## Running after separately authorized publication

The production version was supplied by the user as **Python 3.14.3** on 22 October 2026. The workflow pins that exact version and verifies the selected interpreter before installing dependencies. No production configuration was accessed. There is no configurable version input or fallback.

The workflow uses only pull_request targeting main, on opened, synchronize, reopened and ready_for_review. Draft PRs are eligible immediately: there is no draft exclusion. It does not use workflow_dispatch or pull_request_target and does not require copying the workflow to main. Merge conflicts can prevent GitHub pull_request checks from running; report/review any conflict instead of merging or changing main automatically.

The check requires the same-repository restoration branch. Checkout explicitly selects github.event.pull_request.head.sha, rather than GitHub's synthetic merge SHA. The checkout must descend from 608391b. This tests the application at the PR head, not merge compatibility with main; both the workflow and production dependencies come from that same committed checkout. Subsequent PR-head pushes trigger a new run with a newly reported SHA.

## Render inspection and future draft-PR steps

Local inspection found no Render Blueprint/service configuration file (including render.yaml), no Render deployment hooks or auto-deploy/preview declarations in the available repository configuration, no inherited RENDER_* environment names, no Render-related key names in restoration/original-main dotenv files, no Render CLI on PATH and no configured Render connector. The Procfile only declares Gunicorn web and scheduler_worker commands; it does not establish service-linked branches, auto-deploy policy or preview configuration. Live Render Dashboard/API settings were not available through those sources and were not verified. In particular, production web/worker linked branches, On Commit / After CI Checks Pass settings, build filters, service PR previews, Blueprint preview generation, shared preview credentials/database targets and external deployment automation remain unknown. Absence of a local Blueprint is not evidence that previews or auto-deploy are disabled in Render.

Before any separately authorized push or PR, a Render owner must inspect all repository-linked services and Blueprints and confirm the branch push, PR opening and passing CI will not deploy them. The planned CI-only commit includes [skip render] as a documented Render auto-deploy skip marker. Use [skip preview] in the future draft PR title to suppress automatic service/Blueprint previews; do not include [render preview]. These are additional defenses, not verification of unknown integrations or live settings. No Render settings or hooks were changed or invoked.

After that inspection and explicit push/PR authorization:

1. From the restoration worktree, push only the restoration branch without force: `git push origin HEAD:refs/heads/codex/content-pack-complete-image-restore`. Do not push or merge main.
2. Open a draft PR in struk49/SMU_APP with base main and head codex/content-pack-complete-image-restore. Suggested title: `[skip preview] Run isolated Linux startup smoke`. Keep it a draft; it does not need to be marked ready to start the check.
3. In the PR Checks tab, inspect `Restoration Linux startup smoke / linux-smoke`. If repository policy requires Actions approval, have an authorized maintainer approve that run. No default-branch workflow registration is needed.
4. Confirm the summary's application SHA exactly equals the current PR head and the interpreter is 3.14.3. Require successful clean dependency installation and every Linux/HTTP/worker assertion. No run or a skipped/failed check is not Linux acceptance.
5. Preserve the run URL, tested SHA and result. On failure, inspect the failed phase and redacted diagnostics, and report it without provider calls, deployment or automatic regeneration. Do not merge the PR as part of this smoke exercise.

## Execution and isolation

- Ubuntu 24.04 x64; checkout/setup-python actions pinned to verified release commit SHAs, contents:read only, checkout credentials not persisted. No secrets expressions, repository secret values, environment secrets or production service credentials are used. The automatic read-only checkout token is not passed to the application.
- Fresh venv with only the subject's committed requirements installed from public PyPI using isolated pip, no package cache, followed by pip check. No pytest or additional application dependencies are installed for the smoke itself. Dependency download occurs before network isolation.
- Application checks run inside a new Linux network namespace. Only loopback is enabled. Failure to create that namespace is fatal, with no unrestricted fallback. The namespace disappears when its processes exit; host network configuration is unchanged.
- env -i clears inherited credentials/configuration for application processes. Only the explicit interpreter library path, local support paths and smoke flags are retained. The opt-in sitecustomize guard installs dummy configuration before app imports, disables ambient dotenv loading, requires a temporary SQLite path, clears publishing/billing/account credentials and explicitly disables scheduling.
- Python guards reject/log external connections, DNS and UDP sends, and reject/log scheduler starts. A caught prohibited attempt still fails the final audit. Logs record categories, not destinations or credentials. The kernel namespace independently blocks external networking.
- No repository production files are changed. Production app import still creates its normal tables, exclusively in a new disposable SQLite database. Only local fixture users/posts are inserted. No publishing, image generation or provider calls are requested. No browser is launched, so remote template assets are not fetched.

## Assertions

1. Explicit PR head, ancestry from 608391b and Python 3.14.3; successful committed dependency installation and consistency check.
2. Linux with only the loopback interface; fresh SQLite file; app import and authenticated route registration; scheduler stopped with no jobs.
3. Separate import of the unchanged production scheduler_worker module succeeds, its main is callable, and no scheduler/jobs start. The worker main is never invoked.
4. Real Gunicorn starts via `python -m gunicorn --config gunicorn.conf.py ... app:app`; only loopback bind and one worker are supplied for isolation. The production timeout/config file is unchanged.
5. `/landing` serves HTTP 200. Anonymous post/status requests redirect to login. Real local login submits the normal CSRF token and fixture password.
6. Authenticated carousel page renders fixture caption/generation state and polling URL. Status JSON contains only the owner's rows, hides pending URLs, reports processing and sets no-store. A foreign owner's post is rejected even with a shared group ID.
7. Zero recorded prohibited networking/scheduler attempts. Gunicorn is terminated and waited for in a finally block.

A bounded readiness/import/HTTP timeout fails the run; there is no repair or regeneration. Job timeout is 15 minutes. The Actions summary reports the subject, actual interpreter version, completed checks and pass/fail phase. Failures print only a bounded Gunicorn log tail with URLs and dummy credential values redacted. Raw application logs/database files are not uploaded.

## Local review limits and results

Local guard/harness regressions exercise real disposable SQLite, local HTTP/CSRF login and separate worker imports using the installed Windows Python 3.13 environment. They are infrastructure checks and do not establish Linux/Gunicorn compatibility or a clean Linux dependency installation. The actual Linux result remains pending a future authorized workflow run.

Final local validation: **6 harness tests passed** on Windows Python 3.13, including rejection of malformed or mismatched reported application SHAs.

An initial Windows worker-import check hit its bounded timeout; a diagnostic import and the subsequent fixture/HTTP rerun passed. This does not predict Linux timing.

The workflow is linted with checksum-verified official actionlint, Python sources are compiled, and shell blocks receive syntax checks only. The review patch and local verification evidence are saved under acceptance-runs/pr-linux-smoke-preparation and are excluded from the implementation commit.

Preparation scope is exactly five new files: the workflow, scripts/ci/linux_smoke.py, scripts/ci/sitecustomize.py, tests/test_linux_smoke_harness.py and this document. No changes to requirements, runtime configuration, production entrypoints, image behavior, signed intent, credits, publishing or scheduling.

References: [GitHub pull_request events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request), [Render auto-deploy policy and skip markers](https://render.com/docs/deploys), [Render service previews](https://render.com/docs/service-previews), [Render Blueprint previews](https://render.com/docs/preview-environments), [setup-python exact versions](https://github.com/actions/setup-python/blob/main/docs/advanced-usage.md), [checkout v5 release](https://github.com/actions/checkout/releases/tag/v5.0.0), [setup-python v6 release](https://github.com/actions/setup-python/releases/tag/v6.0.0).
