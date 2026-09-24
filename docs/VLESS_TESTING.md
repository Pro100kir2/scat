# VLESS link testing: how it actually works

This document describes the scanner pipeline **as implemented** in `app/` at `SCANNER_VERSION = "3.1"`
(`app/config.py`). It is for a developer taking over the project. Where the code does not do something,
this document says so.

Code map:

| Concern | File | Main symbols |
|---|---|---|
| Parsing, normalization, link generation, Xray config, pipeline | `app/services/scanner.py` | `parse_vless`, `build_vless_uri`, `link_matches_tested_config`, `build_stream_settings`, `build_outbound`, `build_xray_config`, `VLESSScannerManager` |
| Network probes (what "working" means) | `app/services/probes.py` | `fetch`, `trace`, `reach`, `download`, `direct_exit_ip` |
| Tunables | `app/config.py` | `TIMEOUT`, `BATCH_SIZE`, `WORKERS`, `CANDIDATE_COUNT`, `DEEP_*`, `SCANNER_VERSION`, ... |
| Cache, single-flight, file lock | `app/services/scan_service.py` | `ScanCache`, `ScanService` |
| Public API shape | `app/services/presenter.py`, `app/api/routes.py` | `public_node`, route handlers |
| Subscription output | `app/services/subscription.py` | `generate_subscription_content` |

`vless_scanner.py` in the repository root is an older standalone script. Nothing in `app/` imports it, and it
is **not** part of the pipeline described here.

The code uses two stage names. This document calls them "fast" and "deep":

* **fast test** = stage 1, "qualification": `VLESSScannerManager._find_candidates` → `_qualify`
* **deep test** = stage 2, "deep checks": `VLESSScannerManager._deep_check`, then `rank_nodes` / `is_usable`

---

## 1. Overall pipeline

```
config.txt sources ─► download ─► extract vless:// ─► parse_vless (normalize) ─► round-trip check
   ─► deduplicate (fingerprint) ─► MAX_NODES cut ─► order_candidates
   ─► fast test (batches of Xray, stop at CANDIDATE_COUNT) ─► deep test (one Xray for all candidates)
   ─► is_usable filter ─► rank_score sort ─► TOP_COUNT ─► node_result ─► cache file ─► API ─► frontend
```

Entry point: `VLESSScannerManager.run_scan(query)` → `_pipeline(query)`. It is called only from
`ScanService._run` (`scan_service.py`).

| Stage | Where | Purpose |
|---|---|---|
| Preconditions | `_pipeline` | `ensure_xray_binary()` finds Xray (`XRAY_BINARY`, `PATH`, `/opt/homebrew/bin`, `/usr/local/bin`, `/usr/bin`, `./xray`). If none is found, it tries to download Xray v25.1.30 from GitHub. `probes.direct_exit_ip()` gets the scanner's own public IP from the Cloudflare trace. If that fails, the scan fails with `cannot determine the scanner's own IP`. |
| Sources | `read_sources` | Lines of `config.txt` (`CONFIG_FILE`). Blank lines and `#` comments are skipped, and a ` #` suffix is stripped. A line must start with `http://`, `https://` or `vless://`. Duplicate lines are removed. |
| Download | `_download_all_sources`, `_download_source` | aiohttp over certifi TLS, `SOURCE_CONCURRENCY` (20) at a time, `SOURCE_TIMEOUT` (45 s) each. A non-2xx response, a timeout or an exception becomes a failed `SourceResult`. Direct `vless://` lines skip the download. |
| Extraction | `extract_source_nodes`, `extract_vless`, `decode_base64` | Finds every `vless://…` substring (`VLESS_RE`). Only when the body has **none** does it try base64 (standard, then URL-safe alphabet) and search the decoded text. |
| Parsing + normalization | `parse_vless` | Builds a `VLESSNode`, rejects configs the scanner cannot test, drops parameters it does not test, and builds `node.link` (see §5). |
| Round-trip validation | `link_matches_tested_config`, called in `_collect` | Keeps a node only if parsing `node.link` again gives an identical Xray outbound. |
| Deduplication | `_collect` | Keeps the first node for each `node.fingerprint` (see §2.9). |
| Cap + ordering | `_collect`, `order_candidates` | Keeps the first `MAX_NODES` (30000) unique nodes in source order, then interleaves sources (see §2.6). |
| Fast test | `_find_candidates`, `_qualify` | Runs real traffic through Xray until `CANDIDATE_COUNT` (25) nodes pass. |
| Deep test | `_deep_check` | Runs repeated latency probes and a 2 MB download for each candidate. |
| Final result | `rank_nodes`, `is_usable`, `rank_score` | Filters, sorts and keeps `TOP_COUNT` (5). |
| Serialization | `node_result` | `asdict(node)` without `local_port`. |
| Cache | `ScanCache.save` | Writes `DATA_DIR/scan_<all|CC>.json` atomically. |
| API | `routes.py`, `presenter.public_node` | Serves the cached `top` list. |

