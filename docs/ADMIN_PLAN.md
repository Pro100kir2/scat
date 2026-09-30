# Admin panel, probe verification, distributed scoring — audit and plan

Status: **implemented.** This file is the audit record (the system *before* the change) and the
design; the system as built is documented in [PROBES.md](PROBES.md), [RANKING.md](RANKING.md)
(incl. the audit questions answered for the new code) and [ADMIN.md](ADMIN.md).

**Decisions (2026-10-01):** option A (reachability confirmations re-rank backend-verified
candidates) · confirmations counted **per provider** (one point per ISP; additionally one
public IP can back at most one provider point) · cookie-session admin auth · delivered in one pass.
Branch audited: `provider-probes` @ `f29e25d`.

---

## 0. The headline finding

**The system does not perform VLESS tests through probes, and probe results do not influence
ranking at all.** This is not a bug but an explicit design decision, stated in
`app/probes/__init__.py`, `app/config.py:77`, `probe/internal/agent/job.go:1-3`,
`probe/cmd/scat-probe/main.go:8` and `docs/PROBES.md` ("A probe **never** runs VLESS or Xray").

| Question | Who answers today | How |
|---|---|---|
| Does this VLESS config work? | backend only (`app/services/scanner.py`) | real Xray tunnel, trace, 2nd site, download, 5 latency samples, 2 MB speed test |
| Is its endpoint reachable from ISP X? | probes | `TCP_CONNECT` or `TLS_CONNECT` (SNI, cert not verified) to the config's IP:port |

The top 5 is chosen **before** probes do anything; probes are scheduled by a listener that fires
after the scan was saved (`app/main.py:47-54`). Probe results are joined at request time into a
per-provider `reachability` array for display (`app/api/routes.py:25-33`). There is no probe
score anywhere.

So Goals D / §15–18 ("score = number of probe confirmations", "top-5 by probe score") are a
**product change**, not an audit of existing behaviour. See §6 for the options and the decision
that is needed.

---

## 1. Architecture overview (as built)

```
config.txt ──► VLESSScannerManager (app/services/scanner.py)   one scan at a time (asyncio.Lock + flock)
                 _collect: download sources (aiohttp, redirects ON, no SSRF filter), extract, parse,
                           dedupe by node.fingerprint, cap MAX_NODES
                 order_candidates: interleave sources
                 _find_candidates: Xray batches of 500, _qualify → first CANDIDATE_COUNT (25) working
                 _deep_check: 5 traces + 2 MB download each
                 rank_nodes / rank_candidates → top[:TOP_COUNT=5], ranked[25]
        │
        ▼
ScanService (app/services/scan_service.py)   JSON cache per query (scan_all.json, scan_DE.json), TTL 24h,
        │                                    single-flight per query, listeners after save
        ▼ listener (query == ALL only)
ProbeService.schedule_scan (app/probes/service.py)   SQLite data/probes.db
        targets table ← ranked[:PROBE_TARGET_COUNT=25], resolved to public IPs (targets.py)
        jobs: every ONLINE non-disabled probe × every unique target without a fresh result
        │
        ▼ GET /api/probes/jobs (long poll) → Go agent runs TCP/TLS/HTTP check → POST /api/probes/results
        │
        ▼
GET /api/results  →  cached top 5  +  reachability(fingerprints) per provider (approved probes only)
```

Persistence: scan cache = JSON files in `DATA_DIR`; probe state = SQLite (`app/probes/store.py`,
`PRAGMA user_version = 1`, schema created with `CREATE TABLE IF NOT EXISTS`, **no migration
mechanism yet**). No scan history is persisted (only the latest cache entry per query).

Frontend: Vue 3 + vue-router, prerendered SSG pages, no state library, no admin UI at all.
Pages: home, providers, add-provider (form → enrollment token → install command →
`InstallStatus.vue` polling `/api/providers/enrollment`), install guide. Tests: vitest (60 pass).

Deployment: `docker-compose.yml` (backend on 127.0.0.1:8000) + frontend nginx (127.0.0.1:8080),
Caddy on host terminates TLS for `scat-search.ru` and `api.scat-search.ru` (different origins,
**same site**). `deploy.sh` over SSH.

## 2. Current probe lifecycle

