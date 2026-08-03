# SMU Model Dependency Plan

This is an audit-and-planning document only. It does not implement the remaining
model moves.

## 1. Verified Current Model Graph

Current model locations:

- `app.py`: `User`, `Post`
- `smu_core/models/feedback.py`: `Feedback`
- `smu_core/models/contact_message.py`: `ContactMessage`
- `smu_core/models/beta_application.py`: `BetaApplication`
- `smu_core/models/brand_brief.py`: `BrandBrief`
- `smu_core/models/connected_account.py`: `ConnectedAccount`
- `smu_core/models/post_revision.py`: `PostRevision`

Current import and registration order:

1. `app.py` creates the Flask app.
2. `app.py` imports shared `db` and `login_manager` from `smu_core.extensions`.
3. `app.py` imports moved models before defining `User` and `Post`.
4. `app.py` defines `User`.
5. `app.py` defines `Post`.
6. `login_manager.user_loader` is registered.
7. `db.create_all()` runs inside `with app.app_context():`.
8. Existing `ALTER TABLE post ...` compatibility patching runs.

Dependency graph by category:

| Source | Target | Dependency type | Current declaration |
| --- | --- | --- | --- |
| `User` | `BrandBrief` | SQLAlchemy relationship/backref | `db.relationship("BrandBrief", backref="user", uselist=False, lazy=True)` |
| `User` | `ConnectedAccount` | SQLAlchemy relationship/backref | `db.relationship("ConnectedAccount", backref="user", uselist=False, lazy=True)` |
| `User` | `Post` | SQLAlchemy relationship/backref | `db.relationship("Post", backref="user", lazy=True)` |
| `BrandBrief` | `User` | Foreign key | `user_id -> user.id`, unique |
| `ConnectedAccount` | `User` | Foreign key | `user_id -> user.id`, unique |
| `Feedback` | `User` | Foreign key | `user_id -> user.id`, nullable |
| `Post` | `User` | Foreign key | `user_id -> user.id`, nullable |
| `Post` | `PostRevision` | SQLAlchemy relationship/backref/cascade | `db.relationship("PostRevision", backref="post", lazy=True, cascade="all, delete-orphan")` |
| `PostRevision` | `Post` | Foreign key and backref target | `post_id -> post.id` |
| `PostRevision` | `User` | Foreign key | `user_id -> user.id` |
| Flask-Login | `User` | Authentication identity | `load_user(user_id)` returns `User.query.get(int(user_id))` |
| Routes/helpers | `User`, `Post` | Runtime query/mutation dependencies | Mostly through `current_user.id`, `User.query`, `Post.query`, and `Post(...)` |
| Tests | `User`, `Post` | Fixture dependencies | `tests/conftest.py` creates `module.User` and `module.Post` |
| Compatibility | `User`, `Post` | Public module exports | Existing callers expect `import app as smu_app; smu_app.User; smu_app.Post` |

## 2. User Dependency Inventory

Exact current class definition:

```python
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    brand_brief = db.relationship(
        "BrandBrief",
        backref="user",
        uselist=False,
        lazy=True
    )
    connected_account = db.relationship(
    "ConnectedAccount",
    backref="user",
    uselist=False,
    lazy=True
)

    posts = db.relationship("Post", backref="user", lazy=True)
```

Table name:

- Implicit SQLAlchemy table name: `user`

Columns in order:

1. `id`
2. `email`
3. `password_hash`
4. `created_at`

Column details:

| Column | Type | Default | Nullable | Constraints |
| --- | --- | --- | --- | --- |
| `id` | `Integer` | none | primary key | primary key |
| `email` | `String(150)` | none | `False` | unique |
| `password_hash` | `String(255)` | none | `False` | none |
| `created_at` | `DateTime` | `datetime.utcnow` callable | default nullable | none |

Password and authentication methods:

- No password methods are declared on the model.
- Registration uses `generate_password_hash(password)` before constructing `User`.
- Login uses `check_password_hash(user.password_hash, password)`.
- `User` inherits `UserMixin`, providing Flask-Login methods/properties such as
  `is_authenticated`, `is_active`, `is_anonymous`, and `get_id()`.