Failure modes of the whole scan (`ScanFailed`): no Xray; no own IP; `no sources configured`;
`no valid VLESS nodes extracted from sources`. Any other exception in the pipeline is logged as
`Scan crashed` and re-raised as `ScanFailed`.

A scan that finds **zero** working nodes is not a failure. It is cached as an empty `top` with a shorter TTL
(see §6).

---

## 2. Fast testing (stage 1: qualification)

### 2.1 What a fast test is

A fast test sends real HTTPS requests **through** the node. The requests go to a local SOCKS5 port that
Xray opens for that node, then over the node's VLESS outbound to the internet. The test does not stop at a
TCP handshake or protocol handshake. A node passes only if all of the following succeed, in this order
(`_qualify`):

1. **`probes.trace(port, direct_ip)`**: `GET https://www.cloudflare.com/cdn-cgi/trace` (`TRACE_URL`)
   * HTTP status must be `200`.
   * The body is parsed as `key=value` lines. `h` must equal `www.cloudflare.com` (`TRACE_HOST`) and `ip`
     must be present. Otherwise the error is `unexpected trace content`. This rejects captive or stub pages
     that answer 200.
   * `ip` must differ from the scanner's own IP. Otherwise the error is
     `not tunnelled: exit IP is the scanner's own`.
   * Records `exit_ip`, `exit_country` (trace `loc`, upper-cased) and `latency_ms`. The latency is the
     elapsed time of this single request, including the SOCKS handshake and the connection setup through
     the tunnel.
2. **Country filter** (country searches only): if `query.country` is set and the measured `loc` differs,
   the node fails with `exits in XX`. The label's flag emoji is *not* used for this decision (see §2.6).
3. **`probes.reach(port)`**: `GET https://www.gstatic.com/generate_204` (`REACH_URL`) must return
   exactly `204`. Otherwise the error is `second destination unreachable`.
4. **`probes.download(port, QUALIFY_DOWNLOAD_BYTES, QUALIFY_DOWNLOAD_TIMEOUT)`**:
   `GET https://speed.cloudflare.com/__down?bytes=262144` must return `200` with at least 256 KiB of body
   within 10 s. Otherwise the error is `stalls during data transfer`.

On success: `node.success = True`, `node.error = ""`, and `latency_ms` is set from step 1.

Properties of every probe (`probes.fetch`):

* TLS is verified end to end with the certifi CA bundle, *inside* the tunnel. A certificate failure is
  reported as `TLS verification failed (traffic intercepted)`.
* `allow_redirects=False`: a redirect is not followed, and its non-200/204 status fails the check.
* The body is read in 64 KiB chunks up to `max_bytes` (16 KiB by default, or the download size).
* SOCKS with `rdns=True`: the probe's target hostname (e.g. `www.cloudflare.com`) is sent to Xray
  unresolved, so the scanner host's DNS does not resolve it.
* `fetch` never raises. It returns an error string instead: `timeout`, the TLS message above, or the
  exception class name (e.g. `ClientOSError`, `ProxyConnectionError`).

### 2.2 What the fast test does NOT check

* **UDP**: the SOCKS inbound is created with `"udp": false`. UDP traffic through the node is never
  tested, and that includes the `xtls-rprx-vision-udp443` flow's UDP path, QUIC and UDP DNS.
* **Access from any network other than the scanner's.** Every probe starts on the scanner host.
* **IPv6 egress through the node.** Probes only record whatever `ip` Cloudflare reports.
* **Destinations other than the three above**, such as Telegram, YouTube or any service the user cares
  about.
* **Latency ranking.** Stage 1 is first-come: the first 25 nodes that pass are kept, whatever their
  latency.
* **Parameters that `parse_vless` drops** (§5.3). The node is tested *without* them. If the server needs
  one, the node simply fails.
* **Xray readiness.** After starting Xray the code waits a fixed `1.0 s` and checks that the process is
  still alive. It does not check that the SOCKS ports are listening. A slow Xray start therefore causes
  false negatives, not false positives.

### 2.3 How Xray is used

One Xray process is started per batch (`xray_session`). It runs `xray run -config <tempfile>` with stdout
discarded. Stderr is drained in a background task so a full pipe can never block Xray.

Config built by `build_xray_config(nodes)`. For node *i* in the batch:

```jsonc
{
  "log": {"loglevel": "warning"},
  "inbounds": [{
    "tag": "scanner-in-<i>", "listen": "127.0.0.1", "port": PORT_START + i,   // 20000 + i by default
    "protocol": "socks", "settings": {"auth": "noauth", "udp": false}
  }],
  "outbounds": [
    { /* build_outbound(node, "scanner-out-<i>") */ },
    {"tag": "direct", "protocol": "freedom"},
    {"tag": "block",  "protocol": "blackhole"}
  ],
  "routing": {"domainStrategy": "AsIs",
              "rules": [{"type": "field", "inboundTag": ["scanner-in-<i>"], "outboundTag": "scanner-out-<i>"}]}
}
```

