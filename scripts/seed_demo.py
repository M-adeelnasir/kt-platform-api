"""Rich demo data for screenshots / product videos (NOT for eval or tests).

Populates the real database so every screen looks alive without waiting on the local LLM:
- employees across departments (some on notice, some departing soon, one departed)
- knowledge bases with PRE-WRITTEN synthesized artifacts (overview/gotchas/glossary/gap/runbook/
  contacts) — no synthesis run needed
- sources shown "connected" with varied last-synced times (drives dashboard connector health)
- a finished interview per hero KB
- historical Q&A rows spread over the last month (drives the analytics screen)

Idempotent-ish: if the hero employee already exists, it exits (reset first for a clean slate):
    uv run python -m scripts.reset_data          # optional clean slate
    uv run python -m scripts.seed_demo

NOTE: this does NOT seed chunks/vectors, so the live oracle won't answer from seeded KBs (that
needs embeddings). The artifacts, dashboards, successor view, analytics, and risk screens are all
fully populated. Sync one KB live if you want to demo the oracle end-to-end.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from data.db import session_scope
from data.models import (
    Interview,
    InterviewMessage,
    KnowledgeItem,
    QaQuery,
    Source,
)
from data.repositories import EmployeeRepository, KnowledgeBaseRepository, WorkspaceRepository

UTC = dt.UTC


def _ago(days: float, hour: int = 10) -> dt.datetime:
    return (dt.datetime.now(UTC) - dt.timedelta(days=days)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )


@dataclass
class Artifact:
    kind: str
    title: str
    body: str


@dataclass
class SourceSpec:
    type: str  # google | github | atlassian | microsoft
    synced_days_ago: float


@dataclass
class QA:
    question: str
    answer: str | None
    abstained: bool
    days_ago: float


@dataclass
class Person:
    name: str
    email: str
    title: str
    department: str
    status: str  # active | on_notice | departed
    github_username: str | None = None
    notice_in_days: int | None = None  # for on_notice: days until last day
    sources: list[SourceSpec] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    interview: list[tuple[str, str]] = field(default_factory=list)  # (role, content)
    qa: list[QA] = field(default_factory=list)


# --- Reusable artifact builders (kept realistic + markdown-formatted) ---


def _hiring_artifacts() -> list[Artifact]:
    return [
        Artifact(
            "overview",
            "System Overview",
            "Imran owned the **AI hiring & assessment platform** — three services that move a "
            "candidate from application to offer.\n\n"
            "- **screen-svc** — screens candidates and auto-advances anyone scoring **≥ 75** to the "
            "interview stage; below-threshold candidates go to human review, never auto-rejected.\n"
            "- **assess-svc** — submits assessments to the scoring vendor **Cognita** (rate limit "
            "**60 req/min**), retrying a failed call up to **3 times** with backoff before flagging "
            "for manual review.\n"
            "- **interview-svc** — schedules interviews and sends reminders daily at **09:00 UTC**.\n\n"
            "All hiring data lives in the `ats_prod` Postgres database; the `candidates` and "
            "`assessments` tables have a unique index on `(candidate_id, assessment_id)` to dedupe "
            "re-scores.",
        ),
        Artifact(
            "gotcha",
            "Gotchas & Landmines",
            "- **Never re-score candidates already in `interview` or `hired`** — it can re-trigger "
            "candidate emails. This caused **incident HR-114** (a bulk re-score double-emailed ~200 "
            "candidates).\n"
            "- **Never auto-reject on the AI score alone** — below-threshold candidates always get a "
            "human review first.\n"
            "- **Don't exceed Cognita's 60 req/min** — batch retries with jitter or you'll hit 429s.\n"
            "- Cognita credentials load from **Vault at `secret/hiring/cognita`** — never `.env` or "
            "the repo.",
        ),
        Artifact(
            "glossary",
            "Glossary",
            "- **Cognita** — third-party scoring provider used by assess-svc.\n"
            "- **ats_prod** — the production Postgres database for all hiring data.\n"
            "- **HR-114** — the 2026-05-09 incident where a bulk re-score double-emailed candidates.\n"
            "- **auto-advance** — moving a candidate scoring ≥ 75 straight to the interview stage.\n"
            "- **Idempotency-Key** — header on every notification so a duplicate send is a no-op.",
        ),
        Artifact(
            "runbook",
            "Runbook — Common Operations",
            "**Re-score a stuck candidate**\n"
            "1. Confirm the candidate is still in `screening` (never re-score interview/hired).\n"
            "2. Run `rescore.py`; it retries Cognita up to 3 times.\n"
            "3. If still failing, flag for manual review — don't loop.\n\n"
            "**Cognita is rate-limiting (429s)**\n"
            "1. Check the per-key request rate against the 60 req/min cap.\n"
            "2. Back off with jitter; the batch will drain.\n\n"
            "**Reminders didn't send**\n"
            "1. The job runs 09:00 UTC; check the scheduler ran.\n"
            "2. Dedupe by `(candidate_id, interview_date)` before resending.",
        ),
        Artifact(
            "gap",
            "Knowledge Gaps",
            "### Gaps\n"
            "- Exact backoff algorithm for Cognita retries isn't documented.\n"
            "- The partial-refund path in the reconciler is half-built (ticket HIRE-203).\n"
            "- No runbook for rotating the Cognita Vault credentials.\n\n"
            "### Suggested interview questions\n"
            "1. What's the precise backoff + jitter policy for assess-svc retries?\n"
            "2. What's left to finish on partial refunds in recon-worker?\n"
            "3. Who owns the Cognita vendor relationship and key rotation?",
        ),
        Artifact(
            "contact",
            "Key Contacts",
            "- **Cognita vendor** — scoring provider; contact via the shared vendor Slack channel.\n"
            "- **Platform on-call** — owns `ats_prod` database access and Vault.\n"
            "- **People Ops** — consumers of HR-114 postmortem actions.",
        ),
    ]


def _pm_artifacts() -> list[Artifact]:
    return [
        Artifact(
            "overview",
            "System Overview",
            "Layla owned the **Candidate Experience** product area — the roadmap, discovery, and "
            "delivery coordination across the hiring squad.\n\n"
            "- Ran quarterly planning and the **now/next/later** roadmap in Jira + Confluence.\n"
            "- Owned the **assessment redesign** initiative and the vendor decision for Cognita.\n"
            "- Primary liaison between Engineering, People Ops, and Legal on candidate data.",
        ),
        Artifact(
            "decision",
            "Key Decisions",
            "- **Chose Cognita over building in-house scoring** — faster to market; revisit if "
            "volume exceeds the 60 req/min tier.\n"
            "- **Human-in-the-loop for below-threshold candidates** — legal/fairness requirement, "
            "never auto-reject on AI score.\n"
            "- **Deferred multi-language assessments** to next quarter (localization cost).",
        ),
        Artifact(
            "gotcha",
            "Gotchas & Landmines",
            "- **Legal must sign off** on any change to how candidate data is stored or scored.\n"
            "- The Cognita contract renews annually — the 60 req/min tier is a hard cap on "
            "throughput; plan launches around it.\n"
            "- Stakeholder reviews happen Thursdays; ship-blocking decisions need them by Wednesday.",
        ),
        Artifact(
            "contact",
            "Key Contacts & Stakeholders",
            "- **Engineering lead** — Imran Shah (backend), owns delivery of assessment changes.\n"
            "- **People Ops** — approves candidate-facing copy and process.\n"
            "- **Legal** — reviews data-handling and fairness.\n"
            "- **Cognita CSM** — vendor success manager for the scoring contract.",
        ),
        Artifact(
            "gap",
            "Knowledge Gaps",
            "### Gaps\n"
            "- The next-quarter localization plan is only in Layla's head.\n"
            "- Cognita renewal negotiation history isn't written down.\n\n"
            "### Suggested interview questions\n"
            "1. What's the localization rollout plan and its dependencies?\n"
            "2. What are the levers in the Cognita renewal?",
        ),
    ]


def _sre_artifacts() -> list[Artifact]:
    return [
        Artifact(
            "overview",
            "System Overview",
            "Omar owned **platform reliability** — CI/CD, the on-call rotation, and the "
            "infrastructure the hiring services run on.\n\n"
            "- Kubernetes clusters + Terraform for `ats_prod` and staging.\n"
            "- The alerting stack and the weekly on-call handoff.\n"
            "- Deploy pipeline and rollback procedures for all three hiring services.",
        ),
        Artifact(
            "runbook",
            "Runbook — Incidents & Deploys",
            "**Rollback a bad deploy**\n"
            "1. `kubectl rollout undo deploy/<svc>`.\n"
            "2. Confirm health checks green; announce in the incident channel.\n\n"
            "**On-call handoff (weekly)**\n"
            "1. Review open alerts + silences.\n"
            "2. Hand off context in the on-call doc; page the next owner.\n\n"
            "**Scale for a hiring surge**\n"
            "1. Bump assess-svc replicas; watch Cognita's 60 req/min ceiling — scaling pods does "
            "not raise the vendor limit.",
        ),
        Artifact(
            "gotcha",
            "Gotchas & Landmines",
            "- **Scaling assess-svc pods does NOT raise Cognita's rate limit** — you'll just queue.\n"
            "- Terraform state is shared; never run `apply` without locking.\n"
            "- The 09:00 UTC reminder job and the nightly batch overlap — watch DB load.",
        ),
        Artifact(
            "gap",
            "Knowledge Gaps",
            "### Gaps\n"
            "- Disaster-recovery restore has never been dry-run end to end.\n\n"
            "### Suggested interview questions\n"
            "1. Walk through a full `ats_prod` restore from backup.\n"
            "2. What manual steps aren't yet in Terraform?",
        ),
    ]


def _fe_artifacts() -> list[Artifact]:
    return [
        Artifact(
            "overview",
            "System Overview",
            "Yusuf owned the **candidate-facing web app** — the application form, assessment UI, and "
            "the recruiter dashboard.\n\n"
            "- Next.js frontend talking to screen-svc and interview-svc.\n"
            "- Owned accessibility and the multi-step assessment flow.",
        ),
        Artifact(
            "gotcha",
            "Gotchas & Landmines",
            "- The assessment form autosaves every 10s — don't remove it, candidates lose work "
            "otherwise.\n"
            "- Recruiter dashboard reads are cached 60s; 'stale' numbers are usually just the cache.",
        ),
        Artifact(
            "glossary",
            "Glossary",
            "- **assessment flow** — the multi-step candidate test UI.\n"
            "- **recruiter dashboard** — internal view of the candidate pipeline.",
        ),
    ]


PEOPLE: list[Person] = [
    Person(
        name="Imran Shah",
        email="imran.shah@example.com",
        title="Senior Backend Engineer",
        department="Engineering",
        status="on_notice",
        github_username="continuity-champ",
        notice_in_days=9,
        sources=[
            SourceSpec("github", 0.2),
            SourceSpec("google", 1),
            SourceSpec("atlassian", 2),
            SourceSpec("microsoft", 0.5),
        ],
        artifacts=_hiring_artifacts(),
        interview=[
            ("ai", "You own assess-svc's retry logic — what's the exact backoff policy?"),
            (
                "human",
                "Exponential backoff, capped at 3 attempts: ~2s, 4s, 8s with jitter so a burst of "
                "429s doesn't retry in lockstep against Cognita's 60 req/min cap. Only 429/5xx are "
                "retried; a 4xx goes straight to manual review.",
            ),
            ("ai", "What's still unfinished that your successor should pick up?"),
            (
                "human",
                "Partial refunds in recon-worker (ticket HIRE-203) and rotating the Cognita Vault "
                "credentials at secret/hiring/cognita — there's no runbook for the rotation yet.",
            ),
        ],
        qa=[
            QA("What is Cognita's rate limit?", "60 requests per minute per API key.", False, 1),
            QA("What score auto-advances a candidate?", "A score of 75 or higher.", False, 2),
            QA("What caused HR-114?", "Re-scoring candidates already in interview/hired.", False, 3),
            QA("How many scoring retries?", "Up to 3, with backoff.", False, 5),
            QA("Where are Cognita creds stored?", "Vault at secret/hiring/cognita.", False, 8),
            QA("What is the vacation policy?", "The knowledge base does not cover this.", True, 4),
            QA("Who approves salary bands?", "The knowledge base does not cover this.", True, 9),
        ],
    ),
    Person(
        name="Layla Haddad",
        email="layla.haddad@example.com",
        title="Senior Product Manager",
        department="Product",
        status="on_notice",
        notice_in_days=24,
        sources=[
            SourceSpec("atlassian", 1),
            SourceSpec("google", 3),
            SourceSpec("microsoft", 2),
        ],
        artifacts=_pm_artifacts(),
        interview=[
            ("ai", "What decisions about candidate scoring should your successor not reopen?"),
            (
                "human",
                "The human-in-the-loop rule for below-threshold candidates is a legal/fairness "
                "requirement — never auto-reject on the AI score. And Cognita vs. in-house was "
                "settled; only revisit if volume outgrows the 60 req/min tier.",
            ),
        ],
        qa=[
            QA("Why did we choose Cognita?", "Faster to market than building in-house.", False, 2),
            QA("Can we auto-reject low scorers?", "No — human review is required.", False, 6),
            QA("What's the localization timeline?", "The KB does not cover this.", True, 7),
        ],
    ),
    Person(
        name="Omar Khalil",
        email="omar.khalil@example.com",
        title="Staff SRE / DevOps",
        department="Engineering",
        status="on_notice",
        github_username="continuity-champ",
        notice_in_days=5,
        sources=[
            SourceSpec("github", 1),
            SourceSpec("atlassian", 4),
            SourceSpec("microsoft", 1),
        ],
        artifacts=_sre_artifacts(),
        qa=[
            QA("How do I roll back a deploy?", "kubectl rollout undo deploy/<svc>.", False, 1),
            QA("Does scaling pods raise Cognita's limit?", "No — the vendor cap is fixed.", False, 3),
        ],
    ),
    Person(
        name="Yusuf Rahman",
        email="yusuf.rahman@example.com",
        title="Frontend Engineer",
        department="Engineering",
        status="departed",
        github_username=None,
        sources=[SourceSpec("github", 40), SourceSpec("google", 42)],
        artifacts=_fe_artifacts(),
        qa=[
            QA("Why does the form autosave?", "So candidates don't lose work.", False, 20),
        ],
    ),
    Person(
        name="Sara Nasser",
        email="sara.nasser@example.com",
        title="Data Scientist",
        department="Data",
        status="active",
    ),
    Person(
        name="Aisha Mansour",
        email="aisha.mansour@example.com",
        title="Sales Lead",
        department="Revenue",
        status="active",
    ),
    Person(
        name="Fatima Zahra",
        email="fatima.zahra@example.com",
        title="HR Manager",
        department="People",
        status="active",
    ),
]


def seed_demo() -> None:
    with session_scope() as session:
        workspace = WorkspaceRepository(session).get_default()
        if workspace is None:
            raise SystemExit(
                "No workspace. Run `uv run python -m scripts.reset_data` or `-m data.seed` first."
            )
        ws_id = workspace.id
        emp_repo = EmployeeRepository(session, ws_id)
        kb_repo = KnowledgeBaseRepository(session, ws_id)

        if emp_repo.get_by_email(PEOPLE[0].email) is not None:
            raise SystemExit("Demo already seeded (Imran exists). Reset first for a clean slate.")

        for p in PEOPLE:
            emp = emp_repo.create(
                name=p.name,
                email=p.email,
                title=p.title,
                github_username=p.github_username,
                status=p.status,
                connectors=[s.type for s in p.sources],
            )
            # Departments aren't a column yet — stash on the KB-less employees via title is enough
            # for the demo; department drives the risk view through a name→dept map there.

            if p.status == "active":
                print(f"created active: {p.name}")
                continue

            kb = kb_repo.create(subject_person_name=p.name, employee_id=emp.id)
            kb.status = p.status
            if p.notice_in_days is not None:
                emp.notice_end_date = _ago(-p.notice_in_days)  # future date

            for spec in p.sources:
                session.add(
                    Source(
                        workspace_id=ws_id,
                        knowledge_base_id=kb.id,
                        type=spec.type,
                        config_json={"demo": True},
                        status="connected",
                        last_synced_at=_ago(spec.synced_days_ago),
                    )
                )

            for a in p.artifacts:
                session.add(
                    KnowledgeItem(
                        workspace_id=ws_id,
                        knowledge_base_id=kb.id,
                        kind=a.kind,
                        title=a.title,
                        body=a.body,
                        source_refs_json=[],
                    )
                )

            if p.interview:
                iv = Interview(workspace_id=ws_id, knowledge_base_id=kb.id, status="completed")
                session.add(iv)
                session.flush()
                for idx, (role, content) in enumerate(p.interview):
                    msg = InterviewMessage(
                        workspace_id=ws_id,
                        interview_id=iv.id,
                        role=role,
                        content=content,
                    )
                    msg.created_at = _ago(3, hour=9 + idx)
                    session.add(msg)

            for q in p.qa:
                row = QaQuery(
                    workspace_id=ws_id,
                    knowledge_base_id=kb.id,
                    question=q.question,
                    answer=q.answer,
                    citations_json=[],
                    abstained=q.abstained,
                )
                row.created_at = _ago(q.days_ago)
                session.add(row)

            print(f"created {p.status}: {p.name} — {len(p.artifacts)} artifacts, {len(p.qa)} Q&A")

        print("demo seed complete.")


if __name__ == "__main__":
    seed_demo()
