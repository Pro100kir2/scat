# Ranking: backend verification, probe confirmations, the top 5

How a VLESS link from a public list becomes one of the five configs the site shows, where every
step lives in the code, and the tests that prove it. See [PROBES.md](PROBES.md) for the probe
protocol and [ADMIN.md](ADMIN.md) for the admin panel.

## The rule

1. **Only configs the backend verified can be shown.** The backend's own Xray checks decide what
   works: a real tunnel, correct exit, a second site, a payload that does not stall, then deep
   checks (≥ `DEEP_MIN_SUCCESS` of `DEEP_ATTEMPTS` latency checks and a completed 2 MB download).
   Probes can never promote a config that failed these (`ranking.usable`).
2. **Primary key: the probe-confirmation score.**
   `score = number of providers whose approved probes confirmed the config's endpoint reachable`.
   1 of 4 providers confirms → 1 point, 2 → 2, 3 → 3, 4 → 4. Not a percentage, not a latency, not
   the number of jobs sent or probes contacted: a point needs a probe's actual `SUCCESS`.
3. **Tie-break: the backend's order** (`scanner.rank_score`: latency penalised by failed checks,
   plus the time to fetch 1 MB). With no probe results at all the order is exactly the backend's.
4. **Top 5** = the first `TOP_COUNT` of that order, over *all* usable candidates of the scan (up
   to `CANDIDATE_COUNT` = 25), not just the backend's own top 5. So a 4/4 config ranked 12th by
   the backend is served before a 2/4 config ranked 1st.

`total` (the "of 4") = providers whose probes gave a *conclusive* latest answer for that target.
Both appear in the public API as `probe_score` / `probe_total` and on the site's config card
(«Подтверждено провайдерами 3/4»). `RANKING_USE_PROBE_SCORE=false` keeps the scores visible but
orders by the backend alone.

**What a probe confirms.** A probe checks reachability of the config's endpoint from its ISP's
network: a TLS handshake with the config's SNI for TLS/REALITY configs (what DPI blocks on), a
TCP connect otherwise. It does not run VLESS (by design: see PROBES.md). So a score of 4/4 means
"the backend proved this config works, and four ISPs' networks let its traffic through".

## The pipeline, step by step

| Step | Where |
|---|---|
| Sources (DB table, imported once from `config.txt`) | `app/services/sources.py` `enabled_specs` |
| Download, SSRF-safe | `sources.fetch_body` → `app/services/safe_fetch.py` `fetch_text` |
| Candidate extraction | `scanner.extract_source_nodes`, `scanner._collect` (`app/services/scanner.py:918`) |
| Parsing, validation, dropping private addresses | `scanner.usable_node` (`:1015`) |
| Deduplication (by config fingerprint, first source wins) | `scanner._collect` |
| Candidate order (sources interleaved) | `scanner.order_candidates` (`:1046`) |
| Finding the first 25 genuinely working | `scanner._find_candidates` (`:827`) |
| Deep checks | `scanner._deep_check` (`:853`) |
| Backend ranking of all candidates | `scanner.rank_candidates` / `rank_nodes` / `rank_score` (`:1058-1077`) |
| Cache (JSON, `top` + `ranked`) | `scan_service.ScanService._run` (`app/services/scan_service.py:252`) |
| Probe round after a full scan | `app/main.py:51` `schedule_probe_round` → `ProbeService.schedule_scan` (`app/probes/service.py:350`) |
| Scan job creation, one per (probe, target) | `ProbeService._enqueue` (`:404`) |
| Probe assignment | every online **approved** probe gets every target (broadcast) |
| Probe receives the job | `GET /api/probes/jobs` long poll → `ProbeService.lease_jobs` / `_lease` (`:455`) |
| Probe performs the connection test | Go agent `Checker.Run` / `tls` (`probe/internal/agent/checks.go:32,75`) |
| Probe returns the result | `Agent.jobLoop` (`agent.go:217`) → `POST /api/probes/results` |
| Backend validates the result | `ProbeService.authenticate` (`:268`) + `submit_results` (`:492`) |
| Aggregation into scores | `ProbeService.confirmations` (`:938`) + `_match_providers_to_addresses` |
| Final ranking | `app/services/ranking.py` `final_top` / `rank` |
| Served | `app/api/routes.py:22` `_cached_top` → `/api/results`, `/api/vless/top`, `/sub`, `/raw` |

## Distribution and anti-abuse

- **Eligibility.** Scan jobs go only to probes that are `approved` and online
  (`schedule_scan`, `ensure_jobs_for_probe`). Pending probes get verification jobs only; disabled
  probes get 403 and their open jobs are withdrawn; offline probes get nothing until they return.
- **Slow or failing probes.** Every probe works through its own queue; nothing waits for anyone.
  A lease expires (check timeout + 30 s), the job is retried once, then expires. A probe whose
  own internet is broken (failed control check) has its failures stored as *inconclusive*.
- **Right result, right config.** A result is accepted only for one of the submitting probe's
  own open jobs; the endpoint, port and SNI come from the stored job, never from the probe.
  `jobs.key` → `targets.key` → `targets.fingerprint`. Configs sharing an endpoint share a target.
