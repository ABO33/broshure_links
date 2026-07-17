# Deployment

## Render

1. Push the repository to GitHub and create a Render Blueprint from `render.yaml`.
2. Add `DATABASE_URL` and `CORS_ALLOWED_ORIGINS` as secret environment values.
3. Add Supabase and TMDB variables only when their implementation phases require them.
4. Before deploying code that depends on a new schema, run `alembic upgrade head` as a
   reviewed one-off job or pre-deploy command.
5. Keep automatic destructive migrations disabled.

The container binds to Render's `PORT`, runs as a non-root user, and exposes `/health` for
the platform health check. Use conservative worker and pool counts until production traffic
and Supabase connection limits are measured.

## EAS from Windows

Use Expo Go for the Phase 1 JavaScript-only application. After native dependencies require
it, run `eas build --profile development --platform android` or `--platform ios`. EAS builds
iOS in the cloud; an iOS Simulator cannot run locally on Windows. Replace the example bundle
and package identifiers and add original icon/splash assets before any distributable build.
