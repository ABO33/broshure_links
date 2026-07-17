# Database

PostgreSQL 16 is used locally and Supabase PostgreSQL is used in hosted environments.
SQLAlchemy's async engine uses `asyncpg`, pre-ping, and deliberately small pools suitable for
Supabase/Render connection limits.

Alembic under `services/api/alembic` is the schema source of truth. Phase 1 includes a no-op
baseline so the migration pipeline can be tested before domain tables are added. Phase 2
will add profiles and RLS; Phases 3–5 add movie cache and user-library tables.

For a direct Supabase database URL, use an async-compatible `postgresql+asyncpg://` URL.
Transaction-mode poolers can conflict with prepared statements; verify the Supabase pooler
mode and configure the driver appropriately before production deployment.
