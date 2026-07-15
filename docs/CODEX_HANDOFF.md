# Codex Handoff

## First task
Do not edit code immediately.

Inspect the repository and report:
1. Duplicate top-level function and route definitions.
2. Every call path from manual Send to Make to `requests.post`.
3. Every call path from APScheduler to `requests.post`.
4. Whether any later definition overrides a helper accepting `user_id`.
5. Whether the scheduler can start more than once.
6. Whether scheduled jobs rely on `current_user`.
7. Whether single and carousel webhook selection is consistent.
8. Whether exceptions always roll back the SQLAlchemy session.
9. Exact reproduction steps for the current publishing failure.
10. The smallest safe repair plan.

## Implementation requirement
After the report is approved:
- Create a branch.
- Make the smallest repair first.
- Add tests.
- Do not begin the broader blueprint refactor until publishing tests pass.
