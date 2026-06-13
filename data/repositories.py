"""Workspace-scoped repositories — the only sanctioned way to read/write Postgres.

Every scoped repository takes a `workspace_id` in its constructor and injects it into every
query, so no caller can accidentally cross the tenant boundary (plan §3, §10). Raw queries
elsewhere in the codebase are forbidden.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from data.models import (
    AuditLog,
    Chunk,
    Document,
    Employee,
    Interview,
    InterviewMessage,
    KnowledgeBase,
    KnowledgeItem,
    Member,
    QaQuery,
    Source,
    Workspace,
)


class WorkspaceRepository:
    """Workspace itself is not workspace-scoped (it IS the workspace), so it stands apart.

    In the single-workspace MVP, `get_default()` returns the one seeded row.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, workspace_id: uuid.UUID) -> Workspace | None:
        return self._session.get(Workspace, workspace_id)

    def get_default(self) -> Workspace | None:
        """The single seeded workspace (MVP). Returns the earliest-created workspace row."""
        stmt = select(Workspace).order_by(Workspace.created_at.asc()).limit(1)
        return self._session.scalars(stmt).first()

    def list_all(self) -> Sequence[Workspace]:
        """All workspaces — for system-level background jobs (e.g. the sync poller)."""
        return self._session.scalars(select(Workspace)).all()

    def create(self, name: str, pinecone_namespace: str) -> Workspace:
        ws = Workspace(name=name, pinecone_namespace=pinecone_namespace)
        self._session.add(ws)
        self._session.flush()
        return ws


class ScopedRepository:
    """Base for all tenant-scoped repositories. Binds a session + a mandatory workspace_id."""

    def __init__(self, session: Session, workspace_id: uuid.UUID) -> None:
        self._session = session
        self.workspace_id = workspace_id


class MemberRepository(ScopedRepository):
    def get_by_external_user_id(self, external_user_id: str) -> Member | None:
        stmt = select(Member).where(
            Member.workspace_id == self.workspace_id,
            Member.external_user_id == external_user_id,
        )
        return self._session.scalars(stmt).first()

    def get_default(self) -> Member | None:
        """The earliest-created member of this workspace (the seeded MVP member)."""
        stmt = (
            select(Member)
            .where(Member.workspace_id == self.workspace_id)
            .order_by(Member.created_at.asc())
            .limit(1)
        )
        return self._session.scalars(stmt).first()

    def upsert(
        self,
        external_user_id: str,
        role: str = "member",
        name: str | None = None,
        email: str | None = None,
    ) -> Member:
        existing = self.get_by_external_user_id(external_user_id)
        if existing is not None:
            return existing
        member = Member(
            workspace_id=self.workspace_id,
            external_user_id=external_user_id,
            role=role,
            name=name,
            email=email,
        )
        self._session.add(member)
        self._session.flush()
        return member

    def list(self) -> Sequence[Member]:
        stmt = select(Member).where(Member.workspace_id == self.workspace_id)
        return self._session.scalars(stmt).all()


class KnowledgeBaseRepository(ScopedRepository):
    def list(self) -> Sequence[KnowledgeBase]:
        stmt = (
            select(KnowledgeBase)
            .where(KnowledgeBase.workspace_id == self.workspace_id)
            .order_by(KnowledgeBase.created_at.desc())
        )
        return self._session.scalars(stmt).all()

    def get(self, kb_id: uuid.UUID) -> KnowledgeBase | None:
        stmt = select(KnowledgeBase).where(
            KnowledgeBase.workspace_id == self.workspace_id,
            KnowledgeBase.id == kb_id,
        )
        return self._session.scalars(stmt).first()

    def get_by_employee(self, employee_id: uuid.UUID) -> KnowledgeBase | None:
        stmt = select(KnowledgeBase).where(
            KnowledgeBase.workspace_id == self.workspace_id,
            KnowledgeBase.employee_id == employee_id,
        )
        return self._session.scalars(stmt).first()

    def create(
        self, subject_person_name: str, employee_id: uuid.UUID | None = None
    ) -> KnowledgeBase:
        kb = KnowledgeBase(
            workspace_id=self.workspace_id,
            subject_person_name=subject_person_name,
            employee_id=employee_id,
        )
        self._session.add(kb)
        self._session.flush()
        return kb


class AuditLogRepository(ScopedRepository):
    def record(
        self,
        *,
        action: str,
        actor_member_id: uuid.UUID | None = None,
        target: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            workspace_id=self.workspace_id,
            actor_member_id=actor_member_id,
            action=action,
            target=target,
            metadata_json=metadata or {},
        )
        self._session.add(entry)
        self._session.flush()
        return entry