Flask-Login requirements:

- `User` must continue to inherit `UserMixin`.
- `login_manager.user_loader` must keep resolving IDs through the same `User`
  model class:

```python
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
```

Relationships declared directly on `User`:

- `brand_brief`: one-to-one relationship to `"BrandBrief"` with `backref="user"`.
- `connected_account`: one-to-one relationship to `"ConnectedAccount"` with
  `backref="user"`.
- `posts`: one-to-many relationship to `"Post"` with `backref="user"`.

Relationships or backrefs elsewhere targeting `User`:

- `BrandBrief.user` backref, created by `User.brand_brief`.
- `ConnectedAccount.user` backref, created by `User.connected_account`.
- `Post.user` backref, created by `User.posts`.
- `PostRevision` has `user_id`, but no relationship/backref to `User`.
- `Feedback` has `user_id`, but no relationship/backref to `User`.

Foreign keys targeting `user.id`:

- `BrandBrief.user_id`: `ForeignKey("user.id")`, non-null, unique.
- `ConnectedAccount.user_id`: `ForeignKey("user.id")`, non-null, unique.
- `Feedback.user_id`: `ForeignKey("user.id")`, nullable.
- `Post.user_id`: `ForeignKey("user.id")`, nullable.
- `PostRevision.user_id`: `ForeignKey("user.id")`, non-null.

Routes, helpers, scheduler jobs, and services that query or construct `User`:

- `load_user(user_id)`: `User.query.get(int(user_id))`.
- `/register`: constructs `User(email=..., password_hash=...)`.
- `/login`: `User.query.filter_by(email=email).first()`.
- Many authenticated routes depend on `current_user.id` or `current_user.email`;
  this is an indirect `User` dependency through Flask-Login.
- `is_current_user_admin()` uses `current_user.email`.
- `build_brand_context(user_id)` uses user ownership indirectly through
  `BrandBrief`.
- Scheduler publishing uses `post.user_id`, not `current_user`, but still depends
  on valid `User` ownership semantics.

Test fixtures or helpers constructing `User`:

- `tests/conftest.py:create_user(module, email="owner@example.com")` constructs
  `module.User(email=..., password_hash="unused")`.

Tests or application imports expecting `app.User`:

- `tests/conftest.py` imports `app as smu_app` and uses `module.User`.
- Any test using `create_user()` depends on `app.User` compatibility.
- Login/session tests and route tests indirectly depend on `app.User` through
  the shared fixture.

Model registration order considerations:

- `User` is currently defined before `Post`.
- Moved models are imported before `User`.
- `db.create_all()` runs after both `User` and `Post` are registered.
- If `User` moves, `app.py` must import `User` before defining `Post`, because
  `Post.user_id` targets `user.id` and the existing `User.posts` relationship
  currently references `"Post"`.

Circular-import risks if moved to `smu_core/models/user.py`:

- `user.py` must not import `app.py`.
- `user.py` should import only `datetime`, `UserMixin`, and `db`.
- Relationships should remain string-based: `"BrandBrief"`,
  `"ConnectedAccount"`, and `"Post"`.
- `user.py` must not import `Post`, `BrandBrief`, or `ConnectedAccount`; those
  imports would create order coupling and raise circular import risk.
- `login_manager.user_loader` should remain in `app.py` until an application
  factory phase, importing the moved `User` symbol from `smu_core.models.user`.

## 3. Post Dependency Inventory

Exact current class definition:

