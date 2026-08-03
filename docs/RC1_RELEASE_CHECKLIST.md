# SMU Release Candidate 1 Checklist

## Environment Checklist

- `SECRET_KEY` is set to a strong production value.
- `DATABASE_URL` points at the production database.
- `SMU_ADMIN_EMAILS` contains the owner/admin email addresses allowed to view `/admin/beta`.
- `OPENAI_API_KEY` is configured where AI generation is enabled.
- `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY` and `CLOUDINARY_API_SECRET` are configured.
- `MAKE_WEBHOOK_SINGLE` and `MAKE_WEBHOOK_CAROUSEL` are configured only where shared fallbacks are required.
- Per-user Make webhook URLs are configured in Connected Accounts for beta users.
- Logs are writable under `logs/` and are not publicly served.

## Database Backup

For SQLite beta deployments:

1. Stop the app or put it into maintenance mode.
2. Copy `posts.db` to a timestamped backup location.
3. Restart the app and verify login, dashboard and calendar access.

For PostgreSQL deployments:

1. Use the hosting provider backup tool or `pg_dump`.
2. Store the backup outside the application filesystem.
3. Verify the backup can be listed and downloaded by the owner.

## Database Restore

For SQLite:

1. Stop the app.
2. Move the current `posts.db` aside.
3. Copy the selected backup into place as `posts.db`.
4. Start the app and verify a known beta user can sign in.

For PostgreSQL:

1. Put the app into maintenance mode.
2. Restore into a clean database or provider restore point.
3. Point `DATABASE_URL` at the restored database if needed.
4. Restart the app and verify dashboard, calendar and connected account data.

## Deploy

1. Confirm tests pass locally.
2. Confirm `git diff --check` has no new whitespace issues.
3. Set production environment variables.
4. Deploy from the release branch or tagged commit.
5. Visit `/landing`, `/privacy`, `/terms`, `/contact`, `/login` and `/`.
6. Sign in as an admin listed in `SMU_ADMIN_EMAILS` and verify `/admin/beta`.

## Rollback

1. Put the app into maintenance mode if users are actively testing.
2. Redeploy the previous known-good commit.
3. Restore the database only if the release introduced unusable data.
4. Verify login, dashboard, calendar and publishing smoke tests.
5. Remove maintenance routing after the rollback is confirmed.

## Private Beta Launch

1. Confirm Privacy, Terms and Contact pages are reachable from the footer.
2. Submit a test beta application and verify it appears in `/admin/beta`.
3. Submit authenticated feedback and verify it appears in `/admin/beta`.
4. Create one test scheduled post and confirm it appears on the calendar.
5. Invite a small initial cohort and record any issues before adding more users.

## Maintenance Mechanism

The app includes a public `/maintenance` page that returns HTTP 503. For planned maintenance, route traffic to this page at the hosting or reverse-proxy layer, then remove that routing after verification.
