# Local startup safety review

Reviewed against restoration HEAD 70b4f91 on 21 October 2026. This focused correction closes the startup safety blocker from the release-readiness audit.

## Change

The local worker previously imported app before checking its database URI and scheduler flag; application import can initialize/patch the database. The PowerShell launcher also silently forced the scheduler off and launched both processes without validating the database target.

Both local entrypoints now use local_startup.py, which imports no application or SMU package and creates no database engine. It validates configuration before the worker imports app and before the launcher starts either application process. The launcher invokes the worker's check-only mode as its preflight; this mode exits before app import, database initialization or worker setup. Worker imports and its existing loop follow a successful preflight.

## Configuration policy

- Require explicit DATABASE_URL and SMU_SCHEDULER_ENABLED values. No implicit database path or scheduler default is accepted by the local entrypoints.
- Support a file-backed, plain sqlite:/// URL without server/authentication components or query options. In-memory databases cannot be shared by the two local processes; alternate drivers/query modes are unsupported by this local launcher.
- Require a recognized false scheduler value: false, 0, no or off, case-insensitive with surrounding whitespace ignored. The launcher no longer sets this value on the user's behalf.
- Discover .env with the same python-dotenv search as app, including normal parent-directory discovery. Resolve interpolation with override=False, so inherited environment values have precedence. Conflicting safety values between the environment and .env fail closed, including a safe inherited setting shadowing an unsafe .env setting. Equivalent recognized false values are compatible.
- Reject missing/blank settings, malformed .env syntax, duplicate safety keys, case variants of safety keys (important for Windows environment behavior), disabled dotenv loading when a .env is discovered, and invalid/unsupported database URLs. Errors contain fixed diagnostic text only; parser exceptions and configuration values are never printed.
- Pin validated safety values in the worker process before application dotenv loading. The launcher's check and both child processes use the same inherited environment and dotenv resolution. The check itself does not modify the parent shell or persistent configuration.

A valid local configuration supplies an intentionally selected SQLite file and an explicit false scheduler flag, for example DATABASE_URL=sqlite:///local-content-pack.db and SMU_SCHEDULER_ENABLED=false. Align any inherited safety values with the local .env rather than relying on an override to hide a conflicting file value. Production's existing scheduler-enabled configuration remains valid for its unchanged production entrypoints.

## Verification

Final mocked suites: **196 passed**, including **45 isolated subprocess regressions**, with no skips. Suites: test_local_startup.py, test_local_carousel_worker.py, test_scheduler_service.py, test_scheduler_lease.py, test_app_factory.py and test_carousel_generation_service.py.

Subprocess checks execute the actual local worker entrypoint and PowerShell launcher in disposable directories. An app import marker and disposable SQLite initialization detect premature imports/database work; a PowerShell Start-Process trap detects attempted worker launch. Rejected PostgreSQL settings from inherited environment and .env leave all markers absent. Conflicts in both directions, missing flags, unsupported values, malformed URLs/files, duplicate/case-aliased keys and dotenv-disabled configuration are covered. Valid environment-only, dotenv-only, matching settings and inherited-variable interpolation reach fake worker/web startup and create only a disposable SQLite database. Check-only mode never imports the fake app even when configuration is valid. Captured error output is checked for credential/connection-string leakage.

The final suite used available PowerShell 7. Earlier Windows PowerShell runs exercised the launcher matrix; one run had a bounded subprocess timeout, addressed with a 60-second test timeout. The final run had no timeouts. The fake web application reproduces production dotenv loading. Production publishing, image providers and the application database were never used by these subprocesses. The parent integration suite also blocks network connections, disables ambient dotenv loading and uses dummy credentials/disposable SQLite.

Python compile checks for both entrypoint/helper modules and the regression file passed. The launcher parses under Windows PowerShell. Git diff whitespace checks passed. Test XML and preservation hashes are retained in local acceptance artifacts and excluded from the commit.

## Scope and release readiness

Only local_carousel_worker.py, scripts/start_local.ps1, new local_startup.py, tests/test_local_startup.py and this review document are included. app.py, config.py, Procfile, gunicorn.conf.py, scheduler_worker.py, production scheduler/publishing, text generation, signed intent, credit handling and image behavior are unchanged.

The startup safety blocker is resolved. The earlier release review's independent conditions remain: perform an isolated Linux Gunicorn smoke check and coordinate worker/web versions because old workers cannot read new provider-complete payloads. Drain new-format queued work before rollback to old workers. The previously documented baseline landing-page assertion is outside this correction. No deployment readiness claim is based on production access.

Original main's HEAD, status and all 57 recorded dirty-file hashes are unchanged. Acceptance artifacts are preserved and excluded. No provider calls, production access, merge, push or deployment occurred.