```python
class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    file_url = db.Column(db.String(500), nullable=False)
    file_type = db.Column(db.String(20), nullable=False)
    prompt = db.Column(db.Text)
    caption = db.Column(db.Text)
    status = db.Column(db.String(50), default="draft")
    created_at = db.Column(db.DateTime, default=datetime.utcnow())
    sent_at = db.Column(db.DateTime)
    scheduled_time = db.Column(db.DateTime, nullable=True)
    group_id = db.Column(db.String(100), nullable=True)
    post_type = db.Column(db.String(50), default="single")
    platforms = db.Column(db.String(200), default="instagram,facebook")
    sort_order = db.Column(db.Integer, default=0)
    is_cover = db.Column(db.Boolean, default=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    grade_result = db.Column(db.Text, nullable=True)
    grade_score = db.Column(db.Float, nullable=True)
    graded_at = db.Column(db.DateTime, nullable=True)
    # AI Improved Version
    improved_caption = db.Column(db.Text, nullable=True)
    improved_at = db.Column(db.DateTime, nullable=True)
    # Brand Coach
    brand_score = db.Column(db.Float, nullable=True)
    brand_feedback = db.Column(db.Text, nullable=True)

    revisions = db.relationship(
    "PostRevision",
    backref="post",
    lazy=True,
    cascade="all, delete-orphan"
)
```

Table name:

- Implicit SQLAlchemy table name: `post`

Columns in order:

1. `id`
2. `file_url`
3. `file_type`
4. `prompt`
5. `caption`
6. `status`
7. `created_at`
8. `sent_at`
9. `scheduled_time`
10. `group_id`
11. `post_type`
12. `platforms`
13. `sort_order`
14. `is_cover`
15. `user_id`
16. `grade_result`
17. `grade_score`
18. `graded_at`
19. `improved_caption`
20. `improved_at`
21. `brand_score`
22. `brand_feedback`

Column details:

| Column | Type | Default | Nullable | Notes |
| --- | --- | --- | --- | --- |
| `id` | `Integer` | none | primary key | primary key |
| `file_url` | `String(500)` | none | `False` | publishing media URL |
| `file_type` | `String(20)` | none | `False` | usually `image` or `video` |
| `prompt` | `Text` | none | default nullable | generation prompt |
| `caption` | `Text` | none | default nullable | publishing and Studio caption |
| `status` | `String(50)` | `"draft"` | default nullable | draft/scheduled/publishing/sent/failed states |
| `created_at` | `DateTime` | `datetime.utcnow()` evaluated at import | default nullable | existing behavior should not change during move |
| `sent_at` | `DateTime` | none | default nullable | set after publishing |
| `scheduled_time` | `DateTime` | none | `True` | stored UTC by scheduling helpers |
| `group_id` | `String(100)` | none | `True` | carousel grouping |
| `post_type` | `String(50)` | `"single"` | default nullable | single/carousel |
| `platforms` | `String(200)` | `"instagram,facebook"` | default nullable | comma-separated selected platforms |
| `sort_order` | `Integer` | `0` | default nullable | carousel ordering |
| `is_cover` | `Boolean` | `False` | default nullable | carousel cover marker |
| `user_id` | `Integer` | none | `True` | `ForeignKey("user.id")` |
| `grade_result` | `Text` | none | `True` | AI grading output |
| `grade_score` | `Float` | none | `True` | AI grading score |
| `graded_at` | `DateTime` | none | `True` | AI grading timestamp |
| `improved_caption` | `Text` | none | `True` | AI improved caption |
| `improved_at` | `DateTime` | none | `True` | improved timestamp |
| `brand_score` | `Float` | none | `True` | Brand Coach score |
| `brand_feedback` | `Text` | none | `True` | Brand Coach feedback JSON |

Foreign keys:

- `Post.user_id -> user.id`, nullable.

Relationships and backrefs:

- `Post.revisions -> "PostRevision"` with `backref="post"`.
- `Post.user` backref is created by `User.posts`.

Cascade behaviour:

- `Post.revisions` uses `cascade="all, delete-orphan"`.
- Deleting a `Post` deletes associated `PostRevision` rows through the ORM
  relationship.

Studio, grading, and revision fields:

- `grade_result`
- `grade_score`
- `graded_at`
- `improved_caption`
- `improved_at`
- `brand_score`
- `brand_feedback`
- `caption`
- `prompt`
- `Post.revisions`

Scheduling fields and datetime semantics:

