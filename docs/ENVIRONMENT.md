# Environment

## Overview

SMU is a Flask application backed by SQLAlchemy. Local development uses SQLite
by default, with `DATABASE_URL` support for hosted databases. The app integrates
with OpenAI, Cloudinary, Make.com, TikTok/yt-dlp and APScheduler.

Not every integration is required for every local task. The app can start with
development fallbacks, but specific features require their own credentials.

## Local Requirements

Install locally:

- Python with `venv` support
- Git
- SQLite support through Python
- a browser for manual testing
- pip dependencies from `requirements.txt`

No Node/npm setup is required by the current repository.

The repository does not currently pin a Python version in a runtime file. Use a
modern Python version compatible with the locked dependencies in
`requirements.txt`.

## Local Setup

Windows PowerShell workflow:

```powershell
cd C:\Users\andre\smu_app
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Create a local `.env` file in the project root and set only the values needed
for the features you are testing.

Run the automated suite before major refactors:

```powershell
python -m pytest tests
```

## Running The App

Run the local development server with:

```powershell
python app.py
```

`app.py` remains the compatibility and startup entry point. On startup it:

- creates the app through `smu_core.create_app()`
- loads configuration
- configures external clients
- initializes database tables
- runs compatibility patching for legacy `post` columns
- registers app-level error handlers
- registers and starts APScheduler jobs

The development server runs with `debug=True` and `use_reloader=False`.

Do not use Flask's development server as the production web server.

## Running Tests

Run the full regression suite:

```powershell
python -m pytest tests
```

Run with default warnings enabled:

```powershell
python -m pytest tests -W default
```

Unit and regression tests mock external services. They should not call real
OpenAI, Cloudinary, Make.com, TikTok or arbitrary HTTP APIs.

See `TEST_PLAN.md` for the testing strategy.

## Environment Variables

The current code reads these environment variables.

| Variable | Required | Purpose | Example format | Default / fallback | Sensitive |
| --- | --- | --- | --- | --- | --- |
| `SECRET_KEY` | Required for production | Flask session signing secret | `a-long-random-secret` | `dev-secret-key` | Yes |
| `DATABASE_URL` | Optional locally, required for hosted DB | SQLAlchemy database URL | `postgresql://user:pass@host/db` | `sqlite:///posts.db` | Yes |
| `SMU_ADMIN_EMAILS` | Optional | Comma-separated admin allowlist for beta admin views | `admin@example.com,owner@example.com` | empty set | No, but avoid exposing private emails unnecessarily |
| `OPENAI_API_KEY` | Required for OpenAI features | OpenAI API access | `sk-...` | empty string | Yes |
| `CLOUDINARY_CLOUD_NAME` | Required for Cloudinary upload features | Cloudinary cloud name | `my-cloud` | unset | No |
| `CLOUDINARY_API_KEY` | Required for Cloudinary upload features | Cloudinary API key | `1234567890` | unset | Yes |
| `CLOUDINARY_API_SECRET` | Required for Cloudinary upload features | Cloudinary API secret | `...` | unset | Yes |
| `MAKE_WEBHOOK_SINGLE` | Optional fallback | Global Make webhook for single-post publishing | `https://hook...` | empty string | Yes |
| `MAKE_WEBHOOK_CAROUSEL` | Optional fallback | Global Make webhook for carousel publishing | `https://hook...` | empty string | Yes |
| `LINKEDIN_CLIENT_ID` | Required for LinkedIn OAuth | LinkedIn Developer app client ID | `86abc...` | empty string | No |
| `LINKEDIN_CLIENT_SECRET` | Required for LinkedIn OAuth | LinkedIn Developer app client secret | `...` | empty string | Yes |
| `LINKEDIN_REDIRECT_URI` | Optional if external URL generation is correct | Absolute LinkedIn OAuth callback URL registered in LinkedIn Developer Portal | `https://smu.example.com/accounts/linkedin/callback` | generated from `url_for(..., _external=True)` | No |

Do not put real values in documentation or commits.

## OpenAI Configuration

OpenAI uses:

```dotenv
OPENAI_API_KEY=
```

`app.py` creates the OpenAI client and passes it into service-backed wrappers.

Features that depend on OpenAI include:

- caption helpers
- caption grading
- Brand Coach flows
- Content Pack generation
- TikTok repurposing
- OpenAI image generation

If `OPENAI_API_KEY` is missing, OpenAI-dependent features may raise a clear
runtime error when used.

## Cloudinary Configuration

Cloudinary uses:

```dotenv
CLOUDINARY_CLOUD_NAME=
CLOUDINARY_API_KEY=
CLOUDINARY_API_SECRET=
```

`app.py` configures Cloudinary at startup. Media upload behavior is implemented
through wrappers backed by `smu_core.services.media`.

Cloudinary-backed features include:

- uploaded post media
- generated image storage
- JPEG-normalized Instagram-safe media

The media workflow includes JPEG normalization, transparent image flattening and
Cloudinary upload where required.

## Make.com Configuration

Make.com can be configured globally and per user.

Global fallback variables:

```dotenv
MAKE_WEBHOOK_SINGLE=
MAKE_WEBHOOK_CAROUSEL=
```