`build_outbound(node, tag)`:

```jsonc
{
  "tag": "<tag>", "protocol": "vless",
  "settings": {"vnext": [{"address": node.address, "port": node.port,     // the real server port
                          "users": [{"id": node.uuid, "encryption": "none", "level": 0,
                                     "flow": node.flow /* only if non-empty */}]}]},
  "streamSettings": build_stream_settings(node)
}
```

`build_stream_settings(node)`:

| Field | Value |
|---|---|
| `network` | `"raw"` for tcp, otherwise `ws` / `grpc` / `xhttp` / `httpupgrade` |
| `security` | `none` / `tls` / `reality` |
| `wsSettings` | `path`, `host` (only non-empty keys) |
| `grpcSettings` | `serviceName`; `multiMode: true` only when `mode == "multi"` |
| `xhttpSettings` | `path`, `host`, `mode` |
| `httpupgradeSettings` | `path`, `host` |
| `tlsSettings` | `serverName` (sni), `fingerprint`, `alpn`, `allowInsecure` (only when set) |
| `realitySettings` | `fingerprint` (always; `chrome` if empty), `serverName`, `publicKey`, `shortId`, `spiderX` (only when set) |

Before Xray starts, `validate_nodes_for_xray` resets `local_port` to 0 and removes REALITY nodes that
lack `publicKey` (`REALITY: missing publicKey`) or SNI (`REALITY: missing SNI/serverName`). It also removes
nodes for which `build_stream_settings` raises (`xray config: …`).

**Handling a config Xray rejects.** If the Xray process exits within the first second, the code logs
`Xray refused a batch of %d nodes, isolating bad nodes` and calls `_drop_rejected_nodes`. That function
bisects the batch with `xray run -test -config …` (`_xray_accepts`, which opens no ports) until single bad
nodes are isolated. Each bad node gets `node.error = "xray rejected config: …"` and the log line
`Xray rejected node host:port — …`. The remaining nodes are started again. If the second start also fails,
`XrayStartError` is raised, and `_find_candidates` logs `Skipping a batch of %d nodes` and moves on to the
next batch.

When the session ends, Xray gets SIGTERM, then SIGKILL after 2 s, and the temp config is deleted.

### 2.4 Connection parameters and timeouts

