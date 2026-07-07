# Continuity — Backend (kt-platform-api)

> **AI-powered knowledge transfer.** When an employee leaves, most of what they know leaves with
> them — the *why* behind decisions, the gotchas, the "ask X about Y". Continuity captures that
> knowledge from the tools they used **and** from their own words, and turns it into a
> **queryable, cited knowledge asset** their successor and team can keep asking questions of for
> months after they're gone.

This repository is the **FastAPI backend** — it owns everything substantial: connectors,
ingestion, retrieval-augmented generation (RAG), the interview engine, synthesis, evaluation, and
the grounded-answers contract. The [frontend lives in **kt-platform-web**](https://github.com/M-adeelnasir/kt-platform-web).

---

## What problem does it solve?

Knowledge-worker attrition quietly destroys institutional knowledge. Documentation goes stale, and
the valuable part lives in people's heads and scattered across their tools. Continuity is built
around one insight: **don't build another documentation tool — build a system that produces a
queryable "digital successor"** that answers questions in natural language, grounded in real
sources, after the person has left.

It's aimed at any knowledge-worker team (engineering, product, data, sales, HR), so ingestion is a
**connector framework**, not a fixed set of integrations.

## The four pillars (how it works)

1. **Ingest** — pull in the artifacts a person touched (code, docs, tickets, chat, email) through
   pluggable connectors, and normalize them.
2. **Elicit** — an AI interviewer asks *grounded, specific* questions to extract the tacit
   knowledge the artifacts don't contain (by text, voice, or video). *This is the moat.*
3. **Synthesize** — turn raw inputs + interviews into structured knowledge (system overview,
   gotchas & landmines, glossary, runbook, contacts) and detect knowledge gaps.
4. **Serve** — an **oracle** that answers questions with citations and **abstains when the answer
   isn't in the sources** (it never guesses), plus a successor onboarding path and exports.

## Key features

- **Grounded-answers contract** — every oracle answer is generated only from retrieved sources,
  cites them, and explicitly says when something isn't covered. A confidently wrong answer is the
  worst failure mode for knowledge transfer, so this is enforced in the AI layer and measured by
  the eval harness.
- **Connectors** — GitHub, Google Workspace (Drive/Docs/Gmail), Atlassian (Jira/Confluence), and
  Microsoft 365 (Outlook/OneDrive/SharePoint/Teams), behind one `Connector` interface. New sources
  are plugins; tokens are encrypted at rest.
- **AI interview engine** — grounded, gap-driven questions; answers are indexed back into the
  knowledge base as first-class sources.
- **Synthesis + gap detector** — regenerates structured artifacts with a fact-check pass and
  surfaces what's missing.
- **Abstention → gap loop** — questions the oracle *couldn't* answer become the next interview.
- **Insights** — workspace dashboard, usage analytics, and an org-wide **knowledge-risk
  (bus-factor)** view that flags single points of failure before they leave.
- **Eval harness** — a golden Q&A set over a fixture corpus scores groundedness, citation
  correctness, abstention, and retrieval recall, so retrieval/prompt/model changes don't silently
  regress answer quality.
- **Multi-tenant seam from day one** — every row carries a `workspace_id` and vectors live in one
  namespace, so single-workspace → multi-org later is additive, not a rewrite.

## Architecture

```
Next.js frontend ──HTTPS──▶ FastAPI backend ──▶ Postgres (domain model, via repositories)
                                    │           ▶ Pinecone (chunk embeddings, workspace namespace)
                                    │           ▶ Redis + Celery (ingest / synthesis / sync jobs)
                                    └──────────▶ Ollama (LLM + embeddings; swappable for hosted)
```

Models sit behind `LLMProvider` / `Embedder` interfaces — the MVP runs free/local on Ollama
(`qwen2.5:7b` + `nomic-embed-text`), and swapping to a hosted model (OpenAI/Anthropic) is an
adapter change with no re-indexing.

## Tech stack

FastAPI · Python 3.12 · Pydantic · SQLAlchemy 2.0 + Alembic · Postgres · Pinecone · Celery + Redis
· Ollama · `uv` · `ruff` + `mypy` + `pytest`.

## Repository layout

```
app/          FastAPI app, routers, request/workspace dependencies, Pydantic schemas
ai/           LLMProvider + Embedder interfaces, chunking, RAG, grounded-answer contract
connectors/   connector framework + GitHub / Google / Atlassian / Microsoft plugins
core/         domain services: ingest, interview, synthesis, export, dashboard, insights, onboarding
data/         SQLAlchemy models, Alembic migrations, repositories (the ONLY place that hits Postgres)
vector/       Pinecone client wrapper (namespace-scoped upsert / query / delete)
jobs/         Celery tasks (sync, ingest, synthesize)
evals/        fixture corpus + golden Q&A + eval runner
config/       pydantic-settings configuration
scripts/      database + demo seed scripts
```

## Getting started

Prerequisites: Postgres, Redis, and [Ollama](https://ollama.com) running locally, plus `uv`.

```bash
# 1) Install deps
uv sync

# 2) Configure (copy the example and fill in values)
cp .env.example .env

# 3) Pull local models
ollama pull qwen2.5:7b
ollama pull nomic-embed-text:latest

# 4) Database
uv run python -m scripts.create_database
uv run alembic upgrade head
uv run python -m data.seed

# 5) Run
uv run uvicorn app.main:app --reload --port 8000                    # API -> http://localhost:8000
uv run celery -A jobs.celery_app worker --loglevel=info --pool=solo # background jobs (2nd terminal)

# (optional) rich demo data:
uv run python -m scripts.seed_demo && uv run python -m scripts.seed_activity
```

Interactive API docs: `http://localhost:8000/docs`. (`--pool=solo` is required for Celery on
Windows.)

## Quality gates

```bash
uv run ruff check . && uv run mypy . && uv run pytest
```

> All configuration and secrets are read via `pydantic-settings` from `.env` (never committed);
> connector tokens are encrypted at rest.
