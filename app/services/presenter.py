"""Shape internal scan data into the public API format."""
import re

_NOISE_RE = re.compile(r"(https?://\S+|t\.me/\S+|@\w+)", re.IGNORECASE)
_SYMBOLS_RE = re.compile(r"[^\w\s.,:()\-–|/+]", re.UNICODE)


def clean_label(name: str) -> str:
    """Source labels often carry ads and emoji; keep a short readable remainder."""
    text = _NOISE_RE.sub(" ", name or "")
    text = _SYMBOLS_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" -|/")
    return text[:48]


def _round(value, digits=0):
    return None if value is None else round(value, digits) if digits else round(value)


def public_node(node: dict, rank: int) -> dict:
    samples = node.get("latency_samples") or []
    return {
        "id": node["fingerprint"],
        "rank": rank,
        "link": node["link"],
        "label": clean_label(node.get("name", "")),
        # Where traffic actually leaves the tunnel, measured during the scan.
        "country": node.get("exit_country") or None,
        "address": node["address"],
        "port": node["port"],
        "network": node.get("network") or "tcp",
        "security": node.get("security") or "none",
        "sni": node.get("sni") or None,
        "flow": node.get("flow") or None,
        "tls_fingerprint": node.get("fingerprint_tls") or None,
        "latency_ms": _round(node.get("latency_ms")),
        "jitter_ms": _round(node.get("jitter_ms")),
        "speed_mbps": _round(node.get("speed_mbps"), 1),
        "stable": bool(node.get("stable")),
        "checks_passed": len(samples),
        "checks_total": max(node.get("attempts") or 0, len(samples)),
    }