- `scheduled_time` is nullable and is stored as UTC by
  `convert_uk_time_to_utc`.
- Scheduler compares `Post.scheduled_time <= datetime.utcnow()`.
- Calendar converts UTC to UK local time for display.
- `status="scheduled"` is used by scheduler due queries.

Publishing, platform, and status fields:

- `file_url`, `file_type`, `caption`, `prompt`, `platforms`, `post_type`,
  `status`, and `sent_at`.
- Duplicate-send protection blocks some manual publishes based on status
  values such as `publishing` and `sent_to_make`.
- Publishing helpers set `status="sent_to_make"` and `sent_at=datetime.utcnow()`.

Carousel/grouping fields:

- `group_id`
- `post_type`
- `sort_order`
- `is_cover`
- `caption`, `platforms`, `scheduled_time`, `status`, and `user_id` shared
  across group members by route/helper conventions.

Routes that create, read, update, or delete `Post`:

- `/`: lists and filters posts for the current user.
- `/create`: creates single posts and carousel posts.
- `/post/<post_id>`: reads a user-owned post and related carousel posts.
- `/edit-post/<post_id>`: reads/updates one post.
- `/rewrite-caption/<post_id>`: reads/mutates caption fields.
- `/rewrite-carousel-caption/<group_id>`: reads/mutates grouped posts.
- `/duplicate-post/<post_id>`: creates a draft copy.
- `/duplicate-carousel/<group_id>`: creates grouped draft copies.
- `/send/<post_id>`: manual single publish.
- `/send-carousel/<group_id>`: manual carousel publish.
- `/post/<post_id>/studio/action/<action>`: Studio action over one post.
- `/edit-carousel/<group_id>`: reads/updates grouped posts.
- `/schedule/<post_id>`: schedules one post or a carousel group.
- `/delete/<post_id>`: deletes one post.
- `/delete-carousel/<group_id>`: deletes grouped posts.
- `/tiktok/create-draft`: creates a post.
- `/tiktok/create-carousel-draft`: creates grouped posts.
- `/content-pack/create-carousel`: creates grouped posts.
- `/content-pack/create-platform-draft`: creates a post.
- `/calendar/events`: reads/serializes posts.
- `/calendar/summary`: reads/aggregates posts.
- `/calendar/events/<post_id>/reschedule`: updates scheduled time.
- `/calendar/events/<post_id>/duplicate`: duplicates as draft.
- `/post/<post_id>/improve`: updates AI fields.
- `/post/<post_id>/use-improved`: updates caption and revisions.
- `/post/<post_id>/custom-caption`: updates caption and revisions.
- `/post/<post_id>/discard-improved`: clears improved fields.
- `/post/<post_id>/ai-editor`: updates caption and revisions.
- `/post/<post_id>/studio`: reads revision history and saves captions.
- `/post/<post_id>/studio/regrade`: updates grading fields.
- `/post/<post_id>/revision/<revision_id>/restore`: restores caption from
  selected revision.

Publishing helpers that read or mutate `Post`:

- `build_single_payload(post)`
- `get_ordered_carousel_posts(group_id, user_id=None)`
- `build_carousel_payload(group_id, user_id=None)`
- `publish_post_to_make(post, user_id)`
- `send_to_make(post_id)`
- `send_carousel_to_make(group_id)`
- `parse_platforms(platforms_string)` reads post platform strings indirectly.
- `log_single_image_diagnostics(post, enabled_platforms)`
- `log_scheduled_post_diagnostics(post, input_local_time=None)`

Scheduler jobs that query or update `Post`:

- `check_scheduled_posts()`
  - queries rows with `scheduled_time is not None`, `status == "scheduled"`,
    and `scheduled_time <= now_utc`
  - deduplicates carousel groups with `processed_groups`
  - calls `publish_post_to_make(post, post.user_id)`
  - sets failed statuses on errors
- startup repair logic queries `Post.status == "generating"` and
  `Post.file_type == "image"` and marks stale rows as draft/failed as currently
  implemented.

