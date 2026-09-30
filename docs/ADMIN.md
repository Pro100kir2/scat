# Admin panel

`https://<site>/admin/`: one place to run Скат. English UI, the site's own design system, loaded
as a separate chunk (the public pages do not grow). Never indexed (`noindex, nofollow`,
`robots.txt`, `X-Robots-Tag`), never framed.

| Section | What you can do |
|---|---|
| Dashboard | system status at a glance (🟢 healthy, or which component is broken), probe / source / scanner / cache / error counters, recent events |
| Probes | every probe with status, online, verification, profile, version, OS, CPU, RAM, storage, IP, contact, times, job counters, last error; filters (status, online, verification, profile, version, provider), search by ID or phone, sorting, pagination. Details: facts, contact (editable), verification history with each check's outcome, jobs and errors. Actions: approve, disable, enable, re-run verification, send test job, delete; dangerous ones ask first |
| Sources | add, edit, enable/disable, delete, test (download and parse, nothing stored), fetch now (stored as the last result), view last result; name, URL, kind, priority, refresh interval, timeout, last fetch / success / error, fetched / valid / unique counts |
| Scanner | status, current scan with progress, queue, last scan; start (if stale), run now, cancel, restart. One scan at a time, as before |
| Scans | history of every scan (kept 2000): times, duration, status, trigger, source / candidate / unique / tested / passed / failed counts, probes used, errors; details with per-source results and the top |
| Providers | per ISP: probes by status, online, tests, errors, heartbeats, score contribution |
| Cache | files, TTL, sizes, expiry; the **live ranking** of every candidate with its probe score (the rows marked "top" are what the public API serves); clear, rebuild, refresh |
| System | health per component (backend, database, scanner, probes, sources, cache, disk), versions (backend, frontend, scanner logic, probe agent), uptime, memory, last successful scan, recent errors |
| Logs | application events (not raw server logs), filtered by component, severity, probe, source, time, text |
| Settings | safe runtime settings, validated server-side and applied immediately; reset to the `.env` default |

## New-probe notifications

Every genuinely new probe shows a toast (🔔 New probe registered, probe ID, status, profile,
provider) with a short chime, and a red counter next to «Probes» until it is opened or marked
read. How "genuinely new" is kept exact:

- the panel polls `GET /api/admin/notifications?after=<cursor>` every 20 s **only while the tab
  is visible** (one indexed query; no SSE/WebSocket existed, and polling this cheaply needs none);
- the cursor (the last `probe_registered` event seen) is kept in `localStorage`: a reload never
  replays anything; the very first poll only sets it, so installing the panel never rings for
  old probes; heartbeats, verification and a probe staying pending never ring again;
- several probes arriving together: one chime, one toast each (up to 5, then a summary);
- the unread state lives on the server (`probes.admin_seen_at`): opening a probe or «Mark as
  read» clears it for every admin session;
- sound: a two-note chime synthesised with Web Audio (no file). Browsers allow sound only after
  a user gesture, so the audio context is unlocked on the first click/key press; if a probe
  arrives before that, the toast still shows and a banner says the browser blocked the sound.
  «🔔 Sound on / 🔕 Sound off» is remembered per browser.

## Authentication

The API has always used `Authorization: Bearer $ADMIN_TOKEN` (constant-time comparison; with no
token set, every admin route answers 404). That still works for scripts. The browser never keeps
the token:

1. The sign-in form sends the token once to `POST /api/admin/session`.
2. The server answers with a random 256-bit session ID in a cookie: `HttpOnly` (page scripts
   cannot read it, so XSS cannot steal it), `Secure` (whenever the public API URL is https),
   `SameSite=Strict`, `Path=/api/admin`. Only its SHA-256 is stored (`admin_sessions`).
3. Sessions end after 12 h (`ADMIN_SESSION_TTL_SECONDS`), after 2 h idle
   (`ADMIN_SESSION_IDLE_SECONDS`), on sign-out, and for everyone when `ADMIN_TOKEN` changes.

**CSRF.** `SameSite=Strict` already keeps the cookie off cross-site requests. On top of that,
every cookie-authenticated request must carry `X-Scat-Admin: 1` (a custom header, so a browser
sends it cross-origin only after a CORS preflight) and, when the browser sends an `Origin`, it
must be one of `ALLOWED_ORIGINS`. CORS allows credentials **only on `/api/admin/*`** and only for
the exact `ALLOWED_ORIGINS` (never `ALLOWED_ORIGIN_REGEX`, never `*`); public endpoints still
allow no credentials at all.

**Migration:** nothing to do. Existing curl/cron usage with the Bearer header is unchanged.
`ALLOWED_ORIGINS` must contain the site's origin (it already does for the public site). The
site (`scat-search.ru`) and API (`api.scat-search.ru`) are the same site, so the Strict cookie is
sent by the panel's requests.

Other protections: sign-in is rate-limited (10/min per IP), failed admin authentications are
rate-limited and logged as `auth` events, and every admin action is logged as an `admin` event.
A test enumerates every `/api/admin` route from the app's schema and requires 401 without
credentials, so a new route cannot be added unprotected by accident.

