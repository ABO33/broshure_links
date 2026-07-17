# Movie Tracker API

Phase 1 provides the FastAPI runtime, async PostgreSQL connection, health checks,
Alembic foundation, structured logging, tests, and deployment container.

From this directory:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

Run checks with `pytest`, `ruff check .`, `ruff format --check .`, and `mypy app`.