class EmployeeRepository(ScopedRepository):
    VALID_STATUSES = ("active", "on_notice", "departed")

    def list(self) -> Sequence[Employee]:
        stmt = (
            select(Employee)
            .where(Employee.workspace_id == self.workspace_id)
            .order_by(Employee.created_at.desc())
        )
        return self._session.scalars(stmt).all()

    def get(self, employee_id: uuid.UUID) -> Employee | None:
        stmt = select(Employee).where(
            Employee.workspace_id == self.workspace_id, Employee.id == employee_id
        )
        return self._session.scalars(stmt).first()

    def get_by_email(self, email: str) -> Employee | None:
        stmt = select(Employee).where(
            Employee.workspace_id == self.workspace_id, Employee.email == email
        )
        return self._session.scalars(stmt).first()

    def create(
        self,
        *,
        name: str,
        email: str,
        title: str | None = None,
        github_username: str | None = None,
        status: str = "active",
        connectors: Sequence[str] | None = None,
    ) -> Employee:
        employee = Employee(
            workspace_id=self.workspace_id,
            name=name,
            email=email,
            title=title,
            github_username=github_username,
            status=status,
            connectors=list(connectors) if connectors is not None else [],
        )
        self._session.add(employee)
        self._session.flush()
        return employee

    def update(
        self,
        employee: Employee,
        *,
        name: str | None = None,
        email: str | None = None,
        title: str | None = None,
        github_username: str | None = None,
        connectors: Sequence[str] | None = None,
    ) -> Employee:
        """Patch the provided fields (None means 'leave unchanged')."""
        if name is not None:
            employee.name = name
        if email is not None:
            employee.email = email
        if title is not None:
            employee.title = title
        if github_username is not None:
            employee.github_username = github_username
        if connectors is not None:
            employee.connectors = list(connectors)
        self._session.flush()
        return employee


class SourceRepository(ScopedRepository):
    def create(
        self, knowledge_base_id: uuid.UUID, type: str, config: dict[str, object] | None = None
    ) -> Source:
        source = Source(
            workspace_id=self.workspace_id,
            knowledge_base_id=knowledge_base_id,
            type=type,
            config_json=config or {},
        )
        self._session.add(source)
        self._session.flush()
        return source

    def get(self, source_id: uuid.UUID) -> Source | None:
        stmt = select(Source).where(
            Source.workspace_id == self.workspace_id, Source.id == source_id
        )
        return self._session.scalars(stmt).first()

    def list_for_kb(self, knowledge_base_id: uuid.UUID) -> Sequence[Source]:
        stmt = select(Source).where(
            Source.workspace_id == self.workspace_id,
            Source.knowledge_base_id == knowledge_base_id,
        )
        return self._session.scalars(stmt).all()

    def list_connected(self) -> Sequence[Source]:
        """Sources that have stored tokens (i.e. ready to sync) — for the poller."""
        stmt = select(Source).where(
            Source.workspace_id == self.workspace_id,
            Source.encrypted_tokens.isnot(None),
        )
        return self._session.scalars(stmt).all()

    def list_all(self) -> Sequence[Source]:
        """All sources in the workspace (for dashboard connector aggregation)."""
        stmt = select(Source).where(Source.workspace_id == self.workspace_id)
        return self._session.scalars(stmt).all()

    def get_or_create(self, knowledge_base_id: uuid.UUID, type: str) -> Source:
        """Find-or-create the single source of `type` for a knowledge base (e.g. 'upload')."""
        stmt = select(Source).where(
            Source.workspace_id == self.workspace_id,
            Source.knowledge_base_id == knowledge_base_id,
            Source.type == type,
        )
        existing = self._session.scalars(stmt).first()
        if existing is not None:
            return existing
        return self.create(knowledge_base_id=knowledge_base_id, type=type)


class DocumentRepository(ScopedRepository):
    def create(
        self,
        source_id: uuid.UUID,
        external_id: str,
        text: str,
        title: str | None = None,
        author: str | None = None,
        url: str | None = None,
    ) -> Document:
        doc = Document(
            workspace_id=self.workspace_id,
            source_id=source_id,
            external_id=external_id,
            title=title,
            author=author,
            url=url,
            text=text,
        )
        self._session.add(doc)
        self._session.flush()
        return doc

    def get(self, document_id: uuid.UUID) -> Document | None:
        stmt = select(Document).where(
            Document.workspace_id == self.workspace_id, Document.id == document_id
        )
        return self._session.scalars(stmt).first()

    def get_by_external(self, source_id: uuid.UUID, external_id: str) -> Document | None:
        stmt = select(Document).where(
            Document.workspace_id == self.workspace_id,
            Document.source_id == source_id,
            Document.external_id == external_id,
        )
        return self._session.scalars(stmt).first()

    def list_for_kb(self, knowledge_base_id: uuid.UUID) -> Sequence[Document]:
        """All documents belonging to a KB (joined through their sources). Used to give the
        interview engine material to ground questions on.
        """
        stmt = (
            select(Document)
            .join(Source, Document.source_id == Source.id)
            .where(
                Document.workspace_id == self.workspace_id,
                Source.knowledge_base_id == knowledge_base_id,
            )
            .order_by(Document.created_at.desc())
        )
        return self._session.scalars(stmt).all()


