# API

Phase 1 endpoints:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Liveness; does not depend on PostgreSQL |
| GET | `/health/ready` | Readiness; executes `SELECT 1` |
| GET | `/docs` | Development OpenAPI UI |

The readiness failure response is HTTP 503 and does not expose connection strings or driver
errors. Product endpoints will be introduced below `/api/v1` in later phases.