| Setting | Default | Used for |
|---|---|---|
| `TIMEOUT` | 4.0 s | trace (per request) |
| `TIMEOUT + 1` | 5.0 s | reach |
| `QUALIFY_DOWNLOAD_BYTES` / `_TIMEOUT` | 256 KiB / 10 s | fast download |
| hard ceiling in `fetch` | timeout + 0.5 s | `asyncio.wait_for` around each request (python-socks ignores aiohttp's timeout during the SOCKS handshake) |
| Xray start grace | 1.0 s | fixed sleep before probing |
| `direct_exit_ip` | 10 s | trace without proxy |

All of these except the grace period and the ceiling can be overridden through environment variables where
`config.py` reads them with `os.getenv`. `TIMEOUT` can be overridden. `QUALIFY_DOWNLOAD_*` and `DEEP_DOWNLOAD_*`
are constants.

### 2.5 Success and failure

Success means all four steps in §2.1 passed. Failure is the first failing step, and its reason is stored in
`node.error`. **Failed nodes are not persisted and their errors are not logged** (the Xray-rejection case is
the only exception). The errors exist only in memory for the duration of the scan.

### 2.6 Candidate selection and order

`_collect` returns unique nodes in source order: direct `vless://` lines from `config.txt` come first, then
downloaded sources in `config.txt` order. It then keeps the first `MAX_NODES` (30000).

`order_candidates` then:

* interleaves the sources round-robin (`zip_longest` over per-source lists), so one large source cannot fill
  every early slot;
* for a country search, stable-sorts nodes whose label flag (`label_country`, taken from the first flag
  emoji in the `#name`) equals the requested country to the front. This only affects **order**. The country
  is still verified from the trace `loc` (§2.1 step 2).

### 2.7 How many candidates are tested

`_find_candidates` walks the ordered list in batches of `BATCH_SIZE` (500) nodes, with one Xray process per
batch and batches run **sequentially**. It stops starting new batches, and stops pulling new nodes inside a
batch, once `len(found) >= CANDIDATE_COUNT` (25).

Checks already in flight are allowed to finish. Nodes that pass after the limit is reached are not added.
In the worst case every unique node, up to 30000, is tested. `processed_candidates` counts the nodes
that were tested or skipped. It is reported in the API as `tested`.

### 2.8 Concurrency

* Within a batch, `_for_each` runs `min(WORKERS, len(usable))` coroutines (`WORKERS` = 300). Each one pops
  the next node from a shared queue and runs `_qualify` on it. The three or four requests of a single
  node's test run sequentially.
* Only one scan runs at a time. `VLESSScannerManager.lock` (an `asyncio.Lock`) serializes scans in the
  process, and `ScanService._run` also takes an exclusive `fcntl` lock on `DATA_DIR/scan.lock`, so other
  processes that share `DATA_DIR` wait as well. Local SOCKS ports (`PORT_START…`) are therefore never shared
  between two scans in one process.

### 2.9 Duplicate removal

`parse_vless` computes `fingerprint = sha256(json.dumps(identity, sort_keys=True))[:20]` over:
`uuid, address (lower-cased), port, network, security, flow, sni, fp, alpn, insecure, pbk, sid, spx, path,
host, serviceName, mode`.

These are the values *after* normalization (§5), so two links that differ only in parameter aliases, dropped
parameters, `&amp;`, percent-encoding or the `#name` label count as one node. `_collect` keeps the
**first** occurrence. `fingerprint` is also exposed as `id` in the public API.

With `DEBUG=true`, `build_xray_config` also prints nodes that share `address:port` (`[Duplicate Check] …`).
This is informational only; such nodes are not removed.

---

## 3. Deep testing (stage 2)

### 3.1 How it differs from the fast test

| | Fast (`_qualify`) | Deep (`_deep_check`) |
|---|---|---|
| Nodes | Every node, in order, until 25 pass | Only the ≤ 25 candidates |
| Xray | One process per 500-node batch | **One new** process for all candidates. Local ports are reassigned from `PORT_START`. |
| Trace | 1 request (country checked) | `DEEP_ATTEMPTS` (5) requests per node, sequential, 0.3 s apart. The country is **not** re-checked. |
| Second destination | `generate_204` | not repeated |
| Download | 256 KiB / 10 s | 2 MiB (`DEEP_DOWNLOAD_BYTES`) / 25 s (`DEEP_DOWNLOAD_TIMEOUT`) |
| Concurrency | up to 300 | latency: all candidates at once; download: `DEEP_DOWNLOAD_CONCURRENCY` (4) at a time, so the downloads do not compete for the scanner's own bandwidth |
| Result | pass/fail | samples, median latency, jitter, throughput, then filter and rank |

### 3.2 When it runs

It runs always, immediately after stage 1, provided stage 1 found at least one candidate (`_pipeline`:
`if candidates: await self._deep_check(...)`). There is no other trigger.

### 3.3 What it measures

For each usable candidate (`latency` then `speed` inner functions):

* `latency_samples`: the `latency_ms` of every successful `probes.trace` out of 5. Each attempt uses the
  same checks as stage 1 (status 200, Cloudflare `h`, exit IP ≠ scanner IP) with `TIMEOUT` = 4 s.
* `attempts = 5`; `stable = (all 5 succeeded)`; `latency_ms = median(samples)`;
  `jitter_ms = pstdev(samples)` if there are at least 2 samples.
* `speed_mbps = probes.download(port, 2 MiB, 25 s)`. This is `None` if the download returned non-200, came
  up short, or timed out. The elapsed time includes connection setup.

A real Xray connection and real internet traffic through the node are used, exactly as in stage 1. If a
candidate is rejected by Xray in the new session (bisection as in §2.3), it gets no samples and no speed.

### 3.4 Final decision

```python
is_usable(n)  = len(n.latency_samples) >= DEEP_MIN_SUCCESS (4) and n.latency_ms is not None and bool(n.speed_mbps)
rank_score(n) = n.latency_ms * (1 + 2 * (1 - len(samples)/attempts)) + 8000 / n.speed_mbps
top           = sorted(filter(is_usable, candidates), key=rank_score)[:TOP_COUNT]   # TOP_COUNT = 5
```

`8000 / speed_mbps` is the time in ms to fetch 1 MB at the measured speed. Each failed latency attempt
raises the latency weight by 40 %. Lower is better.

### 3.5 Guarantee that the user's link is the tested config

1. `node.link` is built from the same node fields that `build_outbound` uses (§5.1).
2. `_collect` drops a node unless `parse_vless(node.link)` yields an **identical** outbound
   (`link_matches_tested_config`).
3. Stages 1 and 2 test the same `VLESSNode` objects. Nothing modifies the config fields between parsing
   and caching. Only `local_port` and the measurement fields change.
4. The cached `link` is that `node.link`, and the API returns it unchanged. `/api/vless/sub?rename=true`
   rewrites only the `#fragment` (name) via `customize_vless_url`.

The "critical" parameters are exactly those that affect the outbound: `uuid, address, port, flow, network,
security, sni, fp, alpn, allowInsecure, pbk, sid, spx, path, host, serviceName, mode`. `encryption` is
always `none`. The `#name` is not part of the comparison.

### 3.6 Per transport and security mode

Supported transports are `tcp` (alias `raw`), `ws` (`websocket`), `grpc`, `xhttp` (`splithttp`) and
`httpupgrade` (`http-upgrade`). The transport comes from `type` or `network`. Any other transport (e.g.
`kcp`, `quic`, `h2`/`http`) raises `unsupported transport` and the link is skipped.

| Mode | Parsing rules (`parse_vless`) | Tested as |
|---|---|---|
| `security=none` | Anything other than `tls`/`reality`, including empty, becomes `none`. `sni` and `fp` are cleared. | plain stream |
| `security=tls` | Keeps `sni`, `fp` (only known values), `alpn` (only `h2`, `http/1.1`, `h3`), `allowInsecure` | `tlsSettings` |
| `security=reality` | `sid` must match `^([0-9a-f]{2}){0,8}$` (case-insensitive), otherwise the link is rejected. A non-empty `pbk` must decode to 32 bytes (base64url), otherwise the link is rejected. `fp` defaults to `chrome`. `alpn` and `allowInsecure` are dropped. At test time, missing `pbk` or `sni` → not tested (§2.3). | `realitySettings` |
| `tcp` | `path`, `host`, `serviceName`, `mode` dropped. `headerType` is ignored. | `network: "raw"` |
| `ws` | `path`, `host` kept | `wsSettings` |
| `grpc` | `serviceName` kept. `mode` becomes `multi` only if it equals `multi` (case-insensitive), otherwise it is empty. | `grpcSettings` |
| `xhttp` | `path`, `host`, `mode` (verbatim) kept. `extra` is ignored. | `xhttpSettings` |
| `httpupgrade` | `path`, `host` kept | `httpupgradeSettings` |
| `flow` | Must be empty, `xtls-rprx-vision` or `xtls-rprx-vision-udp443`. Anything else (e.g. `xtls-rprx-direct`) → the link is rejected. | `users[0].flow` |
| `encryption` | Values starting with `mlkem` (VLESS Encryption) → the link is rejected. Every other value is treated as `none`. | `"encryption": "none"` |

`xray run -test` in the bisection (§2.3) is the final authority. Anything Xray still refuses is dropped and
its reason logged.

---

## 4. False-positive protection, and why a passing link can still fail for a user

### 4.1 What the probes rule out

| Situation | Caught by |
|---|---|
| Node never tunnels, so traffic leaves from the scanner's IP | trace `ip` ≠ `direct_ip` |
| Captive portal or stub page answering 200 | trace body must contain `h=www.cloudflare.com` |
| Interception or MITM inside the tunnel | certifi-verified HTTPS through the tunnel |
| Server allows only a few hosts | `generate_204` must return 204 |
| DPI lets small requests through and stalls bulk data | 256 KiB (stage 1) and 2 MiB (stage 2) downloads |
| Flaky nodes | ≥ 4 of 5 traces in stage 2 |
| Exit country different from the label | country taken from the trace `loc`, not the label |
| Share link differs from the tested config | round-trip check (§5.7) |

### 4.2 Remaining limitations

* **Time.** Results are cached for up to 24 h (`CACHE_TTL_SECONDS`), and a stale cache is still served
  while a rescan runs (§6.6). Public nodes go offline, rotate UUIDs or change keys and ports within that
  window. Nothing re-checks a cached node before it is shown.
* **Network position.** Every probe starts on the scanner server. A node that works from the server can be
  blocked, throttled or DPI-filtered on the user's ISP or mobile network, or in the user's country. The
  scanner cannot see this. It has no probes from other vantage points.
* **Server address resolution.** Xray on the scanner host resolves the node's `address` with the host's
  DNS. The user's device resolves it with its own DNS, which may return different IPs (geo-DNS or CDN
  fronting) or be filtered.
