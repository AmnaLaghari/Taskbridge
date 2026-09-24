# Taskbridge

Bidirectional sync between GitHub Issues and Todoist — built around everything that goes
wrong when two systems that don't know about each other have to agree: duplicate
deliveries, dropped webhooks, sync loops, and conflicting edits.

> Work in progress. Architecture, design decisions and failure-mode table land as the
> sync core is built. See [DECISIONS.md](DECISIONS.md) for the running log.

## Stack

| Layer      | Choice                                          |
| ---------- | ----------------------------------------------- |
| API        | Django 5.2 LTS, Django REST Framework           |
| Storage    | PostgreSQL 16                                   |
| Jobs       | Celery + Redis 7 (Beat for reconciliation)      |
| HTTP       | httpx                                           |
| Tests      | pytest, pytest-django, respx                    |
| Tooling    | uv, ruff, mypy (strict), pre-commit, GitHub Actions |
| Dashboard  | Next.js (planned)                               |

## Run it

Requires Docker.

```bash
docker compose up --build
```

Then check <http://localhost:8000/health>:

```json
{"status": "ok", "database": "ok", "redis": "ok"}
```

## Develop locally

Requires [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you).

```bash
cp .env.example .env
docker compose up -d postgres redis
cd backend
uv sync
uv run pytest
uv run ruff check . && uv run mypy .
```

### Without Docker

Postgres 16 and Redis can also run as native services — on Windows via
`winget install PostgreSQL.PostgreSQL.16` and `winget install Memurai.MemuraiDeveloper`
(Memurai speaks the Redis protocol). Create the role and database once:

```bash
psql -U postgres -c "CREATE ROLE taskbridge WITH LOGIN PASSWORD 'taskbridge' CREATEDB;"
psql -U postgres -c "CREATE DATABASE taskbridge OWNER taskbridge;"
```

Then point `DATABASE_URL` and `REDIS_URL` at `localhost` and skip `docker compose`.

Install the git hooks once from the repo root:

```bash
uv run --directory backend pre-commit install
```

## Try it against a real repository

Set `GITHUB_TOKEN` (a fine-grained PAT with Issues read/write) and `GITHUB_REPO`
(`owner/repo`) in `.env`, then poll once:

```bash
cd backend
uv run python manage.py sync_now github
```

```
created connection for github:owner/sandbox
polling github:owner/sandbox…
fetched 3 · created 3 · updated 0 · unchanged 0
```

Run it again without changing anything and everything reports as unchanged — an
issue whose synced fields still hash the same is never written twice.

`--reset-cursor` forgets where the last poll got to and reads the repository from the
beginning; `--account owner/other-repo` polls a different repository.

### Watching it work

The Django admin is the operator view: canonical tasks, their links to each provider,
inbox deliveries, pending outbox operations and dead letters.

```bash
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Then open <http://localhost:8000/admin/>. Tasks are read-only there — a canonical task
with no provider behind it would be a change nobody asked for.

## Layout

```
backend/
  taskbridge/   Django project: settings, urls, Celery app
  sync/         the sync core (models, providers, engine)
  tests/
frontend/       Next.js dashboard (planned)
```

## License

MIT
