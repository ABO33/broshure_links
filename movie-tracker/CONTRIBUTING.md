# Contributing

Create focused branches using the `codex/` prefix, keep secrets out of commits, and update
documentation with behavior changes. Work phase by phase; do not merge a phase until its
tests, linting, type checks, and startup checks pass.

## Required checks

```powershell
cd services/api
ruff check .
ruff format --check .
mypy app
pytest

cd ../../apps/mobile
npm run lint
npm run typecheck
npm test
```

Migration revisions must be additive and reviewed. Never rewrite a migration that has run
in a shared environment, and never include destructive production operations implicitly.