* **UDP is untested** (§2.2). Apps that rely on UDP or QUIC may fail over a node that passed.
* **Only three destinations are tested**: Cloudflare trace, gstatic, Cloudflare speed. Other destinations
  may be blocked by the node operator.
* **Client differences.** The link carries only the parameters the scanner tested. When a parameter is
  absent (e.g. no `fp` for a TLS node, or no `alpn`), the scanner used Xray's default. A client that
  applies its own defaults, or a non-Xray core that interprets a transport differently, may behave
  differently.
* **Load.** At most 5 traces and one 2 MiB download are sent per node. Behaviour under sustained use by
  many users of the same public node is not measured.
* **Exit country in stage 2.** The country is checked only in stage 1. A load-balanced node that changes
  its exit between stages keeps the stage-1 `exit_country`.
* **Single sample of throughput.** `speed_mbps` comes from one download and depends on the scanner's own
  bandwidth and on 4 downloads running in parallel.

---

## 5. Final VLESS link generation

### 5.1 Where the parameters come from

`build_vless_uri(node)` (called at the end of `parse_vless`) serializes **only** `VLESSNode` fields, and
those same fields feed `build_outbound`. Nothing is copied from the source link. `node.params`, the raw
flattened query, is kept on the node for reference but is never used to build the link or the Xray config.

```
vless://<quote(uuid)>@<host>:<port>?encryption=none&type=<network>&security=<security>
        [&flow][&path][&host][&serviceName][&mode][&sni][&fp][&alpn][&allowInsecure=1][&pbk][&sid][&spx]
        [#<quote(name)>]
```

The keys always appear in this order, and each optional key appears only when its value is non-empty.