Calendar helpers that query or serialize `Post`:

- `calendar_post_title(post)`
- `calendar_posts_for_range(start_utc, end_utc)`
- `filter_calendar_posts(posts, platform_filter=None, status_filter=None)`
- `build_calendar_summary(posts)`
- `reschedule_calendar_post(post, new_date)`
- `duplicate_calendar_post_as_draft(post)`
- `build_calendar_event(post)`
- `/calendar/events`
- `/calendar/summary`
- `/calendar/events/<post_id>/reschedule`
- `/calendar/events/<post_id>/duplicate`

AI Studio helpers that read or mutate `Post`:

- `evaluate_brand_match(post, brand_context="")`
- `update_brand_coach(post, brand_context="")`
- `rewrite_caption_with_action(post, brand_context="", action="improve")`
- `grade_post_with_ai(post, brand_context="")`
- `improve_post_with_ai(post, brand_context="")`
- `save_post_revision(post, source="manual")`
- Studio routes listed above.

Test fixtures or helpers that create `Post`:

- `tests/conftest.py:create_post(...)` constructs `module.Post(...)`.
- `tests/conftest.py:create_carousel(...)` creates two grouped posts through
  `create_post`.

Tests or imports expecting `app.Post`:

- Any test using `create_post` or `create_carousel`.
- Publishing tests directly query `module.Post`.
- Calendar tests create and assert against posts.
- Studio UI/model-move tests use `module.Post`.

Database patching targeting `post`:

The existing patch block calls `inspector.get_columns("post")` and conditionally
adds:

- `group_id VARCHAR(100)`
- `post_type VARCHAR(50) DEFAULT 'single'`
- `grade_result TEXT`
- `grade_score FLOAT`
- `graded_at TIMESTAMP`
- `improved_caption TEXT`
- `improved_at TIMESTAMP`
- `brand_score FLOAT`
- `brand_feedback TEXT`

This block must remain after `Post` is imported/registered and must not be
rewritten during a model-only move.

Circular-import risks if moved to `smu_core/models/post.py`:

- `post.py` must not import `app.py`.
- `post.py` should import only `datetime` and `db`.
- `user_id` should remain `db.ForeignKey("user.id")`.
- `revisions` should remain `db.relationship("PostRevision", ...)`.
- `post.py` should not import `User` or `PostRevision`; string declarations are
  enough and avoid circular imports.
- `app.py` must import both moved `User` and moved `Post` before
  `db.create_all()` and before any route/helper uses them.

## 4. Import and Circular-Dependency Risks

Primary risks:

- `User` currently owns the `posts` relationship to `"Post"`, while `Post`
  owns the `user_id` foreign key to `"user.id"`. Moving either model requires
  preserving string-based declarations.
- `Post` owns the `revisions` relationship to `"PostRevision"`, while
  `PostRevision` is now in `smu_core.models`. The relationship should remain
  string-based.
- `login_manager.user_loader` must continue to see the same `User` symbol
  exported by `app.py`.
- `db.create_all()` must run only after all model classes are imported/defined.
- `smu_core.models.__init__` must avoid import cycles. If `User` or `Post`
  modules remain self-contained, importing all models from `__init__.py` should
  remain safe.

Name-collision scan:

- No module named `user.py` or `post.py` currently exists in `smu_core.models`;
  creating those files is safe if imports are explicit.
- Route/helper parameters named `post` are common:
  `log_scheduled_post_diagnostics`, `log_single_image_diagnostics`,
  `evaluate_brand_match`, `update_brand_coach`, `build_single_payload`,
  `rewrite_caption_with_action`, `grade_post_with_ai`, `save_post_revision`,
  `publish_post_to_make`, calendar helpers, and Studio helpers.
- Route/helper parameters named `user_id` are common:
  `load_user`, `build_brand_context`, `get_user_connected_accounts`,
  `get_enabled_platforms_for_user`, `get_user_make_webhook`,
  `publish_post_to_make`.
- Local variables named `post` are used in most post routes. This is normal, but
  the previous Brand Coach bug shows why tests should assert route helpers
  receive the model instance rather than imported functions.