`POST /api/providers` (website form: name, country, region, ASN, info) → provider row +
single-use enrollment token (24 h) → installer `POST /api/probes/diagnostics` (device facts →
SUPPORTED/UNSUPPORTED + profile) → download + SHA-256 verify → `scat-probe enroll` →
`POST /api/probes/register` → probe row `status = pending` (or `approved` if
`PROBE_AUTO_APPROVE`) → heartbeats every 30 s, long-poll jobs.

- Pending probes **do receive and run jobs** (`schedule_scan` and `seen()` enqueue for
  `status != 'disabled'`); their results are stored but filtered out at read time
  (`status='approved'`). Their provider stays hidden from the public list.
- Approval is manual: `POST /api/admin/probes/{id}/approve` via curl.
- There is already a **control job** (`HTTP_GET` to `PROBE_CONTROL_URL`, gstatic 204) sent to
  every probe with its first targets; its outcome sets `probes.control_ok`. It is used only to
  mark a probe's failures as inconclusive.
- Website status states: `waiting → installing | unsupported → registered → online | offline`.
  "pending" is only mentioned in one sentence on the success screen.

## 3. Current scanner lifecycle

Described in §1. Facts relevant to the admin panel:
- `VLESSScannerManager.lock` serialises all scans; `ScanService` keeps one task per query and
  caps queued country scans at `MAX_QUEUED_SCANS`. Cancelling = `task.cancel()`; the
  `xray_session` context manager already kills Xray on exit, so cancellation is safe.
- Live progress: `scanner.get_status()` (stage, counts). After a scan, only the cache entry
  survives (`total_nodes, tested, working, duration_seconds, sources[]`).
- `stats` counters and `last_error` are in memory.

## 4. Source model

`config.txt` (+ `config.txt.backup`), one URL or direct `vless://` per line, `#` comments,
read on every scan by `read_sources()`. Per-scan `SourceResult(url, success, vless_count, error)`
lands in the cache entry. Public `GET /api/sources` exposes the list and last status.

**SSRF:** source fetching uses `aiohttp` with `allow_redirects=True`, system DNS, no address
filter. Safe today only because the file is operator-controlled. Once sources are editable from
a browser (a stolen admin session, a CSRF, a typo), this becomes a real SSRF into
`127.0.0.1:8000`, the Docker network, and `169.254.169.254`. The probe side already has a good
address policy (`app/probes/targets.py:is_public_ip`) that can be reused.

Secondary note: VLESS **node** addresses from public lists are handed to Xray unfiltered, so a
poisoned list can make the backend's Xray open TCP connections to `127.0.0.1:*` or Docker
neighbours (speaking VLESS, so low impact, but it is still an internal connection). Proposed:
drop nodes whose address is a non-public IP literal during `_collect`.

## 5. Ranking / scoring (as built)

`scanner.py:1084-1110`:
- `is_usable`: ≥ `DEEP_MIN_SUCCESS` (4/5) latency samples, a latency, a completed 2 MB download.
- `rank_score = latency_ms × (1 + 2 × (1 − success_ratio)) + 8000 / speed_mbps` (lower is better).
- `top = sorted(usable, key=rank_score)[:5]`; `ranked = usable (same order) + the rest by latency`.
- Stored in the cache; the public API renumbers `rank` 1..n in that order (`presenter.public_node`).

## 6. Decision needed: what a "probe confirmation" is

Options:

**A. Recommended: probe reachability confirmations re-rank the backend-verified candidates.**
Keep the backend's Xray check as the gate: only configs that genuinely work get in. A probe
"confirms" a candidate when its reachability job for that candidate's target ends in
`SUCCESS` and is not inconclusive. Score = distinct eligible probes confirming. The top 5 is
recomputed from the 25 candidates whenever probe results change: score descending, then the
existing `rank_score`. That delivers §15–17 exactly, needs **no agent change for scoring**, and
matches the threat model (the network blocks we care about are DPI/SNI/IP blocks, which a
TCP/TLS check does see). Limitation to state honestly: a probe proves "the ISP lets this
endpoint through", not "the full VLESS session works from this ISP".

**B. Real VLESS tests on probes.** Embed Xray-core in the Go agent. The binary grows from about
7 MB to 25–30 MB, which rules out most OpenWrt routers (8–16 MB flash, and the agent runs with
`SetMemoryLimit(24MB)`). It also routes test traffic from volunteers' home IPs through untrusted
servers from public lists, which breaks the documented promise to volunteers. This is a
re-architecture. Not recommended.

