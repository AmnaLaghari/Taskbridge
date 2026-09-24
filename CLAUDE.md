# CLAUDE.md

Instructions for Claude Code working in this repository.

## What this project is

Taskbridge syncs GitHub Issues and Todoist tasks in both directions. The interesting part
is not CRUD — it is correctness under failure: duplicate webhook deliveries, dropped
deliveries, sync loops, partial writes, and conflicting edits on both sides.

This is a portfolio project. The author must be able to defend every line at a whiteboard.
Prefer the clear implementation over the clever one, and explain trade-offs rather than
hiding them.

## Ground rules

### Git

- **I do all the committing. You never run `git commit`** — not even when I approve the
  work, not even "so the branch is clean". Leave the changes in the working tree and tell
  me what is there.
- **Never `git push`, force-push, open a PR, or merge.** Those are mine too.
- Never `git reset --hard`, `git checkout --`, `git clean`, or discard uncommitted work
  without asking first.
- Read-only git is fine without asking: `status`, `diff`, `log`, `branch`, `show`, `fetch`.
- Creating a branch and rebasing onto `main` are fine when I ask for them; they do not
  write history I have not seen.
- **Never commit directly to `main`.** Every piece of work gets its own branch, created
  from an up-to-date `main`:
  - `feat/<slug>` — new behaviour (`feat/canonical-model`, `feat/outbox-worker`)
  - `fix/<slug>` — bug fixes
  - `docs/<slug>` — documentation and plans
  - `chore/<slug>` — tooling, dependencies, CI
- One branch per feature, matching a day in `docs/PLAN-WEEK1.md` where possible. If a task
  turns out to cover two features, say so and propose splitting it.
- Before starting new work, check the current branch. If it is `main`, or still holds the
  last feature, create a fresh branch rather than adding to it.
- `main` only moves through merged PRs — and only when I ask for the merge.

The loop for every piece of work, and who does what:

| Step | Who |
| --- | --- |
| 1. Start from a current `main`, create `feat/<slug>` | you |
| 2. Build it, with tests, until ruff, mypy and pytest pass | you |
| 3. Report what changed and offer a commit message | you |
| 4. Commit, push, open the PR, merge it, delete the branch | me |
| 5. Tell you it is merged so you can rebase or start the next branch | me |

Never add a second feature to a branch whose PR is already open.

When the work is ready, hand me: the list of files changed, a one-line summary, and a
suggested commit message I can paste — imperative subject under 72 characters, then a
body explaining *why*, ending with the Co-Authored-By trailer.

### Environment and secrets

- **Never read, print, edit or commit `.env`.** It holds real API tokens.
  If a value is needed, tell me which key to set — do not fill it in for me.
- If you need to know whether a key is set, check that it is non-empty; never echo values.
- When a new setting is introduced, add it to `.env.example` with an empty or placeholder
  value and document it in the README.
- No secrets, tokens or personal repo names in code, tests, fixtures or logs.
- Never send data to a third-party API to "test" something without telling me first. Calls
  to GitHub and Todoist write to real accounts.

### Database

- Schema changes go through Django migrations. Never edit the database by hand and never
  edit an applied migration.
- Never run `flush`, `reset_db`, `DROP`, or delete rows outside a test database.
- Prefer letting Postgres enforce invariants (unique constraints, foreign keys, check
  constraints) over enforcing them in Python.

### Dependencies

- Ask before adding a dependency, and say what it replaces or why the standard library
  is not enough.
- Add it with `uv add`, never by hand-editing `pyproject.toml`, and commit `uv.lock`.

## How to work here

- Every behaviour change needs a test. The failure tests (retries, duplicate deliveries,
  loops, crashes mid-operation) are the point of this project, not extras.
- Run before handing anything back:
  ```
  cd backend
  uv run ruff check . && uv run ruff format . && uv run mypy . && uv run pytest
  ```
- mypy is strict. No `Any` in new code, no `# type: ignore` without a comment saying why.
- Never weaken a check (skip a test, relax mypy, add a broad `noqa`) to make the suite
  pass. Report the failure instead.
- After any design decision with a real alternative, add an entry to `DECISIONS.md`:
  what was chosen, what else was considered, why, and the trade-off accepted.
  `DECISIONS.md` and `docs/PLAN-WEEK1.md` are working notes for me alone — they are
  excluded from git via `.git/info/exclude`. Keep them updated, never commit them, and
  never reference them from committed files.
- Keep `sync/providers/` free of engine logic and the engine free of provider details.
  Adding a third provider should mean one new file plus a registry entry.
- Don't tell me something works if it has not been run. If it is untested, say so.

## Environment notes

- Windows. Postgres 16 and Memurai (Redis-compatible) run as local Windows services;
  Docker is not usable on this machine until virtualization is enabled in the BIOS.
- `docker-compose.yml` must keep working for anyone cloning the repo — it is the
  documented way in. Do not let it rot just because it is unused locally.
- Python is managed by uv. Always run commands through `uv run` from `backend/`.

## Commands

| Task          | Command                                        |
| ------------- | ---------------------------------------------- |
| Install deps  | `uv sync`                                      |
| Dev server    | `uv run python manage.py runserver`            |
| Migrations    | `uv run python manage.py makemigrations` / `migrate` |
| Tests         | `uv run pytest`                                |
| Lint + types  | `uv run ruff check . && uv run mypy .`         |
| Worker        | `uv run celery -A taskbridge worker -B -l info` |

All run from `backend/`.
