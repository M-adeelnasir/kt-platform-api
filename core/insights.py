"""Insights: analytics + knowledge-risk views computed from real workspace data.

- Analytics: oracle usage (questions, abstention, coverage, activity over time).
- Knowledge risk: per-person and per-department "bus factor" — who is a single point of
  failure and how much of their knowledge is captured. (A management view; the deeper
  org-wide risk scoring is a Phase-3 feature, but this is real, data-driven, not mocked.)
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from data.repositories import (
    DocumentRepository,
    EmployeeRepository,
    InterviewRepository,
    KnowledgeBaseRepository,
    KnowledgeItemRepository,
    QaQueryRepository,
    SourceRepository,
)

UTC = dt.UTC


# --- Unanswered questions (abstention → gap loop) ---


@dataclass
class UnansweredQuestion:
    question: str
    count: int
    last_asked: dt.datetime


def _normalize_q(q: str) -> str:
    return " ".join(q.lower().strip().rstrip("?").split())


def unanswered_questions(
    session: Session, workspace_id: uuid.UUID, kb_id: uuid.UUID
) -> list[UnansweredQuestion]:
    """Group the oracle's abstained (unanswered) questions for a KB into capture-worthy gaps.

    These are the real questions successors asked that the knowledge base couldn't answer —
    the strongest signal for what to capture next (feed them into the interview)."""
    rows = QaQueryRepository(session, workspace_id).list_for_kb(kb_id, abstained_only=True)
    groups: dict[str, UnansweredQuestion] = {}
    for r in rows:
        key = _normalize_q(r.question)
        if not key:
            continue
        g = groups.get(key)
        if g is None:
            groups[key] = UnansweredQuestion(question=r.question, count=1, last_asked=r.created_at)
        else:
            g.count += 1
            if r.created_at > g.last_asked:
                g.last_asked = r.created_at
    return sorted(groups.values(), key=lambda g: (g.count, g.last_asked), reverse=True)


# --- department inference (no department column yet; derive from title) ---

_DEPT_RULES: list[tuple[tuple[str, ...], str]] = [
    (("sre", "devops", "infra", "platform"), "Engineering"),
    (("engineer", "developer", "backend", "frontend", "full stack", "fullstack"), "Engineering"),
    (("product", "pm ", "product manager"), "Product"),
    (("data", "ml", "machine learning", "analyst", "scientist"), "Data"),
    (("sales", "account", "revenue", "growth"), "Revenue"),
    (("hr", "people", "recruit", "talent"), "People"),
    (("design", "ux", "ui"), "Design"),
]


def infer_department(title: str | None) -> str:
    t = (title or "").lower()
    for keywords, dept in _DEPT_RULES:
        if any(k in t for k in keywords):
            return dept
    return "General"


# --- Analytics ---


@dataclass
class DayPoint:
    date: str  # ISO date
    questions: int
    abstained: int


@dataclass
class TopKb:
    knowledge_base_id: uuid.UUID
    subject: str
    questions: int


@dataclass
class Analytics:
    total_questions: int
    answered: int
    abstained: int
    abstention_rate: int  # %
    kbs_total: int
    kbs_ready: int
    coverage: int  # % of KBs with synthesized knowledge
    artifacts_total: int
    interviews_total: int
    timeseries: list[DayPoint]  # last 30 days
    top_kbs: list[TopKb]


def build_analytics(session: Session, workspace_id: uuid.UUID) -> Analytics:
    queries = list(QaQueryRepository(session, workspace_id).list_all())
    total = len(queries)
    abstained = sum(1 for q in queries if q.abstained)
    answered = total - abstained
    rate = round(abstained / total * 100) if total else 0

    # 30-day activity (bucket by date).
    today = dt.datetime.now(UTC).date()
    buckets: dict[str, list[int]] = {
        (today - dt.timedelta(days=i)).isoformat(): [0, 0] for i in range(29, -1, -1)
    }
    for q in queries:
        key = q.created_at.astimezone(UTC).date().isoformat()
        if key in buckets:
            buckets[key][0] += 1
            if q.abstained:
                buckets[key][1] += 1
    timeseries = [DayPoint(date=d, questions=v[0], abstained=v[1]) for d, v in buckets.items()]

    kb_repo = KnowledgeBaseRepository(session, workspace_id)
    item_repo = KnowledgeItemRepository(session, workspace_id)
    iv_repo = InterviewRepository(session, workspace_id)
    kbs = list(kb_repo.list())
    per_kb_items = {kb.id: len(item_repo.list_for_kb(kb.id)) for kb in kbs}
    kbs_ready = sum(1 for kb in kbs if per_kb_items[kb.id] > 0)
    artifacts_total = sum(per_kb_items.values())
    interviews_total = sum(len(iv_repo.list_for_kb(kb.id)) for kb in kbs)

    q_by_kb: dict[uuid.UUID, int] = {}
    for q in queries:
        q_by_kb[q.knowledge_base_id] = q_by_kb.get(q.knowledge_base_id, 0) + 1
    subject = {kb.id: kb.subject_person_name for kb in kbs}
    top = sorted(q_by_kb.items(), key=lambda kv: kv[1], reverse=True)[:5]
    top_kbs = [
        TopKb(knowledge_base_id=kid, subject=subject.get(kid, "Unknown"), questions=n)
        for kid, n in top
    ]

    return Analytics(
        total_questions=total,
        answered=answered,
        abstained=abstained,
        abstention_rate=rate,
        kbs_total=len(kbs),
        kbs_ready=kbs_ready,
        coverage=round(kbs_ready / len(kbs) * 100) if kbs else 0,
        artifacts_total=artifacts_total,
        interviews_total=interviews_total,
        timeseries=timeseries,
        top_kbs=top_kbs,
    )


# --- Knowledge risk / bus factor ---

# capture milestones: connected source / documents / synthesized / interview (matches dashboard)
def _capture_score(
    has_source: bool, has_docs: bool, has_artifacts: bool, has_interview: bool
) -> int:
    return round(sum([has_source, has_docs, has_artifacts, has_interview]) / 4 * 100)


def _risk_level(status: str, capture: int) -> str:
    if status == "departed":
        return "critical" if capture < 50 else "medium"
    if status == "on_notice":
        if capture < 25:
            return "critical"
        if capture < 60:
            return "high"
        if capture < 100:
            return "medium"
        return "low"
    return "low"  # active


_RISK_WEIGHT = {"critical": 100, "high": 70, "medium": 40, "low": 10}


@dataclass
class RiskPerson:
    employee_id: uuid.UUID
    name: str
    title: str | None
    department: str
    status: str
    capture: int  # 0-100
    risk: str  # critical|high|medium|low
    knowledge_base_id: uuid.UUID | None


@dataclass
class DeptRisk:
    department: str
    people: int
    at_risk: int  # on_notice or departed
    avg_capture: int
    risk: str


@dataclass
class KnowledgeRisk:
    org_risk_score: int  # 0-100, higher = more risk
    critical: int
    high: int
    people_at_risk: int
    people: list[RiskPerson]
    departments: list[DeptRisk]
    spofs: list[RiskPerson] = field(default_factory=list)  # single points of failure


def build_knowledge_risk(session: Session, workspace_id: uuid.UUID) -> KnowledgeRisk:
    emp_repo = EmployeeRepository(session, workspace_id)
    kb_repo = KnowledgeBaseRepository(session, workspace_id)
    item_repo = KnowledgeItemRepository(session, workspace_id)
    src_repo = SourceRepository(session, workspace_id)
    doc_repo = DocumentRepository(session, workspace_id)
    iv_repo = InterviewRepository(session, workspace_id)

    people: list[RiskPerson] = []
    for e in emp_repo.list():
        kb = kb_repo.get_by_employee(e.id)
        capture = 0
        if kb is not None:
            has_source = len(src_repo.list_for_kb(kb.id)) > 0
            has_docs = len(doc_repo.list_for_kb(kb.id)) > 0
            has_artifacts = len(item_repo.list_for_kb(kb.id)) > 0
            has_interview = len(iv_repo.list_for_kb(kb.id)) > 0
            capture = _capture_score(has_source, has_docs, has_artifacts, has_interview)
        people.append(
            RiskPerson(
                employee_id=e.id,
                name=e.name,
                title=e.title,
                department=infer_department(e.title),
                status=e.status,
                capture=capture,
                risk=_risk_level(e.status, capture),
                knowledge_base_id=kb.id if kb else None,
            )
        )

    at_risk_people = [p for p in people if p.status in ("on_notice", "departed")]
    critical = sum(1 for p in people if p.risk == "critical")
    high = sum(1 for p in people if p.risk == "high")

    # Org risk score: average risk weight across at-risk people (0 if none at risk).
    if at_risk_people:
        org_score = round(
            sum(_RISK_WEIGHT[p.risk] for p in at_risk_people) / len(at_risk_people)
        )
    else:
        org_score = 0

    # Departments.
    by_dept: dict[str, list[RiskPerson]] = {}
    for p in people:
        by_dept.setdefault(p.department, []).append(p)
    departments: list[DeptRisk] = []
    for dept, members in sorted(by_dept.items()):
        at_risk = [m for m in members if m.status in ("on_notice", "departed")]
        avg_cap = round(sum(m.capture for m in at_risk) / len(at_risk)) if at_risk else 100
        worst = "low"
        for m in at_risk:
            if _RISK_WEIGHT[m.risk] > _RISK_WEIGHT[worst]:
                worst = m.risk
        departments.append(
            DeptRisk(
                department=dept,
                people=len(members),
                at_risk=len(at_risk),
                avg_capture=avg_cap,
                risk=worst if at_risk else "low",
            )
        )

    # SPOFs: people leaving with critical/high risk (their knowledge is concentrated + uncaptured).
    spofs = sorted(
        [p for p in at_risk_people if p.risk in ("critical", "high")],
        key=lambda p: _RISK_WEIGHT[p.risk],
        reverse=True,
    )

    people.sort(key=lambda p: _RISK_WEIGHT[p.risk], reverse=True)
    return KnowledgeRisk(
        org_risk_score=org_score,
        critical=critical,
        high=high,
        people_at_risk=len(at_risk_people),
        people=people,
        departments=departments,
        spofs=spofs,
    )