**C. Keep ranking backend-only.** Show the score, but don't use it for ordering. This
contradicts §16.

Sub-decisions for A (my defaults):
- **Unit of confirmation:** one per probe, and probes that share the same public IP collapse
  to one. This blocks a cheap Sybil attack: ten enrollments from one home count as one.
- **Denominator ("n/4"):** the approved probes that have a conclusive result for that target in
  the current round. Online status at result time decides eligibility, not online status now.
- **Freshness:** only results from the current target round count (jobs created after the scan
  that produced the targets), not any result within 24 h.
- **Tie-break:** existing `rank_score`, then latency. Configs with 0 confirmations stay eligible
  for the top 5 only if fewer than 5 have ≥1 confirmation (or when no probes exist, which gives
  exactly today's behaviour).

## 7. Answers to §33 (from the code, before the change; see RANKING.md for after)

| # | Question | Answer (today) |
|---|---|---|
| 1 | Candidates selected for probes | `entry["ranked"][:PROBE_TARGET_COUNT]` = up to 25 (all candidates), **main query only**; country searches never get probes (`main.py:47`) |
| 2 | Eligible probes | every **online, non-disabled** probe, **including pending** (`service.py:482`, `seen()` → `ensure_jobs_for_probe`) |
| 3 | Assignment | broadcast: each probe gets every unique target (by `Target.key`) that it has no fresh non-ERROR result for (`_enqueue`); unique index `jobs_open(probe_id,key)` prevents duplicates |
| 4 | How a probe gets a job | `GET /api/probes/jobs?wait=25` long poll, leased with timeout+30 s, retried once (`PROBE_JOB_MAX_ATTEMPTS=2`) |
| 5 | VLESS connection test on probe | **none**: `TCP_CONNECT` or `TLS_CONNECT` with SNI, `InsecureSkipVerify` (`checks.go`) |
| 6 | Result authentication | `Bearer prb_id.secret` (SHA-256 hash, constant time); the job must be this probe's own open job. **No proof of execution**: a client holding credentials can answer `SUCCESS` without connecting |
| 7 | Result → candidate | `jobs.key` → `targets.key` → `targets.fingerprint`. Several configs sharing IP:port:SNI share one result (correct for reachability) |
| 8 | Duplicate prevention | a job accepts one result (`status IN (queued,leased)` then `done`); nothing aggregates a count, so no dedupe of counts exists |
| 9 | Score calculation | **no probe score exists**; ranking is `rank_score` on backend data only |
| 10 | Where stored | backend ranking in `data/scan_all.json`; probe results in SQLite `results` |
| 11 | Sorting | `rank_nodes` in `scanner.py`, during the scan |
| 12 | Cache preserves score | n/a; reachability is joined at request time, so it is never stale |
| 13 | Public API returns score | no, it returns per-provider states `SUCCESS/FAILED/TIMEOUT/CHECKING/NO_PROBE/OFFLINE/NOT_TESTED` |
| 14 | Backend-only bypass of probe scoring | backend-only is the **only** ranking |
| 15 | One probe → multiple points | n/a today. For display, a provider is SUCCESS if **any** of its probes succeeded in the last 24 h |
| 16 | Failed/offline probe affects ranking | cannot affect ranking. For display: results from a probe that is now offline still count until TTL (by design: "last known") |
| 17 | Top-5 before or after probe scoring | **before**; probes run only after the top 5 is cached |

## 8. Gaps (what must change)

Probes / verification
1. Auto-verification instead of manual approve. Proposed (reuses the job system, no new agent
   code): on first contact, enqueue a **verification round** for a pending probe:
   - `HTTP_GET` to `https://<api>/api/probes/verify/<one-time nonce>`. The backend **observes
     the request arrive**, checks the nonce belongs to this probe's open job, and records the
     source IP. Then the probe must report `SUCCESS` with `http_status=204` for that job. That
     is proof that the probe executed an HTTP job from its own network, not just an answer.
   - The existing control job (gstatic 204) and one `TLS_CONNECT` to a known-good public
     endpoint, to prove the TLS path works.
   - All pass within `PROBE_VERIFY_TIMEOUT` (10 min) → `approved`. Otherwise it stays
     `pending`, with `verification_status=failed` and a coded reason (`no_contact`,
     `nonce_not_fetched`, `wrong_status`, `tls_failed`, `timeout`, …). The admin can re-run it.
   - Pending probes no longer receive scan targets, only verification jobs.
   - The manual `approve` stays available as an admin override. `PROBE_AUTO_APPROVE` is kept
     for closed deployments.
2. Proof of execution for scoring results (anti-fake-SUCCESS). *Built instead: hidden one-time-URL
   spot checks (no agent change); the certificate idea below was dropped because REALITY servers
   present the camouflage site's certificate, which varies by edge, so fingerprints would give
   false failures.* Originally proposed: a **backwards-compatible agent upgrade**. `TLS_CONNECT` results carry `cert_sha256` of the leaf certificate. The backend
   records the expected fingerprint when it resolves targets (it handshakes once itself) and gives
   a scoring point only when they match. Old agents without the field still show reachability,
   but are counted only when `PROBE_REQUIRE_PROOF=false`. Also add
   **spot-check nonce jobs** (verification-style HTTP_GET, randomly mixed in) that detect probes
   answering blindly; failing one → probe auto-flagged, points withdrawn. Requires Go toolchain
   to build; `go` is not installed locally (Docker build stage has it).
3. Cumulative per-probe counters (jobs done/failed/timeouts, last job, last error): jobs rows
   are deleted after 24 h, so counters go on the `probes` row.
4. Phone: the website form is the natural place (the enrollment is created there, installer
   unchanged). Store on `enrollments.contact_phone`, copy to `probes.contact_phone` at
   register. Normalised to E.164 (`+` and 8–15 digits, Russian `8XXXXXXXXXX` → `+7…`).
   Admin-only. Never in `_probe_public`, `_provider_public`, `enrollment_status`, or logs.
   Plaintext in SQLite with the same protection as `public_ip` (already stored). Encryption at
   rest can be added later with an env key if wanted.
5. Install UX: add `verifying / verified / verification_failed` to `EnrollmentStatus`, with
   human texts and reasons + "what to do", technical details in a collapsible block.

Scoring / ranking (if option A)
6. `app/services/ranking.py`: `probe_scores(fingerprints)` + `final_ranking(entry, scores)`,
   used by `/api/results`, `/api/vless/top|sub|raw`. Pure function over the cache entry +
   SQLite, so the cache never goes stale and needs no rewrite. Public node gains
   `probe_score`, `probe_total`.
7. Pending/disabled exclusion both at job creation and at aggregation. Aggregation keeps only
   the latest conclusive result per (probe, key) in the round. Probes collapse by public IP.
8. Separate the ranking helper from the aggregation path in tests (§17 requires both).

Admin backend
9. Auth: see §9.
10. New SQLite tables via a real migration runner (`user_version` 1→2): `sources`,
    `scans`, `scan_sources`, `events`, `settings`, `admin_sessions`, `verifications`; new columns on
    `probes`/`enrollments`. Scan results stay JSON; the scan history rows are written by a
    ScanService listener plus start/fail hooks.
11. Sources: move from `config.txt` to the `sources` table. On first start after migration,
    import `config.txt` once (the file stays as a seed/backup, and is still used if the DB is
    empty). SSRF-safe fetcher: https/http only, no credentials in URL, resolve then every IP
    must pass `is_public_ip`, connect pinned to the vetted IP (custom aiohttp resolver),
    redirects followed manually (max 3, each re-validated), body cap (e.g. 20 MB), timeout,
    ports 80/443/8080/8443 by default. Validation on create/update **and** at fetch time
    (DNS rebinding).
12. Scanner: start / cancel / clear cache / rebuild cache (=force scan). "Restart scanner" =
    cancel + start, exposed only as that. No concurrent scans (existing lock is respected).
13. Settings: whitelisted keys with type/min/max, stored in `settings`, applied onto `config`
    attributes at startup and on change (all scanner settings are read at call time, so this
    works; rate limiter limits are rebuilt). Secrets are never in the whitelist.
14. Events log: `events(ts, component, severity, kind, probe_id, source_id, message, data)` with
    retention 30 days/100k rows; written by services at meaningful points; a scrubber drops
    tokens/secrets/phones from `data`.
15. Health: DB ping, scanner state, last success, probe counts, sources failing, cache
    age, `shutil.disk_usage(DATA_DIR)`, RSS from `/proc/self/status`/`resource`, uptime, versions.
16. Notifications: `GET /api/admin/notifications?after=<event_id>` returns new
    `probe_registered` events and unread count; `POST …/read`. The UI polls every 20 s, only
    while the tab is visible (no SSE/WebSocket exists; polling a single indexed query is cheap).
    The last seen event id is kept in `localStorage`, so reloads don't replay sounds.

Admin frontend
17. `/admin/*` routes: lazy-loaded, excluded from prerender/sitemap, `noindex`, own layout
    reusing `main.css` tokens/buttons/inputs. Pages: Dashboard, Probes (+ details), Sources,
    Scanner, Scans (+ details), Providers, Cache, System, Logs, Settings. Sound: a short WebAudio
    beep (no asset file), unlocked on the first user gesture. If audio is blocked, the visible
    toast still shows and a "🔇 enable sound" hint appears. Mute state is kept in `localStorage`.

## 9. Admin authentication — proposal

Today: `Authorization: Bearer ADMIN_TOKEN` compared with `hmac.compare_digest`; 404 when unset.
There is no browser flow, and CORS allows no credentials and only `Content-Type`.

Putting `ADMIN_TOKEN` into JS/sessionStorage would expose it to any XSS. Proposal:
- `POST /api/admin/session {token}` (rate-limited, failures logged as events) → random 256-bit
  session id; only its SHA-256 is stored in `admin_sessions` (expiry 12 h, idle 2 h). Set as
  `__Host-`-style cookie (`HttpOnly; Secure; SameSite=Strict; Path=/api/admin`). The frontend
  and API are same-site (`scat-search.ru` / `api.scat-search.ru`), so Strict works.
- CSRF: SameSite=Strict + every state-changing admin request must carry `X-Scat-Admin: 1`
  (a custom header forces a CORS preflight, which only the exact allowed origin passes) +
  `Origin` check against `ALLOWED_ORIGINS`.
- CORS: `allow_credentials=True` **only** for exact `ALLOWED_ORIGINS` (never a regex/`*`),
  methods add PUT/PATCH/DELETE, headers add `X-Scat-Admin`. Public endpoints are unaffected.
- `require_admin` accepts either the Bearer token (curl/scripts keep working) or a valid
  session cookie. Logout deletes the session. The token itself never reaches JS after login.
- Local dev (`http://localhost`): the `Secure` flag is dropped only when the request is not https.

## 10. Test plan

- Migration: open a copy of a v1 DB (fixture built with the old schema + rows) → v2, rows intact.
- Probe lifecycle: register → pending → verification jobs → faithful agent executes → nonce hit
  → approved; failure paths with coded reasons; pending gets no scan targets.
- **25-probe integration** (`tests/integration/test_distributed.py`, marker `integration`):
  real FastAPI app served by uvicorn on 127.0.0.1; 25 **faithful Python agents**
  (`tests/probe_agent.py`: same wire contract as `job.go`/`checks.go`: strict decode,
  validation, real TCP connect / TLS handshake / HTTP GET, long poll, heartbeat, retries),
  address policy relaxed to loopback exactly like the Go tests do; target servers = local TCP/TLS
  listeners, some of them closed or blackholed per probe to create 4/4…1/4 confirmation
  patterns; verification via the real nonce endpoint; assertions on the final `/api/results`
  order. Plus an optional Go-driven variant that runs the real `agent.Agent` ×25 against the
  same server (`go test -tags integration`), skipped when `go` is absent.
- Scoring/ranking deterministic tests over the full aggregation path (the A–H dataset of §17).
- Duplicates, replay (a result for a done job, another probe's job, an expired job), same-IP
  collapse, pending/disabled/offline exclusion, fake SUCCESS without proof.
- Sources CRUD + SSRF matrix (127.0.0.1, localhost, 10/8, 172.16/12, 192.168/16, 169.254.169.254,
  `[::1]`, `::ffff:127.0.0.1`, decimal/octal IPs, DNS→private, redirect→private, userinfo URLs,
  non-http schemes).
- Admin authz: every `/api/admin/*` route is enumerated from `app.routes` and asserted to 401 without
  credentials (so a new route cannot forget the dependency), CSRF header, cookie flags, phone
  never in public payloads (grep all public responses).
- Frontend: vitest for notification dedupe/mute/unread, status texts, API client.

Pre-existing baseline: 315 passed, **2 failed** (`test_scanner.py` Xray tests: local Xray
26.3.27 rejects `allowInsecure`, which the pinned 25.1.30 in the Dockerfile accepts, so this is a
real forward-compat issue for the next Xray upgrade), **1 error** (`tests/test_uri_compat.py:69`
helper named `tested` is collected as a test).

## 11. Delivery phases

1. Migrations + events + admin session auth + CORS change + authz test harness.
2. Verification workflow + phone + install UX + notifications endpoint.
3. Scoring/ranking (option A) + faithful agent + 25-probe integration + ranking tests.
4. Sources table + SSRF-safe fetcher + admin sources API.
5. Scan history, scanner controls, cache, settings, health, logs APIs.
6. Admin frontend (all pages) + tests.
7. Agent proof-of-execution (Go; needs toolchain) + docs.

## 12. Security review (done)

Reviewed: admin authentication and authorization, IDOR, SSRF, XSS, command/SQL injection,
arbitrary URL fetching, probe impersonation, replay and duplicate results, privilege
escalation, secret and log leakage, CSRF (cookies were introduced), malicious source URLs,
malicious probe results. Findings and what was done:

| # | Finding | Severity | Fix | Test |
|---|---|---|---|---|
| 1 | Source URLs become admin-editable: the old fetcher followed redirects and resolved freely, so a URL (or a redirect, or DNS rebinding) could reach 127.0.0.1:8000, the Docker network or 169.254.169.254 | high (new surface) | `safe_fetch`: scheme/userinfo checks, backend DNS with all-addresses-public rule, pinned resolver, per-hop redirect validation, size/time caps; enforced on save and on every fetch | `tests/test_sources.py` (32-case URL matrix, DNS tricks, redirects to private/metadata/localhost, loopback never contacted) |
| 2 | VLESS links with literal private addresses were handed to the backend's Xray | medium | dropped in `scanner.usable_node` | `test_scanner_collects_from_the_registry` |
| 3 | Pending (unverified) probes received real scan jobs; only reading filtered their results | medium | scan targets go to approved probes only | `test_pending_disabled_and_failed_probes_never_contribute`, `test_verified_probe_starts_working_immediately` |
| 4 | Any credential holder could claim SUCCESS for everything | high for scoring | verification by server-observed one-time URL; hidden spot checks; one point per provider and per public IP; latest-result-only | `tests/test_verification.py`, `tests/test_scoring.py`, both 25-probe suites (liar never approved) |
| 5 | Admin login answered 422 (not 404) with the admin API disabled: reveals the endpoint | low | enabled-check runs before body validation | `test_admin_api_is_off_without_a_token` |
| 6 | Cookie sessions bring CSRF | by design | SameSite=Strict + `X-Scat-Admin` + Origin check; credentials CORS only on `/api/admin`, exact origins only | `tests/test_admin_auth.py` |
| 7 | Token must not reach JS | by design | exchanged once for an HttpOnly cookie; only hashes stored; token rotation ends sessions | `test_login_sets_a_strict_http_only_cookie...`, frontend `admin.test.ts` |
| 8 | A new admin route could be added without protection | process | every `/api/admin` route enumerated from the schema must answer 401 | `test_every_admin_route_requires_authentication` |
| 9 | Secrets/phones could end up in logs | medium | event scrubber (keys and token patterns); phone never logged | `test_event_filters`, `test_phone_is_stored_for_admins_and_nowhere_public` |
| 10 | A misbehaving probe could flood the event log with rejected results | low | one event per probe per 10 min; retention caps | — |
| 11 | Body-size middleware extended to `/api/admin` answered 411 to bodyless `curl -X POST` (regression caught in review) | low | bodyless admin requests pass; chunked/oversized still refused | `test_bodyless_admin_actions_work_like_the_documented_curl_commands` |

Unchanged and verified: XSS (all output is text interpolation; the only `v-html` is the locally
generated QR SVG), SQL (parameters everywhere; sort columns from a whitelist), IDOR (single admin
role; probes can only answer their own jobs; enrollment status needs its token), command
injection (no shell anywhere in the new code), path traversal (source cache files are named by
integer ID).

Residual risk, accepted and documented (PROBES.md, RANKING.md): a client that honestly runs every
HTTP job but invents TCP/TLS outcomes is not caught automatically; per-provider and per-IP
counting bound its effect, and the admin sees each probe's network.