- Local variables named `user` appear in `/register`, `/login`, and test
  fixtures. Moving the model does not conflict if import names stay explicit:
  `from smu_core.models.user import User`.

## 5. Test Protection Matrix

| Critical behaviour | Existing protection | Missing or recommended before moving |
| --- | --- | --- |
| Registration | release/beta tests and route tests indirectly; `/register` route uses `User` | Add explicit `User` model move compatibility test for registration creates `app.User` |
| Login/logout | existing auth flow tests if present; many login-required tests depend on `login()` fixture | Add explicit `load_user` smoke test around persisted user ID |
| Flask-Login user loading | indirect through authenticated client routes | Add direct `load_user(str(user.id)) is user` regression |
| User ownership isolation | calendar, publishing, BrandBrief, ConnectedAccount, revision tests | Keep broad suite; add User move test for `current_user` route isolation |
| BrandBrief relationship | `test_brand_brief_model_move.py` | Covered |
| ConnectedAccount relationship | `test_connected_account_model_move.py` | Covered |
| Feedback relationship | Feedback endpoint/model move tests cover `user_id`; no direct `User.feedback` relationship exists | No relationship test needed unless relationship added later |
| Post creation | publishing, calendar, content pack, create-post tests | Add `Post` model move metadata/creation smoke |
| Post editing | existing edit/studio tests partly cover | Add explicit `/edit-post/<id>` preserves owner and fields |
| Post deletion | existing route coverage unclear | Add delete single and delete carousel cascade checks before Post move |
| Carousel creation | publishing/calendar/content-pack tests | Covered indirectly; add Post model move group ordering/representative test |
| Scheduling | publishing scheduling tests and calendar tests | Covered |
| Scheduled publishing | `tests/test_publishing.py` scheduler tests | Covered |
| Manual publishing | `tests/test_publishing.py` manual publish tests | Covered |
| Calendar events | `tests/test_calendar.py` Sprint 1-3 coverage | Covered |
| Calendar summaries | `tests/test_calendar.py` summary tests | Covered |
| Calendar rescheduling | `tests/test_calendar.py` drag/reschedule tests | Covered |
| Studio save | `tests/test_post_studio_brand_coach.py`, PostRevision tests | Covered |
| Revision creation | `tests/test_post_revision_model_move.py` | Covered |
| Revision restore | `tests/test_post_revision_model_move.py` | Covered |

Highest-value missing tests before either central model move:

1. Direct `User` compatibility and `load_user` test.
2. Direct registration/login persistence test that asserts the stored class is
   `smu_core.models.User` after the move.
3. Direct `Post` metadata and relationship test before the move.
4. Delete single post and delete carousel route tests confirming expected
   cascade/current-user behavior.
5. Explicit `/edit-post/<id>` ownership/edit regression.

## 6. Recommended Migration Order

Recommendation: **Option A - move `User` first, verify, then move `Post`.**

Rationale:

- `User` is smaller and has fewer columns than `Post`.
- `User` is central to Flask-Login, so moving it separately gives the clearest
  diagnosis if login/session behavior changes.
- `Post` depends on `user.id`, and `User.posts` currently references `"Post"`.
  Moving `User` first while leaving `Post` in `app.py` preserves a simple
  string relationship from moved `User` to still-local `"Post"`.
- Moving `Post` first would leave `User.posts` in `app.py` pointing to a moved
  model, which should work, but failures could mix relationship-registration
  issues with the much larger Post route/publishing/scheduler surface.
- Moving both together creates a wider failure surface and would make any
  breakage harder to attribute.

Option B, move `Post` first:

- Possible, but higher blast radius because `Post` touches scheduler,
  publishing, calendar, Studio, AI, revisions, and patching.
- Any failure could be from model registration, relationship resolution, route
  imports, patching, or business logic.

Option C, move both together:

- Not recommended. It is larger than necessary and violates the pattern that
  has kept prior moves reversible and diagnosable.

