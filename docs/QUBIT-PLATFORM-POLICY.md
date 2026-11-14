# Qubit platform policy — official APIs, approvals and hard rules

**Status:** policy reference. The machine source of truth is
`voicebridge/integrations/platform_policy.py`; this document explains it and
does not restate per-platform counts that can drift. Requirement source:
`docs/QUBIT-OFFICE-MASTER-PLAN.md` §33 and §36.

## The rule

A Qubit agent may integrate with an external platform only through that
platform's **official API/OAuth route**. It never drives a logged-in UI, never
scrapes while authenticated, never fakes engagement and never sends bulk or
spam messages. Human approval is required for first publish, claims/pricing,
sensitive replies, spend and irreversible actions.

## Registry fields

Every registered platform records:

| Field | Meaning |
|---|---|
| `official_api` / `oauth_required` | Whether a documented API/OAuth route exists |
| `automated_publish` | `allowed`, `limited` or `forbidden` |
| `automated_engagement` | Always `forbidden` |
| `api_notes` | Official route and prohibited shortcuts |
| `rate_limit_notes` | Per-app/per-account limits and backoff |
| `approval_requirements` | Human gates, including the five baseline approvals |
| `bot_labeling` | Disclosure/labelling duties |
| `app_review` | App review, audit or partner-program requirement |
| `data_consent` | Consent, minimisation and data-use rules |
| `safe_fallback` | What to do when automation is unavailable or forbidden |
| `browser_automation`, `logged_in_scraping`, `fake_engagement`, `fake_reviews`, `fake_followers`, `bulk_dms`, `spam` | Always `forbidden` |

Registered platforms: Etsy, X, Meta (Facebook), Instagram, Threads, LinkedIn,
TikTok, YouTube, Pinterest, Reddit, email, generic web and CRM. Aliases
(`twitter`, `facebook`, `generic_web`, …) resolve to the canonical record.

## Evaluator

`evaluate_action(platform_id, action)` returns a decision that separates three
questions a caller must not conflate:

1. **allowed** — may this action happen at all?
2. **automated** — may it run without a human in the loop?
3. **requires_approval** — must a human approval token be presented?

For example, X automated posts are `allowed`/`automated`, LinkedIn publishing is
`limited` (approval required), and generic web publishing is `forbidden`.
Automated likes/follows/comments, fake engagement/reviews/followers, bulk DMs
and browser automation are refused on every platform.

## Adapters

`voicebridge/integrations/` contains the typed, dry-run adapters:

- `EtsyAdapter` — shop/listing/receipt/inventory reads; digital draft, publish,
  price, refund and buyer message operations.
- `SocialAdapter` — publish, reply, direct message and metrics for any
  registered social platform.
- `CrmAdapter` — contact read/upsert, activity logging and lead creation.
- `EmailAdapter` — approved sending with marketing opt-in enforcement.

Every adapter:

- accepts only an injected `Transport`; no module opens a network client;
- defaults to `dry_run=True` and reports `executed=False`;
- returns `adapter_unavailable` / `adapter_not_configured` when the transport or
  credentials are absent — never a fabricated success;
- requires an `ApprovalToken` for publish, price, refund, message, reply, DM,
  email send and lead creation;
- requires an idempotency key for mutations and replays the original result;
- refuses with `adapter_rate_limited` before calling the provider.

Adapter idempotency is in-process; durable cross-restart idempotency belongs to
the Business OS tool gateway. The adapters prevent duplicate local attempts,
not duplicate provider effects after an `unknown` outcome.

The Business OS tool gateway remains the durable enforcement point for real
calls; these adapters are the typed surface beneath it.
