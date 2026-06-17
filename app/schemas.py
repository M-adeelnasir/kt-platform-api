"""Pydantic API schemas (the HTTP boundary). These drive the OpenAPI doc and the generated
TypeScript client, so keep field names stable and explicit.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    status: str
    environment: str


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    external_user_id: str
    name: str | None = None
    email: str | None = None
    role: str


class WorkspaceContextOut(BaseModel):
    """Workspace-scoped data returned to a logged-in user (Phase 0 DoD endpoint)."""

    model_config = ConfigDict(from_attributes=True)

    workspace_id: uuid.UUID
    workspace_name: str
    pinecone_namespace: str
    member: MemberOut


class JobEnqueuedResponse(BaseModel):
    task_id: str


class ConnectStartResponse(BaseModel):
    auth_url: str


class JobResultResponse(BaseModel):
    task_id: str
    ready: bool
    failed: bool = False
    result: str | None = None


class KnowledgeBaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subject_person_name: str
    status: str
    created_at: datetime


class KnowledgeBaseCreate(BaseModel):
    subject_person_name: str


class EmployeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    title: str | None = None
    github_username: str | None = None
    status: str
    connectors: list[str] = []
    notice_end_date: datetime | None = None
    created_at: datetime


class KnowledgeBaseDetailOut(BaseModel):
    """A single KB plus its linked employee (for the KB workspace subject header)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subject_person_name: str
    status: str
    created_at: datetime
    employee: EmployeeOut | None = None


class EmployeeCreate(BaseModel):
    name: str
    email: str
    title: str | None = None
    github_username: str | None = None
    status: str = "active"
    connectors: list[str] = []


class EmployeeStatusUpdate(BaseModel):
    status: str


class EmployeeUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    title: str | None = None
    github_username: str | None = None
    connectors: list[str] | None = None


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    status: str
    last_synced_at: datetime | None = None


class EmployeeDetailOut(BaseModel):
    """Employee plus their knowledge base id and connected sources (for the detail view)."""

    employee: EmployeeOut
    knowledge_base_id: uuid.UUID | None = None
    sources: list[SourceOut] = []


class IngestTextRequest(BaseModel):
    title: str
    text: str
    url: str | None = None


class AskRequest(BaseModel):
    question: str


class InterviewStartResponse(BaseModel):
    interview_id: uuid.UUID
    question: str


class InterviewAnswerRequest(BaseModel):
    answer: str


class InterviewQuestionResponse(BaseModel):
    question: str


class InterviewMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: str
    content: str
    created_at: datetime


class InterviewDetailOut(BaseModel):
    id: uuid.UUID
    status: str
    messages: list[InterviewMessageOut] = []


class InterviewFinishResponse(BaseModel):
    indexed_answers: int
    chunks: int


class KnowledgeItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    title: str
    body: str
    created_at: datetime


class UnansweredQuestionOut(BaseModel):
    """A question the oracle couldn't answer (abstained), grouped — a capture-worthy gap."""

    question: str
    count: int
    last_asked: datetime


# --- Successor onboarding ---


class OnboardingStepOut(BaseModel):
    kind: str
    title: str
    intro: str
    body: str
    read_minutes: int


class OnboardingViewOut(BaseModel):
    knowledge_base_id: uuid.UUID
    subject_person_name: str
    subject_title: str | None = None
    source_count: int
    interview_count: int
    steps: list[OnboardingStepOut] = []
    total_read_minutes: int
    ready: bool


# --- Dashboard ---


class DashboardKpis(BaseModel):
    on_notice: int
    departing_soon: int  # on notice with notice_end_date within 30 days
    kbs_ready: int  # KBs that have synthesized knowledge
    kbs_total: int
    jobs_running: int
    needs_attention: int  # sources not fully connected


class DashboardPerson(BaseModel):
    employee_id: uuid.UUID
    name: str
    title: str | None = None
    notice_end_date: datetime | None = None
    knowledge_base_id: uuid.UUID | None = None
    capture_progress: int  # 0-100, milestone heuristic


class DashboardConnector(BaseModel):
    type: str
    status: str  # connected | syncing | action_needed | not_connected
    employee_count: int
    last_synced_at: datetime | None = None


class DashboardJob(BaseModel):
    id: str
    kind: str  # synthesis | sync | ingest | interview
    subject: str | None = None


class DashboardOut(BaseModel):
    kpis: DashboardKpis
    on_notice: list[DashboardPerson]
    connectors: list[DashboardConnector]
    active_jobs: list[DashboardJob]


# --- Analytics ---


class AnalyticsDayOut(BaseModel):
    date: str
    questions: int
    abstained: int


class AnalyticsTopKbOut(BaseModel):
    knowledge_base_id: uuid.UUID
    subject: str
    questions: int


class AnalyticsOut(BaseModel):
    total_questions: int
    answered: int
    abstained: int
    abstention_rate: int
    kbs_total: int
    kbs_ready: int
    coverage: int
    artifacts_total: int
    interviews_total: int
    timeseries: list[AnalyticsDayOut]
    top_kbs: list[AnalyticsTopKbOut]


# --- Knowledge risk ---


class RiskPersonOut(BaseModel):
    employee_id: uuid.UUID
    name: str
    title: str | None = None
    department: str
    status: str
    capture: int
    risk: str
    knowledge_base_id: uuid.UUID | None = None


class DeptRiskOut(BaseModel):
    department: str
    people: int
    at_risk: int
    avg_capture: int
    risk: str


class KnowledgeRiskOut(BaseModel):
    org_risk_score: int
    critical: int
    high: int
    people_at_risk: int
    people: list[RiskPersonOut]
    departments: list[DeptRiskOut]
    spofs: list[RiskPersonOut]