## 7. User-Only Migration Phase

Target file:

- `smu_core/models/user.py`

Import strategy:

- `user.py` imports:

```python
from datetime import datetime
from flask_login import UserMixin
from smu_core.extensions import db
```

- It must not import `app.py`, `Post`, `BrandBrief`, or `ConnectedAccount`.
- Relationships remain string-based.

Compatibility re-export:

- In `app.py`, remove the inline `User` class and import:

```python
from smu_core.models.user import User
```

- Export `User` from `smu_core/models/__init__.py` and include it in `__all__`.
- Preserve `app.User` by not renaming the imported symbol.

Relationship strategy:

- Keep:
  - `db.relationship("BrandBrief", backref="user", uselist=False, lazy=True)`
  - `db.relationship("ConnectedAccount", backref="user", uselist=False, lazy=True)`
  - `db.relationship("Post", backref="user", lazy=True)`
- Do not move or alter `Post`.

Registration order:

- Import moved extracted models and moved `User` before defining `Post`.
- Define `Post`.
- Register `login_manager.user_loader`.
- Run existing `db.create_all()` and patching unchanged.

Tests required:

- `smu_app.User is smu_core.models.User`.
- `User.__table__.name == "user"`.
- Column order unchanged: `id`, `email`, `password_hash`, `created_at`.
- `email` remains unique and non-null.
- `password_hash` remains non-null.
- `UserMixin` behavior remains available.
- `load_user(str(user.id))` returns the persisted user.
- Registration creates a `User`.
- Login/logout still works.
- Existing `create_user()` fixture still works.
- `User.brand_brief`, `User.connected_account`, and `User.posts` relationships
  still resolve.

Manual smoke checks:

- Register a new account.
- Log out and log back in.
- Dashboard opens.
- Brand Brief opens and saves.
- Connected Accounts opens and saves.
- Create a draft post.
- Calendar opens.
- Scheduler starts once.

Rollback procedure:

1. Put the exact `User` class definition back into `app.py`.
2. Remove `from smu_core.models.user import User` from `app.py`.
3. Remove `User` from `smu_core/models/__init__.py`.
4. Delete `smu_core/models/user.py`.
5. Remove the User model-move regression test.
6. Rerun full tests and import smoke.

Stop conditions:

- `load_user` fails.
- `app.User` compatibility fails.
- `User` metadata changes.
- `email` uniqueness or nullability changes.
- Login/logout changes.
- Relationships to `BrandBrief`, `ConnectedAccount`, or `Post` break.
- `db.create_all()` runs before `User` and `Post` are both registered.
- A schema migration appears necessary.
- Moving `Post` becomes necessary to make `User` work.

## 8. Post-Only Migration Phase

Target file:

- `smu_core/models/post.py`

Import strategy:

- `post.py` imports:

```python
from datetime import datetime
from smu_core.extensions import db
```

- It must not import `app.py`, `User`, `PostRevision`, routes, scheduler,
  publishing, Studio, AI, Cloudinary, or Make.com helpers.
- Preserve `created_at = db.Column(db.DateTime, default=datetime.utcnow())`
  exactly during the structural move, even though it is an import-time evaluated
  default.

Compatibility re-export:

- In `app.py`, remove the inline `Post` class and import:

```python
from smu_core.models.post import Post
```

- Export `Post` from `smu_core/models/__init__.py` and include it in `__all__`.
- Preserve `app.Post` by not renaming the imported symbol.

Relationship strategy:

- Keep `user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)`.
- Keep:

```python
revisions = db.relationship(
    "PostRevision",
    backref="post",
    lazy=True,
    cascade="all, delete-orphan"
)
```

- Do not import `PostRevision` directly.
- Do not alter `User.posts`.

Registration order:

- Import moved extracted models, moved `User`, and moved `Post` before
  `db.create_all()`.
- Keep `login_manager.user_loader` in `app.py`.
- Keep existing `db.create_all()` and `ALTER TABLE post ...` patching exactly
  where they are, after `Post` import/registration.