## Source management and SSRF

Sources moved from `config.txt` to the `sources` table. On the first start after the upgrade the
file is imported once (and left in place as a backup); from then on the table is the source of
truth, managed in the panel.

A source URL typed into a browser form must never make the backend fetch internal things. The
backend (not the form) enforces, on save **and** on every fetch
(`app/services/safe_fetch.py`):

- only `http`/`https`, no `user:pass@`, no whitespace/control characters, ≤ 2048 characters;
- the host is resolved by the backend and refused if **any** address is not public: loopback,
  RFC 1918, link-local (incl. `169.254.169.254` metadata), CGNAT, unique-local and link-local
  IPv6, multicast, reserved, documentation ranges, IPv4 wrapped in IPv6. `localhost`, `*.local`,
  `*.internal`, `*.lan`, `*.home.arpa` and dot-less names (Docker service names) are refused
  before DNS. Numeric tricks (`0x7f.1`, `127.1`) resolve to loopback and are refused the same way;
- the connection goes only to the addresses just validated (a pinned resolver), so DNS
  rebinding between the check and the connection is impossible;
- redirects are never followed blindly: each `Location` is validated the same way, at most
  `SOURCE_MAX_REDIRECTS` (3);
- proxy environment variables are ignored, the body is capped at `SOURCE_MAX_BYTES` (32 MB), and
  the time at the source's timeout.

Also new: VLESS links whose server is a literal non-public address are dropped before Xray sees
them, so a poisoned list cannot make the backend's own Xray connect into the host or Docker
network.

## Runtime settings

Stored in SQLite and applied over the environment's values (`app/admin/settings.py`); a reset
returns to the `.env` value. Every value has a type and bounds; combinations are checked
(`DEEP_MIN_SUCCESS ≤ DEEP_ATTEMPTS`, offline ≥ two heartbeats); an update is all-or-nothing.
Scanner, source, cache, probe, verification and ranking settings are there. Not there, by design:
`ADMIN_TOKEN`, `ALLOWED_ORIGINS`, `ALLOWED_ORIGIN_REGEX`, `TRUST_PROXY_HEADERS`, `PUBLIC_API_URL`,
`PROBE_VERIFY_URL`, `PROBE_CONTROL_URL`, paths and binaries: secrets, trust and network
boundaries stay in `.env`. `CACHE_TTL_SECONDS` applies to caches written from then on;
`AUTO_REFRESH` after a restart.

## Events (Logs)

Written by the services at the moments an operator acts on: probe registered / verified /
verification failed / spot check failed / results rejected, source failed / recovered, scan
completed / failed / cancelled, sources imported, admin sign-in and failed sign-ins, every admin
action, settings changes, startup. Kept 30 days, at most 100 000 rows. Before storing, values
under keys like `token`, `secret`, `password`, `authorization`, `cookie`, `phone`, `session` are
replaced by `[redacted]`, and enrollment tokens or Bearer credentials inside messages are
masked.

## API

All under `/api/admin`, all require the session cookie + `X-Scat-Admin: 1`, or
`Authorization: Bearer $ADMIN_TOKEN`.

| Method | Path | |
|---|---|---|
| POST / GET / DELETE | `/session` | sign in (`{token}`), check, sign out |
| GET | `/dashboard`, `/health` | overview; health per component |
| GET | `/notifications?after=` · POST `/notifications/read` | new-probe events and unread probes; `{probe_ids}` or `{all: true}` |
| GET | `/probes?status&online&verification&profile&version&provider_id&q&sort&order&page&per_page` | list (`items`; `probes` kept for old scripts) |
| GET | `/probes/{id}`, `/probes/{id}/jobs?errors&page`, `/probes/{id}/verifications` | details |
| POST | `/probes/{id}/approve\|disable\|enable\|verify\|test` | actions |
| PATCH / DELETE | `/probes/{id}` | contact phone / delete |
| POST | `/probes-round` | re-run the probe round for the current cache |
| GET | `/providers` | per-provider view |
| GET / POST | `/sources` · GET / PATCH / DELETE `/sources/{id}` | CRUD |
| POST | `/sources/{id}/test`, `/sources/{id}/fetch`, `/sources-check` | download and parse |
| GET | `/scanner` · POST `/scanner/start\|run\|cancel\|restart` | scanner |
| GET | `/scans?status&query&page` · `/scans/{id}` | history |
| GET | `/cache` · POST `/cache/clear\|rebuild\|refresh` | cache and live ranking |
| GET | `/events?component&severity&kind&probe_id&source_id&since&until&q&page` | logs |
| GET / PUT | `/settings` · DELETE `/settings/{key}` | runtime settings |
| GET / POST | `/stats`, `/scan` | kept from before |

## Deployment

Nothing new to configure: `ADMIN_TOKEN` (deploy.sh generates it), `PUBLIC_API_URL` (deploy.sh
sets it; verification needs it), `ALLOWED_ORIGINS` with the site's origin. The SQLite migration
runs by itself on start and keeps every existing row. The frontend image serves `/admin/*` from
its own shell (`nginx.conf`). Rebuild both images: `docker compose build && docker compose up -d`.
