"""The final ranking the public API serves: backend verification first, probe confirmations second.

1. Eligibility is decided by the backend alone: only configs the Xray deep check found
   usable (scanner.is_usable: ≥ DEEP_MIN_SUCCESS latency checks passed and a multi-MB
   download completed) can be shown. Probes cannot promote a config that does not work.
2. Among those, the primary key is the probe-confirmation score: the number of providers
   whose approved probes confirmed the config's endpoint reachable (ProbeService.confirmations).
   4/4 providers → 4 points, 1/4 → 1 point.
3. Ties keep the backend's order (scanner.rank_score: latency penalised by failed checks
   plus the time to fetch 1 MB). With no probe results at all, the order is exactly the
   backend's, as before.

Computed on every read from the cache entry and the live probe results, so the cache never
holds a stale ranking: a result that arrives, expires, or is withdrawn changes the order on
the next request. The cache file itself is never rewritten.
"""
import logging
from typing import Callable, Optional

from app import config

logger = logging.getLogger("skat.ranking")

Confirmations = Callable[[list[str]], dict[str, dict]]


def usable(node: dict) -> bool:
    """scanner.is_usable, on the cached dict form of a node."""
    return (
        len(node.get("latency_samples") or []) >= config.DEEP_MIN_SUCCESS
        and node.get("latency_ms") is not None
        and bool(node.get("speed_mbps"))
    )


def candidates(entry: Optional[dict]) -> list[dict]:
    """Usable configs of a scan in the backend's order."""
    if not entry:
        return []
    ranked = entry.get("ranked")
    if ranked:
        return [n for n in ranked if usable(n)]
    return list(entry.get("top") or [])  # caches written before "ranked" existed


def rank(nodes: list[dict], confirmations: dict[str, dict], use_score: Optional[bool] = None) -> list[dict]:
    """Sort by (-score, backend position). Every node gets probe_score / probe_total."""
    use_score = config.RANKING_USE_PROBE_SCORE if use_score is None else use_score
    scored = []
    for position, node in enumerate(nodes):
        conf = confirmations.get(node["fingerprint"]) or {}
        scored.append((position, {**node, "probe_score": int(conf.get("score") or 0),
                                  "probe_total": int(conf.get("total") or 0),
                                  "probe_providers": list(conf.get("providers") or []),
                                  "backend_rank": position + 1}))
    if use_score:
        scored.sort(key=lambda item: (-item[1]["probe_score"], item[0]))
    return [node for _, node in scored]


def final_top(entry: Optional[dict], confirm: Confirmations, limit: Optional[int] = None) -> list[dict]:
    """The top `limit` (TOP_COUNT) configs of a cached scan, probe-confirmed ones first."""
    limit = config.TOP_COUNT if limit is None else limit
    nodes = candidates(entry)
    if not nodes:
        return []
    try:
        confirmations = confirm([n["fingerprint"] for n in nodes])
    except Exception:
        # Probe data is a bonus: without it, serve the backend's own ranking.
        logger.exception("probe confirmations unavailable; serving the backend ranking")
        confirmations = {}
    return rank(nodes, confirmations)[:limit]