Interaction with `PostRevision`:

- `PostRevision.post_id` remains `ForeignKey("post.id")`.
- `Post.revisions` remains the owner of the cascade and `post` backref.
- Existing PostRevision model-move tests should be rerun unchanged.

Interaction with database patching:

- The patching block depends on `post` table existing.
- It should remain after `db.create_all()`.
- Do not add or remove patch columns in this phase.

Tests required:

- `smu_app.Post is smu_core.models.Post`.
- Table name and column order unchanged.
- `user_id` still targets `user.id`.
- `Post.user` backref still works.
- `Post.revisions` and `PostRevision.post` still work.
- Cascade delete unchanged.
- `create_post` fixture still works.
- Create, edit, delete, schedule, manual publish, scheduled publish, calendar,
  carousel, Studio save, revision restore tests all pass.
- Direct smoke for `db.metadata.tables["post"]` before patching logic.

Manual smoke checks:

- Dashboard opens.
- Create single draft.
- Create carousel draft.
- Edit a post.
- Schedule a post.
- Open calendar.
- Reschedule in calendar.
- Open AI Studio.
- Save a Studio caption and verify revision history.
- Restore a revision.
- Do not publish a real post during this structural phase.
- Scheduler starts once.

Rollback procedure:

1. Put the exact `Post` class definition back into `app.py`.
2. Remove `from smu_core.models.post import Post` from `app.py`.
3. Remove `Post` from `smu_core/models/__init__.py`.
4. Delete `smu_core/models/post.py`.
5. Remove the Post model-move regression test.
6. Rerun full tests, compile, import smoke, and manual smoke.

Stop conditions:

- `Post` metadata changes.
- `user_id` foreign key changes.
- `Post.user` breaks.
- `Post.revisions` or `PostRevision.post` breaks.
- Cascade behavior changes.
- Scheduler due query changes.
- Publishing payloads or status transitions change.
- Calendar serialization changes.
- Studio revision creation or restore changes.
- Existing `ALTER TABLE post` patching runs before `Post` is registered.
- Another model must be moved to make `Post` work.
- A schema migration appears necessary.

## 9. Rollback Strategy

General rollback for either central model:

1. Restore the exact original class body into `app.py`.
2. Remove the matching import from `app.py`.
3. Remove the model export from `smu_core/models/__init__.py`.
4. Delete the new model file.
5. Delete only the focused regression test for that attempted move.
6. Confirm `db.create_all()` still runs after all model definitions/imports.
7. Run full tests and the relevant import smoke test.

Because each phase moves exactly one class, rollback should be a small textual
reversal with no schema migration.

## 10. Stop Conditions

Stop immediately if any of these occur:

- SQLAlchemy metadata differs unexpectedly.
- A foreign key target changes.
- Relationship or backref behavior changes.
- Flask-Login cannot load users.
- `app.User` or `app.Post` compatibility breaks.
- Route ownership checks change.
- Scheduler lifecycle or due query behavior changes.
- Publishing payload or webhook behavior changes.
- Calendar event serialization changes.
- Studio revision behavior changes.
- `db.create_all()` or `ALTER TABLE post` order must be rewritten.
- Moving both models together appears necessary.
- A database migration appears necessary.
- Existing tests fail because of the structural move.

## 11. Unresolved Questions

- The `Post.created_at` default currently uses `datetime.utcnow()` instead of
  the callable `datetime.utcnow`. The move should preserve this exactly, but a
  later cleanup phase may want to correct it with explicit migration/testing.
- `Post.user_id` is nullable, even though most current workflows require
  ownership. Preserve this during the model move; evaluate separately later.
- `PostRevision.user_id` has no relationship to `User`; preserve this during the
  model move.
- The `User.connected_account` relationship indentation is unusual in `app.py`.
  Preserve behavior during the move; formatting cleanup should be separate.
- Full `git diff --check` has previously been blocked by unrelated template
  whitespace in the working tree. Keep model-move checks scoped when necessary,
  and clean unrelated whitespace only in an approved cleanup phase.
