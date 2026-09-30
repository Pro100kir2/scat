# Provider probes

Скат has two strictly separate questions:

| | Question | Who answers | How |
|---|---|---|---|
| **VLESS scanner** | Does this VLESS configuration actually work? | the backend (`app/services/scanner.py`) | real VLESS connections through Xray, downloads, latency |
| **Provider probes** | Is this VPN endpoint reachable from this ISP's network? | probes on volunteers' devices | TCP connect, TLS handshake, HTTP GET, nothing else |

A probe **never** runs VLESS or Xray, never acts as a VPN, never executes commands, and never
opens a listening port. It is a small, typed network-measurement agent that talks to the API
over outbound HTTPS only.

- [Architecture](#architecture)
- [Data model and states](#data-model-and-states)
- [API](#api)
- [Installation](#installation): [interactive](#interactive-installation), [manual](#manual-installation), [removal](#removal)
- [Supported platforms](#supported-platforms)
- [OpenWrt](#openwrt)
- [Security model](#security-model)
- [Operations](#operations): adding a provider, approving probes, configuration
- [Troubleshooting](#troubleshooting)
- [Developing a new probe implementation](#developing-a-new-probe-implementation)

## Architecture

```
                     full scan finished (ScanService listener, main "all" query only)
                                       │
                 ranked candidates ────┤  top PROBE_TARGET_COUNT (25): usable ones in rank order
                                       ▼
                     ProbeService.schedule_scan          app/probes/service.py
                     ├─ resolve hostnames on the backend, reject non-public addresses
                     ├─ one Target per config: TLS_CONNECT (tls/reality, with SNI) or TCP_CONNECT
                     ├─ targets table = current set (deduplicated by Target.key)
                     └─ per online probe: one job per target without a fresh result,
                        plus a control HTTP_GET if the probe's last control is > 30 min old
                                       │
          SQLite (DATA_DIR/probes.db)  │  jobs: queued → leased → done | expired
                                       ▼
   probe ── GET /api/probes/jobs?wait=25 (long poll) ──▶ leased jobs (≤ 10 per poll)
   probe ── runs checks, ≤ 4 at a time (goroutines) ───▶ POST /api/probes/results
   probe ── POST /api/probes/heartbeat every 30 s
                                       │
                                       ▼
   GET /api/results ── reachability for the shown configs, read from stored results only
```

Components:

| Path | What |
|---|---|
| `app/probes/targets.py` | the only job shapes, address/port/SNI validation, DNS on the backend |
| `app/probes/profiles.py` | diagnostics → SUPPORTED/UNSUPPORTED + installation profile |
| `app/probes/service.py` | enrollment, credentials, heartbeats, queue, results, aggregation, maintenance |
| `app/probes/store.py` | SQLite schema (WAL, one connection on the event loop) |
| `app/probes/api.py` | `/api/providers*`, `/api/probes/*`, `/api/admin/probes*` |
| `app/probes/releases.py` | binaries + `SHA256SUMS` served from `PROBE_DIST_DIR` |
| `probe/` | the Go agent (standard library only) and `install.sh` |
| `frontend/src/pages/Providers*`, `AddProviderPage`, `InstallGuidePage` | the website side |
| `frontend/src/components/results/NetworkAvailability.vue` | per-provider beacons in an opened result card |

Why SQLite: the scan cache stays JSON files, but probe state needs transactional, concurrent
writes (many agents, leases, retries). SQLite is in the Python standard library and lives in
the existing `DATA_DIR` volume. The API runs one uvicorn worker, so long-poll wake-ups are
in-process `asyncio.Event`s.

### When probe work happens

- **After a successful full scan** (the main, all-countries query). Country searches and page
  loads never create jobs.
- **When a probe comes online** (first heartbeat, or back after being offline): it gets the
  current targets it has no fresh result for.
- **When an admin approves a probe**, or calls `POST /api/admin/probes-round`.

### Queue, retries, caching

| Setting | Default | Meaning |
|---|---|---|
| lease | check timeout + 30 s | a leased job not answered in time goes back to the queue |
| `PROBE_JOB_MAX_ATTEMPTS` | 2 | then the job expires |
| `PROBE_JOB_TTL_SECONDS` | 900 | an unclaimed job expires |
| `PROBE_JOBS_PER_POLL` | 10 | per long poll; the agent runs ≤ 4 at once |
| `PROBE_RESULT_TTL_SECONDS` | 86400 | a result is reused instead of re-checking |

Deduplication: at most one open job per `(probe, target key)` (a partial unique index), and
configs that share an endpoint share one target. A fresh result for a probe and key means no
job at all.

The **control check**: before its first job (and every 30 min), a probe fetches
`PROBE_CONTROL_URL` (a Google 204 page). If that fails, the probe's own internet is broken, so
its FAILED/TIMEOUT results are stored as *inconclusive* and do not paint providers red.

## Data model and states

Tables (`app/probes/store.py`): `providers`, `enrollments`, `probes`, `jobs`, `results`,
`targets`. A result row has endpoint (ip, port, server name), provider, probe, check type,
outcome, latency, `checked_at`, `expires_at`. Individual probe results are kept, so per-probe
views can be added later; the API currently aggregates per provider.

Per config and provider the API reports one of:

| State | Meaning | Site |
|---|---|---|
| `SUCCESS` | at least one approved probe of the provider reached the endpoint | aqua beacon, «Доступен · 38 мс» |
| `FAILED` | probes answered, none reached it; at least one got refused/reset/TLS error | coral, «Недоступен» |
| `TIMEOUT` | probes answered, none reached it, all timed out | coral, «Нет ответа» |
| `CHECKING` | an online probe holds an open job for it | sand, breathing, «Проверяется» |
| `NO_PROBE` | the provider has no approved probe any more | empty ring, «Нет зонда» |
| `OFFLINE` | the provider's probes are offline and there is no fresh result | dark light, «Зонд офлайн» |
| `NOT_TESTED` | no result yet, the target was rejected, or the config is not in the probe set | empty ring, «Не проверялся» |

Providers appear publicly only once they have an approved probe. A probe is **Online** while
its last request is at most `PROBE_OFFLINE_AFTER_SECONDS` (100 s ≈ 3 missed 30-s heartbeats)
old.

## API

All under `/api`. Agent endpoints authenticate with `Authorization: Bearer <probe_id>.<secret>`.
POST bodies on these paths need `Content-Length` and are capped at 64 KB (411/413).

Website:

| Method | Path | Body → reply |
|---|---|---|
| GET | `/providers` | → `{providers[], probes[], offline_after_seconds}`; never starts checks |
| POST | `/providers` | `{name, country, region?, asn?, info?}` → 201 `{enrollment_id, token, expires_at, provider, install{curl, wget, diagnose_only, ...}}`; 10/hour per IP |
| POST | `/providers/enrollment` | `{enrollment_id, token}` → `{state: waiting \| installing \| unsupported \| registered \| online \| offline, diagnostics, probe}` |

Installer and agent:

| Method | Path | Body → reply |
|---|---|---|
| GET | `/probes/install.sh` | the installer, with the API origin filled in |
| POST | `/probes/diagnostics?format=json\|env` | `{token?, diagnostics{...}}` → verdict, profile, checksum, reasons |
| GET | `/probes/releases` | versions, profiles, sizes, SHA-256 |
| GET | `/probes/download/{profile}` | the binary (`X-Checksum-Sha256` header) |
| POST | `/probes/register` | `{token, agent_version, profile, platform}` → 201 `{probe_id, secret, status, heartbeat_seconds}` |
| POST | `/probes/heartbeat` | `{agent_version, uptime_seconds, jobs_running}` → `{status, heartbeat_seconds, rotate_credentials}` |
| GET | `/probes/jobs?wait=0..25&limit=1..50` | → `{jobs: [...]}` (long poll) |
| POST | `/probes/results` | `{results: [{job_id, outcome, latency_ms?, error?, http_status?, tls_version?}]}` (≤ 50) → `{accepted, rejected[]}` |
| POST | `/probes/rotate` | → `{probe_id, secret}`; the old secret works for 1 more hour |

A job is always exactly one of:

```json
{"job_id": "job_…", "type": "TCP_CONNECT", "host": "93.184.215.14", "port": 443, "timeout_ms": 5000}
{"job_id": "job_…", "type": "TLS_CONNECT", "host": "104.16.1.1", "port": 443, "timeout_ms": 5000, "server_name": "www.microsoft.com"}
{"job_id": "job_…", "type": "HTTP_GET", "host": "142.250.74.35", "port": 443, "timeout_ms": 5000, "server_name": "www.gstatic.com", "path": "/generate_204", "tls": true}
```

Outcomes: `SUCCESS`, `FAILED`, `TIMEOUT`, `ERROR` (the probe could not run the check, e.g. no
IPv6 route: never counted against the endpoint). Error codes are `^[a-z0-9_]{1,40}$`, never
free text.

Admin (`Authorization: Bearer $ADMIN_TOKEN`): `GET /admin/probes` (all probes with their IPs),
`POST /admin/probes/{id}/approve|disable`, `POST /admin/probes-round`.

## Installation

The website (`/providers/add/`) creates a one-time **enrollment token** (valid 24 h, one probe)
and shows the commands with it.

### Interactive installation

```sh
curl -fsSL https://api.scat-search.ru/api/probes/install.sh | sh -s -- --token skenr_…
wget -qO- https://api.scat-search.ru/api/probes/install.sh | sh -s -- --token skenr_…   # OpenWrt
```

1. **DIAGNOSTIC.** `install.sh` collects OS, distribution, kernel, `uname -m`, the ELF class and
   byte order of `/bin/sh` (the userland, not the kernel), CPU model and cores, RAM total and
   available, free space where the binary goes, init system (systemd / procd / OpenRC / SysV),
   OpenWrt release/arch/target, curl/wget/busybox/Docker, root, IPv4/IPv6 default routes.
   It sends them as JSON over HTTPS.
2. **VERDICT.** The server answers `SUPPORTED` with a profile, SHA-256 and download URL, or
   `UNSUPPORTED` with the failed requirements and alternatives. Unsupported → the script prints
   why and exits with code 3. **Nothing is downloaded.** The reply is a strict `key=value`
   list; the script matches known keys and never evaluates anything.
3. **INSTALL** (supported only): download → verify SHA-256 (`sha256sum`, `openssl` or `shasum`;
   none → abort) → test-run the binary (catches a wrong CPU build before anything is placed) →
   install to `/usr/local/bin` (`/usr/bin` on OpenWrt) → `scat-probe enroll` (probe ID and
   secret, `/etc/scat-probe/probe.json`, mode 0600) → service (systemd with an empty capability
   set, procd, OpenRC or SysV) → start → `scat-probe check` until the first heartbeat succeeds.

Options: `--diagnose-only`, `--uninstall`, `--reenroll`, `--print-diagnostics` (no network).
Re-running the installer on a device that already has a probe keeps its identity (an upgrade).

### Manual installation

1. `… | sh -s -- --token … --diagnose-only` tells you the profile (or pick it from
   `/api/probes/releases`).
2. Download `/api/probes/download/<profile>` to `/usr/local/bin/scat-probe` (`/usr/bin` on
   OpenWrt); check `sha256sum` against the releases list; `chmod 755`.
3. `scat-probe enroll --api https://api.scat-search.ru --token skenr_…`
4. Run `scat-probe run` as a service. Unit files for systemd and procd are on the website's
   install guide (`/providers/install/#manual`) and in `probe/install.sh`.

### Removal

```sh
curl -fsSL https://api.scat-search.ru/api/probes/install.sh | sh -s -- --uninstall
```

Stops and removes the service, the binary and `/etc/scat-probe`. The probe then shows as
offline; an admin can disable it.

## Supported platforms

| Profile | Built as | Typical devices |
|---|---|---|
| `linux-amd64` | GOARCH=amd64 | PCs, servers, VMs |
| `linux-386` | GOARCH=386, softfloat | old x86 |
| `linux-arm64` | GOARCH=arm64 | Raspberry Pi 3/4/5 (64-bit OS), Filogic routers |
| `linux-armv7` | GOARCH=arm GOARM=7 | 32-bit Raspberry Pi OS (also on a 64-bit kernel) |
| `linux-armv6`, `linux-armv5` | GOARM=6 / 5 | Pi Zero/1, old NAS and routers |
| `linux-mipsle` | GOARCH=mipsle, softfloat | MediaTek MT7621/MT7628 routers |
| `linux-mips` | GOARCH=mips, softfloat | Qualcomm Atheros (ath79) routers |
| `linux-mips64`, `linux-mips64le` | softfloat | Cavium Octeon |
| `linux-riscv64` | GOARCH=riscv64 | VisionFive, Milk-V |

Requirements checked by `app/probes/profiles.py`: Linux, kernel ≥ 3.2, RAM ≥ 48 MB total and
≥ 16 MB available, free storage ≥ binary size + 1 MB, root, curl or HTTPS-capable wget, IPv4.
No init system is a warning (the probe runs but will not survive a reboot), not a failure.
Not supported: other operating systems (the alternative offered is any Linux device behind the
same connection), big-endian ARM, unknown MIPS byte order (refused, never guessed). ICMP ping
is not offered: it needs raw sockets or root, and ISPs often filter ICMP differently from
the TCP traffic VPNs use.

Binaries are built by the backend `Dockerfile` (`probe` stage, `go vet` + `go test` first),
`CGO_ENABLED=0 -trimpath -ldflags "-s -w"`, and served from `PROBE_DIST_DIR`.

## OpenWrt

- `uname -m` says `mips` on both MT7621 (little-endian) and ath79 (big-endian). The installer
  also sends `DISTRIB_ARCH` (`mipsel_24kc` / `mips_24kc`) and the ELF byte order; the server
  refuses rather than guesses when both are missing.
- HTTPS: `opkg update && opkg install uclient-fetch libustream-mbedtls ca-bundle`.
- The binary goes to `/usr/bin` on the overlay; the service is a procd init script
  (`/etc/init.d/scat-probe`, respawn). Logs: `logread -e scat-probe`.
- Flash: the MIPS binary is ~6.9 MB (x86_64 ~6.3 MB, ARM64 ~5.9 MB). 16 MB-flash routers usually lack the space; the installer
  says so and suggests freeing `/overlay` or extroot. Any other Linux device on the same
  connection measures the same network.
- RAM: the agent sets a 24 MB soft memory limit and aggressive GC; typical RSS is ~8-12 MB.

## Security model

**The probe cannot be used as an SSRF proxy or a LAN scanner.**

- Targets originate in public subscription lists, i.e. from anyone. The backend resolves names
  itself and hands probes **IP literals only**. A name with any non-public answer is refused
  as a whole. There is no DNS on the probe, so there is no DNS rebinding and no split horizon.
- Rejected on the backend (`targets.py`) **and again in the agent** (`job.go`): loopback,
  RFC 1918, link-local (incl. 169.254.169.254 metadata), CGNAT 100.64/10, multicast, reserved,
  documentation, benchmarking, unique-local and site-local IPv6, IPv6 with zones, and IPv4
  wrapped in IPv6 (mapped, NAT64, 6to4, Teredo). Ports 0, 25, 135-139, 445, 465, 587 are refused.
  Timeouts are bounded to 0.5-10 s; server names must be public DNS names; HTTP paths are plain
  (no `..`, `//`, scheme or host).
- **No commands.** Three job types with strict schemas; unknown fields are rejected by both
  sides; anything else becomes `ERROR invalid_job` without a connection being made.
- HTTP_GET: GET only, the connection is pinned to the job's IP, proxies from the environment
  are ignored, redirects are not followed, at most 64 KB of the body is read and discarded;
  certificates are verified (a captive portal must not look like success). TLS_CONNECT does
  not verify certificates on purpose: it measures whether the handshake gets through.
- **Credentials.** Enrollment tokens and probe secrets are 256-bit random; only SHA-256 hashes
  are stored; comparisons are constant-time. Tokens are single-use and expire after 24 h; the
  website sends them in POST bodies, never in URLs. Secrets rotate (the server asks after 30
  days; the old secret stays valid for one hour so a crash mid-rotation is harmless). The agent
  writes credentials atomically with mode 0600.
- **Abuse through user-owned probes.** New probes are `pending`: they may heartbeat and run jobs,
  but their results count and their provider appears publicly only after an admin approves
  them. Results are accepted only for the probe's own open jobs, and all target data is taken
  from the job, not from the probe. Admins can disable a probe (403 from then on, open jobs
  expire).
- **Rate limits** (in memory, one API process): provider creation and registration 10/hour per
  IP, public probe endpoints 60/min per IP, agent requests 120/min per probe, failed
  authentication 30/min per IP. Body size is capped before parsing.
- **Privacy.** Volunteers' IP addresses are stored for abuse handling and shown only to admins.
- **Installer.** The API origin inside the script must be `https://`; the token and every
  value from the server are pattern-checked; the download URL must be this API's own download
  path; the checksum is verified before the binary is ever executed.
- The API host sends `X-Robots-Tag: noindex, nofollow` on every response.

## Operations

**Adding a new provider** needs no code: anyone uses «Добавить провайдера» on the site.
Providers are keyed by case-insensitive name and country ("МТС" and " мтс " are one provider;
"MTS" in Latin letters is another, merge in SQL if needed). One provider can have any number of
probes in different cities; a provider is reachable when any of its approved probes succeeded.

**Approving probes:**

```sh
curl -H "Authorization: Bearer $ADMIN_TOKEN" https://api.scat-search.ru/api/admin/probes
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" https://api.scat-search.ru/api/admin/probes/prb_…/approve
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" https://api.scat-search.ru/api/admin/probes/prb_…/disable
```

Check that the probe's IP (from the admin list) plausibly belongs to the claimed provider
(`whois`, ASN lookup) before approving.

**Configuration:** see the `PROBE_*` variables and `PUBLIC_API_URL` in `.env.example` and
`app/config.py`. `PUBLIC_API_URL` must be set in production (deploy.sh does): behind Caddy the
API sees plain http and cannot build `https` install commands itself.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| installer: `could not reach` | no working HTTPS on the device, wrong clock (certificate errors), or firewall |
| installer exits with 3 | UNSUPPORTED; the reasons are printed and shown on the website |
| `checksum mismatch` | download corrupted or tampered with; nothing was installed; retry |
| `does not run on this device` | wrong build for the CPU; report the diagnostics (`--print-diagnostics`) |
| `token expired` / `already used` | create a new token on the website |
| probe installed but Offline | `journalctl -u scat-probe` / `logread -e scat-probe`, `scat-probe check` |
| `credentials rejected` in the log | the probe was deleted or its secret lost: `--uninstall`, then install with a new token |
| `this probe is disabled` | an admin disabled it |
| provider stays «Не проверялся» | the probe is pending approval, offline, or its control check fails (its internet is down) |

## Developing a new probe implementation

The protocol is small enough to reimplement (for example a C agent for 8 MB-flash routers).
A compatible probe must:

1. Take a one-time token, `POST /api/probes/register` with its own version, profile and
   `GOOS/GOARCH`-style platform; store the returned ID and secret privately.
2. Send `POST /api/probes/heartbeat` every `heartbeat_seconds`; rotate via `/probes/rotate`
   when `rotate_credentials` is true, persisting the new secret before using it.
3. Long-poll `GET /api/probes/jobs?wait=25`; decode strictly (unknown fields = invalid job).
4. **Re-validate every job** exactly like `probe/internal/agent/job.go` (IP literal only, the
   public-address policy, blocked ports, timeout range, hostname and path rules). Never resolve
   names. Never run anything but the three checks.
5. Report each job once with `SUCCESS/FAILED/TIMEOUT/ERROR`, latency in ms for successes, and
   an error code; batch up to 50 results.
6. Back off with jitter on errors; on 401 wait minutes, not seconds; on 403 wait an hour.
7. Never listen on a port.

Build the reference agent and run its tests:

```sh
docker run --rm -v "$PWD/probe:/src" -w /src golang:1.24-alpine sh -c 'go vet ./... && go test ./...'
```

Backend tests: `pytest tests/test_probe_*.py tests/test_installer.py`.