- **No double counting.** A job takes one result (then it is `done`; a replay or duplicate is
  rejected and logged). Per probe, only its *latest conclusive* result per target counts. Per
  provider, at most one point however many probes it has. One public IP backs at most one
  provider's point (a bipartite matching), so one connection posing as several ISPs gets 1.
- **Fake confirmations.** A client must pass verification (the server must see its request),
  and hidden spot checks keep checking it; a lying probe loses its approval and all its results.
  Pending, disabled and spot-check-failed probes never count, whatever is in the table.
- **Offline probes.** A result a probe sent while online is a real observation and counts until
  `PROBE_RESULT_TTL_SECONDS` (24 h), even if the probe goes offline after sending it. After that
  it counts for nothing.

## Answers to the audit questions (as built)

| # | Question | Answer |
|---|---|---|
| 1 | How many candidates are probe-tested? | the usable-first `ranked` list of each full scan, up to `PROBE_TARGET_COUNT` (25); country searches reuse the main scan's results |
| 2 | How many probes are eligible? | every online probe with status `approved` |
| 3 | How are candidates assigned? | broadcast: each eligible probe gets every target it has no fresh result for |
| 4 | How does a probe receive a job? | long poll `GET /api/probes/jobs`, leased per job |
| 5 | How does it test? | TLS handshake with the config's SNI (TLS/REALITY) or TCP connect, from its own network |
| 6 | How is a result authenticated? | per-probe secret (SHA-256 hashed, constant-time) + the job must be this probe's open job; the probe itself proved it executes jobs (verification, spot checks) |
| 7 | Result → candidate? | through the stored job's key and the targets table |
| 8 | Duplicate confirmations? | one result per job; latest conclusive per probe; one point per provider; one point per public IP |
| 9 | How is the score calculated? | `ProbeService.confirmations`: providers with a confirming approved probe, matched to distinct IPs |
| 10 | Where is it stored? | computed on read from `results`/`probes`/`targets` (SQLite); nothing to go stale |
| 11 | Where is sorting done? | backend order in `scanner.rank_candidates` (at scan time); final order in `ranking.rank` (at request time) |
| 12 | Does the cache preserve the score? | the cache keeps the backend's candidates; scores are joined live on every request, so a new, expired or withdrawn result changes the order immediately; clearing the cache removes the candidates (tested) |
| 13 | Does the public API return it? | yes: `probe_score`, `probe_total` on every node; order of `/api/results`, `/api/vless/top`, `/sub`, `/raw` |
| 14 | Can backend-only checks bypass probe scoring? | no: every served list goes through `ranking.final_top`; if probe data is unavailable, it falls back to the backend order and logs the error |
| 15 | Can one probe give several points? | no (see 8) |
| 16 | Can failed/offline probes skew the ranking? | failed probes' results are inconclusive or FAILED (never points); offline probes' past results expire; pending/disabled never count |
| 17 | Is the top 5 chosen before or after probe scoring? | after: the top 5 is selected on every request from all usable candidates, by score first |

## Tests

| What | Where |
|---|---|
| 1/4 → 1 … 4/4 → 4; the A–H dataset (A 4, B 1, C 3, D 4, E 2, F 4, G 3, H 1); top 5 = F, D, A, G, C | `tests/test_scoring.py` (service + ranking + cache + public API) |
| duplicates, replays, one provider = one point, one IP = one point, pending/disabled/offline/inconclusive | `tests/test_scoring.py` |
| verification and spot checks | `tests/test_verification.py` |
| **25 probes end to end, faithful agent** | `tests/integration/test_distributed_probes.py` |
| **25 probes end to end, the real Go agent** | `tests/integration/test_go_agents.py` + `probe/internal/agent/integration_test.go` |

The two 25-probe suites run a real uvicorn server on 127.0.0.1 with its own database and eight
real TLS endpoints (one per candidate) that count completed handshakes. 25 agents in four
providers' simulated networks enroll through the website API, verify themselves by really
fetching their one-time URLs, get the scan's jobs, open real TLS connections, and report. The
tests then check the exact scores, the exact top 5 of `/api/results` and `/api/vless/raw`, and
that each endpoint saw exactly as many handshakes as there are probes whose networks let it
through. The Python suite adds a liar (answers SUCCESS without running anything: never approved,
never counted), a probe the admin disables mid-way (its points disappear at once), a slow
device, dropped connections, a replayed result, and spot checks on every honest probe.

The *simulated network* is the one thing a single machine cannot have for real: which ISP a
probe sits in. Each agent gets a filter at the socket level that resets (or, in the Python suite,
silently drops) connections to the endpoints its provider blocks, the way DPI does. Everything
else (job decoding, validation, the check itself, the protocol) is the production code (Go) or
a line-by-line port of it (`tests/probe_agent.py`).

```sh
# everything (about 1.5 min)
.venv/bin/python -m pytest
# only the distributed suites
.venv/bin/python -m pytest -m integration -v
# without them
.venv/bin/python -m pytest -m "not integration"
```

The Go suite compiles the agent's test binary with a local `go`, or inside the
`golang:1.24-alpine` image if it is already present (no download), and is skipped otherwise.
The Go unit tests: `cd probe && go test ./...`.
