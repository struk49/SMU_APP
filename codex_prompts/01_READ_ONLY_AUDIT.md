# Codex Prompt — Read-only audit

Work in read-only mode. Do not modify files.

Read `AGENTS.md` and all files in `docs/`.

Audit the complete repository with priority on publishing and scheduling. Produce:
- duplicate definitions with filenames and line numbers
- route-to-service call graphs
- scheduler startup analysis
- single versus carousel webhook analysis
- current-user usage outside request contexts
- database transaction risks
- security risks involving secrets or logs
- failing or missing tests
- smallest safe repair sequence

Do not propose a full rewrite. Finish with a verification checklist.