Per-user webhook URLs are stored in Connected Accounts.

Resolution behavior:

- single posts use the user's single webhook first, then
  `MAKE_WEBHOOK_SINGLE`
- carousel posts use the user's carousel webhook first, then
  `MAKE_WEBHOOK_CAROUSEL`
- there is no cross-type fallback between single and carousel webhooks

If no webhook is available for the post type, publishing fails before contacting
Make.

## LinkedIn OAuth Configuration

LinkedIn OAuth uses:

```dotenv
LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=
LINKEDIN_REDIRECT_URI=
```

`LINKEDIN_REDIRECT_URI` may be omitted when Flask can generate the correct
external callback URL for:

```text
/accounts/linkedin/callback
```

The redirect URL must also be registered in the LinkedIn Developer Portal.
SMU requests only `openid profile w_member_social` for MVP personal-profile
publishing. Tokens are stored per user in Connected Accounts and are not used
for live publishing until the LinkedIn publishing slice is explicitly wired.

## Database Configuration

`config.py` owns database configuration.

Local default:

```text
sqlite:///posts.db
```

If `DATABASE_URL` is set, it is used instead. URLs beginning with
`postgres://` are normalized to `postgresql://` for SQLAlchemy compatibility.

SQLAlchemy settings:

- `SQLALCHEMY_TRACK_MODIFICATIONS = False`
- engine options include `pool_pre_ping`, `pool_recycle`, `pool_size` and
  `max_overflow`

Database startup behavior remains in `app.py`:

- `db.create_all()` runs inside an app context
- legacy `post` columns are patched with `ALTER TABLE` if missing

The repository does not currently use Alembic or Flask-Migrate.

## Admin Configuration

Admin access uses:

```dotenv
SMU_ADMIN_EMAILS=
```

Format:

```dotenv
SMU_ADMIN_EMAILS=admin@example.com,owner@example.com
```

The value is parsed into a lowercase set of email addresses. Empty or missing
values become an empty set, so no email is granted admin access through this
setting.

## Scheduler Runtime

APScheduler is created and started in `app.py`, not in `create_app()`.

Current job IDs:

- `generate_pending_images`
- `check_scheduled_posts`

Current scheduler behavior:

- runs with timezone `UTC`
- generates pending carousel images on an interval
- checks scheduled posts on an interval
- delegates scheduled-post processing to `smu_core.services.scheduler`

`create_app()` does not start the scheduler. This avoids starting long-running
background jobs in factory-only tests.

Local debug uses `use_reloader=False` to avoid duplicate scheduler startup from
the Flask reloader.

Tests intentionally avoid relying on live scheduler threads.

## External Services

| Service | Required for startup | Required for |
| --- | --- | --- |
| OpenAI | No | captions, grading, Brand Coach, Content Pack, TikTok repurposing, image generation |
| Cloudinary | No | media upload and generated image storage |
| Make.com | No | manual and scheduled publishing |
| TikTok / yt-dlp | No | TikTok transcript extraction and repurposing |

The app may start without all integrations configured, but feature-specific
actions can fail if their required credentials are missing or invalid.

## Security / Secret Handling

Never commit:

- `.env`
- API keys
- Cloudinary secrets
- Make webhook URLs
- database credentials
- Flask session secrets

`.gitignore` excludes `.env`, `.env.*`, virtual environments, local databases,
logs, uploads and common cache files.

Webhook URLs and secrets should not be logged. Diagnostics should report safe
facts such as whether a webhook is configured, not the webhook value.

Rotate any credential that appears in logs, screenshots, commits or chat.

## Production Notes

Known production/runtime considerations:

- use a production WSGI server such as Gunicorn rather than Flask's development
  server
- set environment variables in the hosting provider, not in committed files
- ensure the database uses persistent storage
- confirm whether local SQLite is appropriate before beta or production use
- APScheduler runs inside the web process today, so multi-worker deployments can
  start duplicate schedulers unless deployment is constrained carefully
- Make, Cloudinary and OpenAI credentials should be scoped and rotated as needed

The requirements include `gunicorn` and `psycopg2-binary`, which support hosted
deployment patterns, but deployment guarantees depend on the hosting
configuration.

## Troubleshooting

Common checks:

- If the app starts with the wrong database, inspect `DATABASE_URL` and the
  printed SQLAlchemy URI prefix.
- If local SQLite data is missing, confirm which `posts.db` file the app is
  using.
- If OpenAI features fail, confirm `OPENAI_API_KEY` is set.
- If uploads fail, confirm all three Cloudinary values are set and valid.
- If publishing fails before Make, check Connected Accounts and webhook
  configuration.
- If no scheduled posts publish, confirm rows have `status="scheduled"` and a
  due UTC `scheduled_time`.
- If the scheduler appears duplicated locally, confirm the app is running with
  `use_reloader=False`.
- If the virtual environment will not run on Windows, check the Python launcher
  and venv interpreter path.

## Related Documentation

- `ARCHITECTURE.md`
- `PUBLISHING_CONTRACT.md`
- `TEST_PLAN.md`
- `ROADMAP.md`