### 5.2 Normalization

* Aliases are resolved to one canonical key. Transport: `type`/`network`. SNI: `sni`/`servername`/`serverName`.
  Fingerprint: `fp`/`fingerprint`. REALITY: `pbk`/`publicKey`, `sid`/`shortId`, `spx`/`spiderX`.
  Insecure: `allowInsecure`/`allowinsecure`. Transport names: `raw → tcp`, `websocket → ws`,
  `splithttp → xhttp`, `http-upgrade → httpupgrade`.
* `normalize_network` also repairs broken values in which the next link or a `#`, `@`, `&` or `?` has been
  glued onto `type`.
* `security` and `fp` are lower-cased. An unknown `fp` becomes empty (REALITY: `chrome`). `alpn` is
  filtered and lower-cased. `allowInsecure` is true for `1/true/yes/on` and is serialized as `1`.
* `encryption` is always written as `none`.
* Name: `unquote`, control characters removed, trimmed, truncated to 100 characters.
* The query is parsed with `urllib.parse.parse_qs`, which percent-decodes values and turns `+` into a space.
  The first value wins for repeated keys.

### 5.3 Discarded parameters

The rule is: anything not listed in §5.1, and any listed parameter that does not apply to the node's
transport or security mode (table in §3.6). Examples covered by
`test_parameters_the_scanner_does_not_test_are_not_shared`: `headerType`, `extra`, `path`/`host`/`serviceName`
on tcp, `alpn`/`allowInsecure` on REALITY, and `sni`/`fp`/`pbk` on `security=none`. Other unknown keys (e.g.
`authority`, `pqv`, `ech`) are dropped the same way, because the parser never reads them.

### 5.4 URL encoding

* `uuid` uses `quote(uuid, safe='')`. The name uses `quote(name, safe='')`.
* The query uses `urlencode(params, quote_via=quote, safe='')`, which percent-encodes every reserved
  character in every value (`/` → `%2F`, `,` → `%2C`, `&` → `%26`, `#` → `%23`, `+` → `%2B`, space → `%20`).
* The link is strict RFC 3986. `test_share_link_reproduces_the_tested_config` asserts that only unreserved,
  reserved or `%` characters appear outside an IPv6 host.

### 5.5 IPv6

`urlsplit(...).hostname` strips the brackets (and lower-cases the host). `build_vless_uri` adds brackets
again whenever the address contains `:`, as in `vless://…@[2001:db8::1]:8443?…`. Xray's outbound receives
the bare address.

### 5.6 `&amp;`

`clean_vless_candidate` (called by `extract_vless` and again at the start of `parse_vless`) replaces every
`&amp;` with `&`. It also strips trailing `.,;)]}"'` and whitespace, and removes control characters.
Without this, links scraped from HTML would lose every parameter after the first `&amp;`.

### 5.7 REALITY `sid`

* The value is read from `sid` or `shortId` and whitespace-stripped. It must be 0–16 hex characters of even
  length, otherwise the link is rejected (`invalid REALITY shortId`).
* An **empty** `sid` is allowed. It is omitted from both `realitySettings` and the link, so the user's client
  sends the same (empty) shortId that was tested.
* A non-empty `sid` is passed through verbatim (case preserved) to both.

### 5.8 Why `local_port` (20000+) cannot appear in the user link

* `build_vless_uri` uses `node.port`, the server port parsed from the source. `local_port` is not an input.
* `node.link` is built inside `parse_vless`, before any port is assigned. `build_xray_config` assigns
  `local_port = PORT_START + i` later, and only the SOCKS *inbound* uses it. `build_outbound` uses
  `node.port`.
* `node_result` deletes `local_port` before the node is cached, so the port is never stored or served.
* This is covered by `test_share_link_uses_the_real_server_port_not_the_scanner_port`.

### 5.9 Round-trip validation

`link_matches_tested_config(node)`:

```python
again = parse_vless(node.link)                       # ValueError → False
return build_outbound(again, "check") == build_outbound(node, "check")
```

It runs once per parsed node in `_collect`. On a mismatch the node is skipped with the warning
`Skipping host:port: its share link would not reproduce the tested config`. The tests also check that
generation is idempotent (`parse_vless(node.link).link == node.link`) across REALITY, ws-tls, gRPC multi,
xhttp, httpupgrade, IPv6, HTML-escaped and unencoded-path links.

---

## 6. Caching

### 6.1 What is cached

`ScanService` keeps one JSON file per query in `DATA_DIR` (default `data/`): `scan_all.json` for the main
results and `scan_<CC>.json` for each country search. `ScanCache.save` writes an entry:

```jsonc
{
  "schema": 2, "scanner_version": "3.1",
  "created_at": "…Z", "created_ts": …, "expires_at": "…Z", "expires_ts": …, "count": <len(top)>,
  "country": null | "DE", "duration_seconds": …, "total_nodes": …, "tested": …, "working": …,
  "sources": [{"url", "success", "vless_count", "error"}, …],
  "top": [ node_result(node), … ]   // ≤ TOP_COUNT
}
```

