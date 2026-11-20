#!/usr/bin/env bash
# The repo's checks. Exit 0 only when the branch is fit to review.
#
# The gate runs this script from the main checkout with the job's worktree as
# the working directory. Keep it fast, deterministic and non-interactive: no
# watch modes, no prompts, nothing that rewrites files.
set -euo pipefail

# Backend: FastAPI + SQLModel. A fresh worktree has no venv (.venv is gitignored).
cd backend
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt pytest
fi
.venv/bin/python -m pytest -q
cd ..

# Frontend: Nuxt 3 SPA. No lint or unit tests; typecheck/build is the check.
cd frontend
if [ ! -d node_modules ]; then
  npm ci --prefer-offline --no-audit --no-fund
fi
npx nuxt build
