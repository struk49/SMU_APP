# Architecture

## Current
Most configuration, models, helpers, jobs and routes are inside `app.py`.

## Target
```text
smu_app/
├── app.py
├── config.py
├── extensions.py
├── models.py
├── routes/
│   ├── auth.py
│   ├── posts.py
│   ├── studio.py
│   ├── publishing.py
│   ├── scheduling.py
│   ├── settings.py
│   └── content.py
├── services/
│   ├── publishing_service.py
│   ├── scheduler_service.py
│   ├── ai_service.py
│   ├── brand_service.py
│   ├── grading_service.py
│   └── media_service.py
├── helpers/
│   ├── timezone_helpers.py
│   ├── payload_helpers.py
│   └── parsing_helpers.py
├── templates/
│   └── components/
├── static/
├── tests/
└── docs/
```

## Refactor sequence
1. Create a passing smoke-test baseline.
2. Remove duplicate function definitions.
3. Extract publishing logic without changing route URLs.
4. Make scheduler call the same publishing service.
5. Add per-user timezone.
6. Move routes into blueprints.
7. Introduce migrations and remove ad-hoc schema alteration when safe.
