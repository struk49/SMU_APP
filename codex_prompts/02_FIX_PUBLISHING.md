# Codex Prompt — Repair publishing

Read `AGENTS.md`, `docs/CURRENT_BUGS.md`, `docs/PUBLISHING_CONTRACT.md` and the audit report.

Create a new branch named `codex/publishing-repair`.

Repair the publishing pipeline with the smallest safe changes:
1. Remove duplicate helpers that override corrected functions.
2. Preserve existing route URLs.
3. Create one reusable publishing function/service.
4. Use the post owner's `user_id` in background jobs.
5. Resolve enabled platforms and webhook per owner.
6. Send exactly once.
7. Roll back on failure.
8. Add focused tests for manual and scheduled single publishing and carousel regression.

Do not begin blueprint restructuring yet.

Run tests and report changed files, commands, results and risks.
