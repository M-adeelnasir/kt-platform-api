"""Harder eval corpus (plan §7): long, multi-topic documents where many distinct facts are
packed into a single doc. This stresses chunking — a good strategy keeps each topic's facts
together so the right chunk is retrievable; a naive window can split or dilute them.

Run the A/B at a small top_k so retrieval is selective (see evals/ab_chunking.py).
"""

from __future__ import annotations

from evals.corpus import FixtureDoc

CORPUS_HARD: list[FixtureDoc] = [
    FixtureDoc(
        "platform-handbook",
        "Backend Platform Handbook",
        """The auth-service handles login and tokens. It runs on port 8081 behind the gateway.
It issues JWT access tokens that are valid for 30 minutes, and refresh tokens valid for 14 days.
Active sessions are stored in Redis database number 2.

The paygate service submits charges to our payment provider NorthPay. Failed charge syncs are
retried with exponential backoff, capped at 3 attempts, after which they go to the dead_letter
table. The two central Postgres tables are payment_attempts and ledger_entries.

The catalog-search service is backed by an Elasticsearch index named catalog-v3. A full reindex
job runs every Sunday at 04:00 UTC. The product catalog is cached in Redis with a TTL of 15
minutes; stale reads beyond that fall through to Postgres.

The notifier service sends email through Amazon SES and SMS through Twilio. It is rate limited to
10 messages per second. Message templates live in the S3 bucket named notif-templates.

The etl-pipeline is an Airflow DAG named daily_rollup that runs at 01:00 UTC. It writes aggregated
data into the warehouse schema called analytics. To backfill a date range, pass the --start-date
flag to the DAG run.

The gateway is built on Kong. It enforces a rate limit of 200 requests per second per API key, and
it strips any incoming X-Internal-* headers so clients cannot spoof internal trust.""",
    ),
    FixtureDoc(
        "ops-and-incidents",
        "Operations and Incident History",
        """Deploys go out through GitHub Actions using a blue/green strategy on AWS ECS. To roll
back a production deploy, run `make rollback ENV=prod`, which repoints the load balancer at the
previous task set.

On-call is managed by the PagerDuty schedule named Platform. An unacknowledged page escalates to
the secondary after 15 minutes.

Incident INC-4471 was a catalog-search outage. The root cause was that the Elasticsearch reindex
job ran during peak traffic and saturated the ES heap. The fix was to move the reindex to Sunday
at 04:00 UTC, off peak.

Incident INC-4490 was a paygate double-charge. The root cause was that the manual replay script
re-submitted charges that had already hit the retry cap because it did not check attempt_count.
The fix added an attempt_count guard before replaying.

Incident INC-4502 was an auth-service token storm. The root cause was that Redis database 2 used
the allkeys-lru eviction policy, which evicted live sessions under memory pressure and forced mass
re-logins. The fix changed the eviction policy to volatile-ttl.

Postgres backups are written nightly to the S3 bucket db-backups with a retention of 30 days. A
restore drill is performed quarterly. The gateway has a published SLO of 99.9% availability.""",
    ),
    FixtureDoc(
        "decisions-and-gotchas",
        "Decisions and Gotchas",
        """We chose Kong as the gateway in 2024 rather than building our own, mainly for its plugin
ecosystem (auth, rate limiting, logging) which saved months of work.

We chose Elasticsearch over Postgres full-text search for the catalog because we needed fine
relevance tuning and synonym handling that Postgres FTS could not provide.

Gotcha: never run the catalog reindex during business hours — it competes for the ES heap and was
the cause of INC-4471. Always let it run on the Sunday 04:00 UTC schedule.

Gotcha: notifier email templates must be ASCII-only. UTF-8 emoji in a template body break the SES
send and silently drop the email.

Gotcha: because the gateway strips all X-Internal-* headers, never rely on those headers in a
downstream service for trust decisions — they will always be absent in production.

Gotcha (known gap): auth refresh tokens are NOT revoked when a user changes their password yet.
Until that is fixed, a stolen refresh token survives a password reset.

Terminology: a "rollup" is the daily aggregation job (the daily_rollup DAG). "Dunning" is the
process of retrying failed subscription payments before cancelling.""",
    ),
]
