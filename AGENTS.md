# CardVault

FastAPI + SQLModel + SQLite backend (`backend/`) and a Nuxt 3 + Vue 3 + Vuetify SPA (`frontend/`). See `README.md` for the product flow.

## Fresh checkout

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest && cp .env.example .env
cd ../frontend && npm ci
```

Demo mode mocks card identification and eBay, so no keys are needed. Never commit `.env`, `*.db` or `backend/data/`. The root `.venv` has no pytest; use `backend/.venv`.

UI reviewers need the `agent-browser` CLI to take real screenshots: `npm install -g agent-browser`. Without it they can only read the code.

## Dev servers

```bash
cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev -- --port <port>   # proxies /api and /images to the backend on 8000
```

## Validation (same as `factory/checks.sh`)

```bash
cd backend && .venv/bin/python -m pytest -q
cd frontend && npx nuxt build      # typecheck/build; there are no frontend unit tests or linter
```

Follow the existing code style and keep diffs small. Add backend pytest tests for new behaviour.

## Software factory

`/factory run` builds every open GitHub issue labeled `factory` through
intake → spec → build → gate → review → approve, one PR per issue. State is in
`factory/jobs/` (gitignored); `factory/bin/job` is its only writer. Several windows can run it at once (per-job
leases); `/factory solo` works one job in a single agent; `/factory review` finds
where the pipeline churns. Checks:
`factory/checks.sh`. Dashboard: `cd factory && python3 -m http.server`, then
open http://localhost:8000/dashboard.html.

Each job works in its own git worktree, a full checkout of the repo at
`.worktrees/<job-id>` (gitignored). A tool run from the repo root that doesn't
read `.gitignore` sees those copies too; exclude `.worktrees/` in its config.
