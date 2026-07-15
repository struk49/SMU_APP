# Preparing SMU for Codex

1. Copy this pack into the root of the SMU repository.
2. Put `AGENTS.md` at the repository root.
3. Keep the documentation in `docs/`.
4. Review `.gitignore.additions.txt` and merge relevant entries into `.gitignore`.
5. Confirm `.env` is not tracked:
   `git ls-files .env`
6. Commit the current state.
7. Create a separate Codex branch.
8. Give Codex `codex_prompts/01_READ_ONLY_AUDIT.md` first.
9. Review the audit before allowing edits.
10. Use `codex_prompts/02_FIX_PUBLISHING.md` only after approving the audit.

Do not ask Codex to rewrite all of `app.py` at once.
