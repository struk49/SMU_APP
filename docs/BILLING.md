# Billing

## Scope

Milestones 2A and 2B add the Stripe subscription foundation and gate product
access for one recurring SMU plan. They do not add multiple tiers, Customer
Portal, cancellation UI or subscription management screens.

## Architecture

Billing routes live in `smu_core.blueprints.billing` and preserve app-level
endpoint names:

- `POST /billing/checkout`
- `GET /billing/success`
- `GET /billing/cancel`
- `POST /billing/webhook`

Stripe business logic lives in `smu_core.services.billing`. Routes call the
service and do not contain webhook state rules.

## User State

Subscription state is stored directly on `User` for this single-plan slice:

- `stripe_customer_id`
- `stripe_subscription_id`
- `subscription_status`
- `subscription_current_period_end`

All fields are nullable. Existing users are not marked paid by default.

## Checkout Flow

Authenticated users can start Stripe-hosted Checkout through
`POST /billing/checkout`. The Checkout Session uses:

- `mode="subscription"`
- one line item from `STRIPE_PRICE_ID`
- `client_reference_id` containing the SMU user ID
- metadata containing the SMU user ID
- the existing Stripe customer ID when present
- customer email when no Stripe customer ID exists yet

The success page only tells the user that Stripe is confirming the subscription.
It does not activate access.

## Webhook Flow

`POST /billing/webhook` is public because Stripe calls it directly. It reads the
raw request body and verifies `Stripe-Signature` with `STRIPE_WEBHOOK_SECRET`.

Handled events:

- `checkout.session.completed`
- `invoice.paid`
- `invoice.payment_failed`
- `customer.subscription.updated`
- `customer.subscription.deleted`

Webhook handlers are naturally idempotent: they update fields on the existing
user and do not create duplicate subscription rows.

## Active Access Rule

The billing helper is:

```python
has_active_subscription(user)
```

It returns `True` only for:

- `active`
- `trialing`

Product access is enforced through `smu_core.services.access`:

```python
has_product_access(user)
subscription_required
```

By default, a logged-in user can only use paid SMU product features when their
persisted Stripe subscription status is `active` or `trialing`. Existing users
with `subscription_status = None` are treated as unpaid and are not
grandfathered.

Admins listed in `SMU_ADMIN_EMAILS` have an explicit product-access bypass for
support and testing. This bypass is server-side only and cannot be controlled by
the user.

## Registration Modes

`REGISTRATION_MODE` controls account creation:

- `subscription` (default): anyone may register, then must subscribe before
  using SMU product features.
- `beta`: preserves the approved-beta registration gate.
- `open`: testing/future flexibility mode where users can register and use the
  app without subscription gating.

Beta application status is not used as paid subscription access.

## Public And Protected Routes

Public or ungated routes include:

- `/` for anonymous visitors
- `/landing`
- `/pricing`
- `/about`
- `/privacy`
- `/terms`
- `/contact`
- `/beta/apply`
- `/register`
- `/login`
- `/logout`
- `/billing/checkout`
- `/billing/success`
- `/billing/cancel`
- `/billing/webhook`

Paid product routes include the authenticated dashboard, posts, scheduling,
publishing actions, AI editor, Studio, TikTok repurposing, Content Packs, Brand
Brief editing and Calendar. Connected Accounts can be viewed by unpaid logged-in
users, but account changes and LinkedIn OAuth actions require product access.

Blocked users see the stable flash message:

```text
An active SMU subscription is required to use this feature.
```

and are redirected to `/pricing`.

## Local Test Mode Setup

Set test-mode Stripe values in `.env`:

```dotenv
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_ID=price_...
```

Do not commit real secrets. Tests mock Stripe and must not contact Stripe.

## Deferred Work

- Customer Portal
- cancellation UI
- pricing page redesign
- annual plans
- multiple tiers
- coupons or trials
- invoice UI
- Stripe Tax
- durable webhook event ID storage
