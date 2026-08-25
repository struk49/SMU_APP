# Billing

## Scope

Milestones 2A, 2B and 2C add the Stripe subscription foundation, product-access
gating, and a customer billing experience for one recurring SMU plan. They do
not add multiple tiers, custom card-management forms, invoice dashboards or
custom cancellation flows.

## Architecture

Billing routes live in `smu_core.blueprints.billing` and preserve app-level
endpoint names:

- `POST /billing/checkout`
- `GET /billing`
- `POST /billing/portal`
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
- `subscription_cancel_at_period_end`

Stripe ID/status/date fields are nullable. Existing users are not marked paid
by default. `subscription_cancel_at_period_end` defaults to false and is SMU's
normalized "scheduled to cancel" flag. It may be derived from Stripe's legacy
`cancel_at_period_end` boolean or from a valid `cancel_at` timestamp.

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

## Billing Page And Customer Portal

Authenticated users can open `GET /billing` whether or not they currently have
product access. This is intentional so unpaid, past-due and canceled users can
subscribe or fix billing.

The billing page displays safe customer-facing subscription information:

- plan name: SMU Pro
- access state
- friendly subscription status
- renewal date for active/trialing subscriptions when stored
- scheduled cancellation date when Stripe reports cancellation at period end
- subscription and billing actions

It does not display Stripe customer IDs, subscription IDs, API keys, webhook
URLs or raw Stripe errors.

Stripe Customer Portal is opened through `POST /billing/portal`. SMU always uses
the authenticated user's stored `stripe_customer_id`; the browser cannot submit
or override a Stripe customer ID. The portal return URL points back to
`/billing`.

Customer Portal is used for Stripe-hosted payment-method and subscription
management. SMU does not directly cancel subscriptions when a user clicks Manage
Billing, and returning from the portal does not change access. Stripe webhooks
remain authoritative.

When a customer schedules cancellation at the end of the billing period, Stripe
may continue reporting the subscription status as `active`. Depending on API
version and portal flow, the scheduled cancellation can be represented by either
`cancel_at_period_end=true` or a valid `cancel_at` timestamp. SMU normalizes both
forms onto `subscription_cancel_at_period_end` and displays a notice such as
`Cancels on 23 September 2026`. Product access remains active until Stripe sends
the final subscription-ended event and the persisted status becomes inactive.

The Customer Portal can also let the customer reverse a scheduled cancellation.
When Stripe sends a later subscription update with `cancel_at_period_end=false`
and no `cancel_at` timestamp, SMU removes the scheduled-cancellation notice.

Friendly status labels are presentation-only:

- `active` -> Active
- `trialing` -> Trial
- `past_due` -> Payment issue
- `unpaid` -> Payment required
- `canceled` -> Canceled
- `incomplete` -> Setup incomplete
- `incomplete_expired` -> Setup expired
- `paused` -> Paused
- missing status -> No active subscription
- unknown status -> Subscription unavailable

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

`customer.subscription.updated` is the source of truth for scheduled
cancellation state. It updates `subscription_status`,
`subscription_current_period_end`, `stripe_subscription_id` and
`subscription_cancel_at_period_end` when those values are present in the Stripe
payload. Newer Stripe API versions may put the current period end on
`subscription.items.data[].current_period_end` instead of
`subscription.current_period_end`; SMU reads the legacy top-level value first,
then the first usable subscription item period for the current one-plan setup.
Scheduled cancellation is detected from either `cancel_at_period_end=true` or a
valid `cancel_at` timestamp. `canceled_at` is not used by itself as a future
scheduled-cancellation signal.

`customer.subscription.deleted` marks the subscription canceled and clears
`subscription_cancel_at_period_end` because the subscription has actually ended.

## Active Access Rule

The billing helper is:

```python
has_active_subscription(user)
```

It returns `True` only for:

- `active`
- `trialing`

Scheduled cancellation does not change this rule. A user with
`subscription_status="active"` and `subscription_cancel_at_period_end=True`
keeps product access until Stripe reports the subscription has ended.

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
- `/billing`
- `/billing/checkout`
- `/billing/portal`
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

## Stripe Dashboard Manual Setup

Before live use, configure Stripe Customer Portal in the Stripe Dashboard:

- enable Customer Portal
- review branding and business details
- enable payment-method updates as desired
- configure subscription cancellation behavior
- configure subscription-management features appropriate for the single SMU Pro
  plan
- verify the portal return flow in test mode before live mode

Do not rely on portal features until they are enabled in Stripe. This milestone
does not implement multiple plans, upgrades/downgrades, invoices UI, Stripe Tax
or custom cancellation forms in SMU.

## Deferred Work

- annual plans
- multiple tiers
- coupons or trials
- invoice UI
- Stripe Tax
- durable webhook event ID storage
