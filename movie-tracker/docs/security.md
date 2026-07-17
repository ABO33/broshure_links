# Security

Phase 1 security defaults include environment-only secrets, allowlisted CORS origins,
non-root containers, sanitized structured logging, no request-body logging, restricted HTTP
methods/headers, and an unprivileged mobile environment containing only `EXPO_PUBLIC_*`
values.

Never place database URLs, TMDB tokens, JWT secrets, or the Supabase service-role key in the
mobile app. Phase 2 will validate Supabase JWTs server-side, derive user identity from the
token, create ownership-aware dependencies, and introduce RLS policies. Authorization must
remain enforced in the API and database even when mobile UI controls are hidden.

## Dependency audit note

As of 2026-07-17, `npm audit --omit=dev` reports a moderate `uuid <11.1.1` advisory
through Expo's `@expo/config-plugins -> xcode -> uuid` toolchain. The affected UUID v3/v5/v6
buffer APIs are not called by `xcode` (it calls v4), and npm's proposed automatic fix
downgrades `expo-splash-screen` across SDK boundaries. The repository therefore does not
apply that breaking fix or force an unsupported transitive override. Recheck after each Expo
patch release and remove this note when upstream resolves the chain.
