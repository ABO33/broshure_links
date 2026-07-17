# Migration source of truth

Alembic migrations in `services/api/alembic/versions` are the only schema source of truth.
This directory is reserved for Supabase-specific operational SQL, such as RLS policy scripts,
that Alembic will invoke or document in Phase 2. Do not duplicate application table creation
in both locations.
