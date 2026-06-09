# Continuity — API (FastAPI backend)

The backend owns everything substantive: connectors, ingest, RAG, interview, synthesis,
evals, and the grounded-answers contract. See the root `plan.md` for the full spec.

## Layout (plan §11)

```
app/         FastAPI app, routers, dependencies (auth / workspace context)
ai/          LLMProvider + Embedder interfaces, RAG, interview, synthesis, grounding
connectors/  connector framework + plugins (github / google / notion / upload / webhook)
core/        domain services: ingest, elicit, synthesize, serve
data/        SQLAlchemy models, Alembic, repositories (the ONLY place that hits Postgres)
vector/      Pinecone client wrapper (namespace-scoped upsert/query/delete)
jobs/        Celery tasks
evals/       fixture corpus + golden Q&A + eval runner (CI)
config/      pydantic-settings config (env, provider/model config)
```

## Dev setup

Prereqs (already verified locally): Python 3.12, PostgreSQL 18 on `localhost:5432`,
Redis on `localhost:6379`, Ollama with `qwen2.5:7b` + `nomic-embed-text:latest`.

```bash
cd api
uv sync                       # install deps into .venv
cp .env.example .env          # then fill in DATABASE_URL etc.

# Create the dev database (uses your local Postgres superuser):
#   createdb continuity        (or via psql: CREATE DATABASE continuity;)

uv run alembic upgrade head   # create the schema
uv run python -m data.seed    # seed the single workspace + default member
uv run uvicorn app.main:app --reload   # API on http://localhost:8000

# In a second terminal, the Celery worker:
uv run celery -A jobs.celery_app worker --loglevel=info --pool=solo
```

`--pool=solo` is used on Windows (the default prefork pool doesn't work there).

## Quality gates

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest
```