Each `top` item is `asdict(VLESSNode)` minus `local_port`. It contains **every** other field, including
`source_link`, `params`, `uuid`, `exit_ip`, `latency_samples`, and so on. Writes are atomic: the entry is
written to a `.tmp` file and then moved into place with `os.replace`.

### 6.2 TTL

* `CACHE_TTL_SECONDS` defaults to 86400 (24 h).
* A result with an empty `top` gets `min(TTL, 3600)` (`EMPTY_RESULT_TTL_SECONDS`).
* After a failed scan (`ScanFailed`), non-forced requests return `cooldown` for 5 min
  (`FAILURE_COOLDOWN_SECONDS`). The error is kept only in memory.
* Visitor refresh (`POST /api/cache/refresh`) can happen at most once per `REFRESH_COOLDOWN_SECONDS`
  (600 s) globally.

### 6.3 Scanner versioning

`ScanCache.load` returns `None`, and logs `Ignoring <file> from scanner version '<v>'`, when
`entry["schema"] != 2` or `entry["scanner_version"] != config.SCANNER_VERSION`. Bumping `SCANNER_VERSION`
therefore makes every existing cache file behave as missing, so the next request or the background refresher
starts a new scan.

Old files are **not deleted**. They are overwritten by the next successful scan for that query. For
example, `data/scan_all.json` and `data/scan_DE.json` in the current working copy were written by version
`3.0` and still contain `local_port`. Version 3.1 ignores them.

Bump `SCANNER_VERSION` whenever parsing, testing, ranking or the cached node shape changes.

### 6.4 Data that must not reach users

* `local_port`: removed by `node_result`, so it is never cached (§5.8).
* `source_link` (the original, un-normalized link) and `params` (the raw query): the dataclass comment
  says "never shown to users". `public_node`, used by `/api/results` and `/api/vless/search/{country}`,
  does not include them. **They are still stored in the cache file, and `GET /api/vless/top` returns the
  raw cached node dicts, so that endpoint does expose `source_link`, `params` and `exit_ip`.** The frontend
  does not call `/api/vless/top`.
* Scan failure details: `status().last_scan_failed` is a boolean only. The message is available only at
  `GET /api/admin/stats` (`last_error`, requires `ADMIN_TOKEN`) and in the server log.

### 6.5 Public API fields

`public_node` returns: `id` (fingerprint), `rank`, `link`, `label` (`clean_label(name)`),
`country` (`exit_country`), `address`, `port`, `network`, `security`, `sni`, `flow`, `tls_fingerprint`,
`latency_ms`, `jitter_ms`, `speed_mbps`, `stable`, `checks_passed`, `checks_total`.

### 6.6 Effect on repeated searches

* `GET /api/results` always serves the cached `top`, whether it is fresh or stale, and calls
  `request_scan()`. That call starts a background scan only when the cache is missing or expired, no scan
  is running, and there is no failure cooldown.
* Single-flight: while a scan for a query is running, further requests `join` it instead of starting
  another. `POST /api/scan` behaves the same way and is rate-limited per IP
  (`SCAN_RATE_LIMIT_PER_MINUTE`, default 6).
* When `AUTO_REFRESH` is on (the default), `refresh_forever` rescans the **main** cache as soon as it
  expires. Country caches are rescanned only when requested. They are served stale (200) while the rescan
  runs, and 202 is returned only when no cache exists yet. At most `MAX_QUEUED_SCANS` (3) country scans may
  be running or queued.
* A scan that waited for the file lock skips itself if another process refreshed the cache meanwhile.
* Consequence: two searches within the TTL return the same nodes and the same measurements, even if a node
  has died since.

---

## 7. How to diagnose why a specific VLESS link failed

The scanner does not record why an individual node failed (§2.5), so a failed link has to be reproduced.

### 7.1 Check the server log

The app logs with the format `%(asctime)s %(levelname)s %(name)s: %(message)s`. The scanner logs as
`skat.scanner` and the cache as `skat.cache`.

| Message | Meaning |
|---|---|
| `Skipping H:P: its share link would not reproduce the tested config` | the round-trip check failed |
| `Xray refused a batch of N nodes, isolating bad nodes` | bisection started |
| `Xray rejected node H:P — xray rejected config: …` | Xray refuses this exact config; the reason is Xray's |
| `Skipping a batch of N nodes` (with traceback) | Xray could not start even after bisection |
| `[all] N unique nodes from M sources` | after parse, round-trip and dedup |
| `[all] N candidates after checking M nodes` | end of stage 1 |
| `[all] scan finished in Xs: best N of M candidates` | end of stage 2 |
| `Scan crashed` | unexpected exception, logged with traceback |
| `[key] starting scan (cache: empty/stale/fresh)` / `[key] scan failed: …` | `ScanService._run` |
| `Ignoring scan_all.json from scanner version '3.0'` | version mismatch (§6.3) |