class ChunkRepository(ScopedRepository):
    def add(
        self, document_id: uuid.UUID, ordinal: int, text: str, token_count: int | None = None
    ) -> Chunk:
        chunk = Chunk(
            workspace_id=self.workspace_id,
            document_id=document_id,
            ordinal=ordinal,
            text=text,
            token_count=token_count,
        )
        self._session.add(chunk)
        self._session.flush()
        return chunk

    def get_many(self, chunk_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Chunk]:
        """Fetch chunks by id (workspace-scoped), keyed by id for easy lookup after retrieval."""
        if not chunk_ids:
            return {}
        stmt = select(Chunk).where(
            Chunk.workspace_id == self.workspace_id, Chunk.id.in_(list(chunk_ids))
        )
        return {c.id: c for c in self._session.scalars(stmt).all()}


class QaQueryRepository(ScopedRepository):
    def record(
        self,
        *,
        knowledge_base_id: uuid.UUID,
        question: str,
        answer: str | None,
        citations: list[dict[str, object]],
        abstained: bool,
    ) -> QaQuery:
        row = QaQuery(
            workspace_id=self.workspace_id,
            knowledge_base_id=knowledge_base_id,
            question=question,
            answer=answer,
            citations_json=citations,
            abstained=abstained,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def list_all(self) -> Sequence[QaQuery]:
        """All oracle interactions in the workspace (newest first) — feeds analytics."""
        stmt = (
            select(QaQuery)
            .where(QaQuery.workspace_id == self.workspace_id)
            .order_by(QaQuery.created_at.desc())
        )
        return self._session.scalars(stmt).all()

    def list_for_kb(self, kb_id: uuid.UUID, *, abstained_only: bool = False) -> Sequence[QaQuery]:
        """Oracle interactions for one KB (newest first). `abstained_only` for gap detection."""
        conds = [QaQuery.workspace_id == self.workspace_id, QaQuery.knowledge_base_id == kb_id]
        if abstained_only:
            conds.append(QaQuery.abstained.is_(True))
        stmt = select(QaQuery).where(*conds).order_by(QaQuery.created_at.desc())
        return self._session.scalars(stmt).all()


class InterviewRepository(ScopedRepository):
    def create(self, knowledge_base_id: uuid.UUID) -> Interview:
        iv = Interview(workspace_id=self.workspace_id, knowledge_base_id=knowledge_base_id)
        self._session.add(iv)
        self._session.flush()
        return iv

    def get(self, interview_id: uuid.UUID) -> Interview | None:
        stmt = select(Interview).where(
            Interview.workspace_id == self.workspace_id, Interview.id == interview_id
        )
        return self._session.scalars(stmt).first()

    def list_for_kb(self, knowledge_base_id: uuid.UUID) -> Sequence[Interview]:
        stmt = (
            select(Interview)
            .where(
                Interview.workspace_id == self.workspace_id,
                Interview.knowledge_base_id == knowledge_base_id,
            )
            .order_by(Interview.created_at.desc())
        )
        return self._session.scalars(stmt).all()


class KnowledgeItemRepository(ScopedRepository):
    def create(
        self,
        *,
        knowledge_base_id: uuid.UUID,
        kind: str,
        title: str,
        body: str,
        source_refs: list[dict[str, object]] | None = None,
    ) -> KnowledgeItem:
        item = KnowledgeItem(
            workspace_id=self.workspace_id,
            knowledge_base_id=knowledge_base_id,
            kind=kind,
            title=title,
            body=body,
            source_refs_json=source_refs or [],
        )
        self._session.add(item)
        self._session.flush()
        return item

    def list_for_kb(self, knowledge_base_id: uuid.UUID) -> Sequence[KnowledgeItem]:
        stmt = (
            select(KnowledgeItem)
            .where(
                KnowledgeItem.workspace_id == self.workspace_id,
                KnowledgeItem.knowledge_base_id == knowledge_base_id,
            )
            .order_by(KnowledgeItem.created_at.asc())
        )
        return self._session.scalars(stmt).all()

    def delete_for_kb(self, knowledge_base_id: uuid.UUID) -> None:
        """Clear prior synthesis so a re-run regenerates fresh."""
        for item in self.list_for_kb(knowledge_base_id):
            self._session.delete(item)
        self._session.flush()


class InterviewMessageRepository(ScopedRepository):
    def add(self, interview_id: uuid.UUID, role: str, content: str) -> InterviewMessage:
        msg = InterviewMessage(
            workspace_id=self.workspace_id,
            interview_id=interview_id,
            role=role,
            content=content,
        )
        self._session.add(msg)
        self._session.flush()
        return msg

    def list(self, interview_id: uuid.UUID) -> Sequence[InterviewMessage]:
        stmt = (
            select(InterviewMessage)
            .where(
                InterviewMessage.workspace_id == self.workspace_id,
                InterviewMessage.interview_id == interview_id,
            )
            .order_by(InterviewMessage.created_at.asc())
        )
        return self._session.scalars(stmt).all()
