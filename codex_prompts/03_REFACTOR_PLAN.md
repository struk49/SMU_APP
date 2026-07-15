# Codex Prompt — Architecture plan

Publishing tests are passing.

Create a no-code-change architecture plan to:
- split models, extensions, services and routes
- introduce Flask blueprints
- move scheduling to a dedicated worker
- introduce per-user IANA timezones
- replace ad-hoc schema changes with migrations
- preserve existing URLs and behaviour

Break work into small pull requests with rollback points.
