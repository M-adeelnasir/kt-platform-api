"""Knowledge-transfer report export (plan §13: export KB to Markdown/PDF).

Compiles a departing person's synthesized knowledge (overview / gotchas / glossary / gaps) into a
portable handover document — as Markdown, or a styled, print-ready HTML page (browser "Save as
PDF" gives a clean PDF without a heavy server-side PDF dependency).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

import markdown as md
from sqlalchemy.orm import Session

from data.models import KnowledgeItem
from data.repositories import (
    EmployeeRepository,
    KnowledgeBaseRepository,
    KnowledgeItemRepository,
)

ARTIFACT_ORDER = ["overview", "gotcha", "glossary", "gap"]


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "report"


def _ordered(items: list[KnowledgeItem]) -> list[KnowledgeItem]:
    return sorted(
        items,
        key=lambda i: ARTIFACT_ORDER.index(i.kind) if i.kind in ARTIFACT_ORDER else 99,
    )


def build_markdown(session: Session, workspace_id: uuid.UUID, kb_id: uuid.UUID) -> tuple[str, str]:
    """Return (slug, markdown_text) for the KB. Raises ValueError if the KB is missing."""
    kb = KnowledgeBaseRepository(session, workspace_id).get(kb_id)
    if kb is None:
        raise ValueError("knowledge base not found")
    subject = kb.subject_person_name
    employee = (
        EmployeeRepository(session, workspace_id).get(kb.employee_id) if kb.employee_id else None
    )
    items = _ordered(list(KnowledgeItemRepository(session, workspace_id).list_for_kb(kb_id)))

    lines: list[str] = [f"# Knowledge Transfer Report — {subject}", ""]
    meta: list[str] = []
    if employee:
        if employee.title:
            meta.append(employee.title)
        meta.append(employee.email)
        if employee.notice_end_date:
            meta.append(f"Departing {employee.notice_end_date.date().isoformat()}")
    if meta:
        lines += ["**" + " · ".join(meta) + "**", ""]
    lines += [f"_Generated {datetime.now(UTC).strftime('%Y-%m-%d')} · Continuity_", ""]

    if not items:
        lines += ["> No synthesized knowledge yet — run synthesis on this knowledge base first."]
    for it in items:
        lines += [f"## {it.title}", "", it.body.strip(), ""]

    return _slug(subject), "\n".join(lines)


def build_html(session: Session, workspace_id: uuid.UUID, kb_id: uuid.UUID) -> tuple[str, str]:
    slug, md_text = build_markdown(session, workspace_id, kb_id)
    body = md.markdown(md_text, extensions=["extra", "sane_lists", "nl2br"])
    return slug, _HTML_TEMPLATE.replace("{{BODY}}", body)


_HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Knowledge Transfer Report</title>
<style>
  :root { --accent:#0d9488; --fg:#191919; --muted:#76766f; --border:#e8e8e4; --bg:#fbfbfa; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:#3d3d3d;
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    line-height:1.65; }
  .page { max-width: 760px; margin: 0 auto; padding: 56px 40px 80px; background:#fff;
    min-height:100vh; }
  h1 { font-size: 26px; font-weight: 700; letter-spacing:-.02em; color:var(--fg); margin:0 0 4px; }
  h2 { font-size: 18px; font-weight: 650; color:var(--fg); margin: 34px 0 10px;
    padding-bottom:6px; border-bottom:2px solid var(--accent); }
  h3 { font-size: 15px; color:var(--fg); margin: 18px 0 6px; }
  p { margin: 0 0 12px; } ul,ol { margin: 0 0 12px; padding-left: 22px; } li { margin: 3px 0; }
  strong { color: var(--fg); }
  code { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: .9em;
    background:#f6f6f4; padding:1px 5px; border-radius:4px; }
  blockquote { margin:0 0 12px; padding:8px 14px; border-left:3px solid var(--accent);
    background:#f0fdfa; color:var(--muted); }
  hr { border:0; border-top:1px solid var(--border); margin:28px 0; }
  em { color: var(--muted); }
  @media print { body { background:#fff; } .page { padding: 0; max-width:none; }
    h2 { break-after: avoid; } }
</style></head>
<body><div class="page">{{BODY}}</div></body></html>
"""