Start the app with `DEBUG=true` to get DEBUG-level logging plus the stdout prints `[Port Mapping] …`
(first 5 nodes per batch), `[Duplicate Check] …` and `[Scanner] …` (Xray auto-download):

```bash
DEBUG=true PYTHONPATH=. uvicorn app.main:app --port 8000
```

A link that fails parsing (`ValueError`) is skipped **silently** in `_collect`.

### 7.2 Check the API

```bash
curl -s localhost:8000/api/scan/status        # stage, progress, processed/working counts, last_scan_failed
curl -s localhost:8000/api/sources            # per-source success / HTTP error / timeout / vless_count
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" localhost:8000/api/admin/stats   # last_error
curl -s -X POST -H "Authorization: Bearer $ADMIN_TOKEN" localhost:8000/api/admin/scan   # force rescan
```

### 7.3 Reproduce one link against the real pipeline functions

This ad-hoc script is not part of the repository. It calls the same functions a scan uses, in the same
order. Run it from the repo root with `PYTHONPATH=.`. It needs the Xray binary and outbound internet.

```python
import asyncio
from app.services import probes
from app.services.scanner import (VLESSScannerManager, ScanQuery, is_usable,
                                  link_matches_tested_config, parse_vless)

LINK = "vless://…"

async def main():
    node = parse_vless(LINK)  # ValueError here = rejected at parse time (message says why)
    print("share link:", node.link)
    print("round-trip ok:", link_matches_tested_config(node))
    m = VLESSScannerManager()
    m.direct_ip = await probes.direct_exit_ip()
    xray = m.ensure_xray_binary()
    async with m.xray_session(xray, [node]) as usable:
        if not usable:
            print("not tested:", node.error)  # REALITY missing pbk/sni, or xray rejected config: …
            return
        ok = await m._qualify(node, ScanQuery())  # ScanQuery(country="DE") for a country search
        print("fast:", ok, node.error or "", node.exit_ip, node.exit_country, node.latency_ms)
    if ok:
        await m._deep_check(xray, [node])
        print("deep:", node.latency_samples, node.speed_mbps, "usable:", is_usable(node))

asyncio.run(main())
```

How to read the `fast:` error: see §2.1. Common values are `timeout`, an exception class name such as
`ClientOSError` or `ProxyConnectionError` (the connection through Xray failed), `HTTP 4xx/5xx`,
`unexpected trace content`, `not tunnelled…`, `TLS verification failed (traffic intercepted)`,
`exits in XX`, `second destination unreachable` and `stalls during data transfer`.

To see Xray's own verdict on the generated config, write `build_xray_config([node])` to a file and run
`xray run -test -config that.json`. This is the command `_xray_accepts` runs.

### 7.4 Useful breakpoints

* `parse_vless`: what was kept or dropped (compare `node.params` with the node fields).
* `link_matches_tested_config`: compare the two outbounds on a mismatch.
* `VLESSScannerManager.xray_session`: startup and bisection.
* `VLESSScannerManager._qualify`: the first failing step.
* `VLESSScannerManager._deep_check`, `is_usable`, `rank_score`: why a candidate did not reach the top 5.

### 7.5 Tests

```bash
PYTHONPATH=. pytest -q              # backend: parsing/links (tests/test_scanner.py), cache, API
cd frontend && npm test && npm run lint && npm run typecheck
```

`tests/test_scanner.py` simulates the network with a fake `probes.fetch` (the `Network` class) and
covers every false-positive case in §4.1. `test_xray_accepts_every_share_link_shape` and the bisection
test run only when an Xray binary is present.

---

## 8. Architecture

```
config.txt (URLs + direct vless:// lines)
        │  read_sources
        ▼
Download sources (aiohttp, 20 parallel, 45 s)
        │  extract_source_nodes  (plain text → else base64)
        ▼
Parse + normalize  (parse_vless: reject untestable, drop untested params, canonical keys)
        │
        ▼
Round-trip validation  (link_matches_tested_config)
        │
        ▼
Deduplicate  (fingerprint of normalized identity; first wins) → first MAX_NODES
        │
        ▼
Order  (interleave sources; country-label hint first)
        │
        ▼
Fast test  ── per 500-node Xray batch, 300 workers, stop at 25 ──
        │     trace(200, h=cloudflare, ip≠own) → [country] → generate_204 → 256 KiB download
        ▼
Deep test  ── one Xray for ≤25 candidates ──
        │     5× trace (latency, jitter, stable) → 2 MiB download (4 at a time)
        ▼
is_usable (≥4/5, speed) → rank_score → top 5
        │
        ▼
node_result (drop local_port)  → cache  data/scan_<all|CC>.json  (TTL 24 h / 1 h if empty, SCANNER_VERSION)
        │
        ▼
API  /api/results, /api/vless/search/{CC}  (public_node) · /api/vless/sub, /raw, /top (raw cache)
        │
        ▼
Frontend  (fetchResults → cards with node.link, QR, subscription URL)
```
