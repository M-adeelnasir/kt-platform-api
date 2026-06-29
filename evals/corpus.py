"""Fixture corpus for the eval harness (plan §7).

Hermetic, fully synthetic documents with distinctive, internally-consistent facts — so every
golden question has a known answer and a hallucination is detectable. Mirrors what a departing
engineer's docs/emails look like.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FixtureDoc:
    doc_id: str
    title: str
    text: str


CORPUS: list[FixtureDoc] = [
    FixtureDoc(
        "overview",
        "Payments System Overview",
        """Our payments stack has three services: payments-api (FastAPI), billing-reconciler
(a nightly batch job), and admin-dashboard (Next.js). Stripe is our payment processor and
domain data lives in a Postgres database called billing. A customer is charged through Stripe,
Stripe sends a webhook to payments-api, payments-api writes a row to the payment_attempts table,
and the nightly billing-reconciler matches attempts against ledger_entries. The two central
Postgres tables are payment_attempts and ledger_entries. Deploys go through GitHub Actions to
AWS ECS. The service owner is Adeel Nasir.""",
    ),
    FixtureDoc(
        "runbook",
        "Billing Reconciler Runbook",
        """The billing-reconciler runs nightly at 02:30 UTC as an ECS scheduled task. To re-run
it manually for a specific day, run: python -m billing_reconciler.run --date YYYY-MM-DD. Failed
charge syncs use exponential backoff capped at 5 attempts; after 5 failures the record is written
to the dead_letter table and the on-call engineer is paged via the PagerDuty service named
Billing. The reconciliation discrepancy tolerance is $0.50 — anything below that is auto-cleared.""",
    ),
    FixtureDoc(
        "decisions",
        "Architecture Decision Log",
        """In February 2024 we chose Stripe over Adyen because of faster integration and better
test-mode tooling. We chose nightly batch reconciliation instead of real-time to keep cost and
complexity down; revisit this if chargeback disputes exceed 50 per day. We kept Postgres rather
than DynamoDB for billing data because we need multi-row transactions.""",
    ),
    FixtureDoc(
        "gotchas",
        "Gotchas and Landmines",
        """The Stripe API rate limit is 100 requests per second; do not raise the reconciler retry
cap to compensate. The invoices.status enum still has a legacy value pending_v1 used by exactly 3
old customers — do not remove it until they are migrated. All timestamps are UTC except the
legacy_reports table, which is US/Pacific. Never run the reconciler and the monthly-export job at
the same time; they contend on a database lock, which caused the June 12 staging outage.""",
    ),
    FixtureDoc(
        "contacts",
        "Contacts and Glossary",
        """Contacts: the Stripe Technical Account Manager is Maria Lopez (maria@example.com); the
finance lead is Sara Khan; on-call lives in the #billing-oncall Slack channel. Glossary: DLQ means
dead-letter queue, where unprocessable records land. Recon means the nightly billing
reconciliation job. An idempotency key is a unique key sent with each Stripe request so retries do
not double-charge.""",
    ),
]
