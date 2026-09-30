import base64
import logging
import re

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response
from fastapi.responses import PlainTextResponse

from app import config
from app.admin.auth import require_admin  # noqa: F401 - re-exported for older imports
from app.api.rate_limit import client_ip, scan_limiter
from app.probes import service as probe_service_module
from app.services import ranking
from app.services.presenter import public_node
from app.services.scan_service import scan_service
from app.services.scanner import ScanQuery
from app.services.subscription import generate_subscription_content

router = APIRouter()
logger = logging.getLogger("skat.api")


def _cached_top(query: ScanQuery = ScanQuery(), limit: int = 0) -> list[dict]:
    """The served ranking: backend-verified configs, probe-confirmed ones first (ranking.py)."""
    entry = scan_service.entry(query)
    # Like the cache's own top list, never more than TOP_COUNT, whatever `limit` asks for.
    limit = min(limit, config.TOP_COUNT) if limit else config.TOP_COUNT
    return ranking.final_top(entry, probe_service_module.probe_service.confirmations, limit)


def _public_nodes(nodes: list[dict]) -> list[dict]:
    """Public cards, with provider reachability for each. Reads stored results only."""
    try:
        reach = probe_service_module.probe_service.reachability([n["fingerprint"] for n in nodes])
    except Exception:
        logger.exception("provider reachability unavailable")
        reach = {}
    return [{**public_node(node, rank), "reachability": reach.get(node["fingerprint"], [])}
            for rank, node in enumerate(nodes, 1)]


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/results")
async def get_results(response: Response):
    """Cached scan results. Starts a background scan when the cache is missing or expired."""
    _no_store(response)
    scan_service.request_scan()
    return {
        **scan_service.status(),
        "nodes": _public_nodes(_cached_top()),
    }


@router.get("/scan/status")
async def get_scan_status(response: Response):
    """Lightweight status for polling while a scan runs."""
    _no_store(response)
    return scan_service.status()


@router.post("/scan")
async def request_scan(request: Request, response: Response):
    """Ask for fresh results. Returns immediately; a scan only runs if the cache is stale."""
    _no_store(response)
    scan_limiter.check(client_ip(request))
    outcome = scan_service.request_scan(trigger="visitor")
    return {"outcome": outcome, **scan_service.status()}


@router.post("/cache/refresh")
async def refresh_cache(request: Request, response: Response):
    """Ignore today's cache and rescan now. Allowed once per REFRESH_COOLDOWN_SECONDS for everyone."""
    _no_store(response)
    retry_after = scan_service.refresh_retry_after(config.REFRESH_COOLDOWN_SECONDS)
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="Results were refreshed recently",
            headers={"Retry-After": str(retry_after)},
        )
    scan_limiter.check(client_ip(request))
    outcome = scan_service.refresh()
    return {"outcome": outcome, "retry_after": config.REFRESH_COOLDOWN_SECONDS, **scan_service.status()}


COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")


@router.get("/vless/search/{country}")
async def search_by_country(
    request: Request,
    response: Response,
    country: str = Path(description="ISO 3166-1 alpha-2 code of the exit country, e.g. DE"),
):
    """The same pipeline as the main results, limited to configs that exit in `country`.

    200 with the best configs when a cached result exists (fresh, or stale while it is
    being refreshed); 202 while the first search for this country is queued or running.
    Poll the same URL until it returns 200.
    """
    if not COUNTRY_RE.match(country):
        raise HTTPException(status_code=422, detail="country must be a two-letter ISO code, e.g. DE")
    query = ScanQuery(country=country.upper())
    _no_store(response)

    # Only requests that would really launch a scan count against the limit, so
    # polling a running or cached search stays free.
    if scan_service.would_start(query):
        scan_limiter.check(client_ip(request))
    outcome = scan_service.request_scan(query)
    if outcome == "busy":
        raise HTTPException(
            status_code=503,
            detail="Too many country searches in progress, try again shortly",
            headers={"Retry-After": "60"},
        )

    status = scan_service.status(query)
    if status["cache_state"] == "empty" and status["is_scanning"]:
        response.status_code = 202
    return {
        "country": query.country,
        "outcome": outcome,
        **status,
        "nodes": _public_nodes(_cached_top(query)),
    }


# Kept for the previous web UI and existing scripts.
router.add_api_route("/scan/start", request_scan, methods=["POST"], include_in_schema=False)


@router.get("/vless/top")
async def get_top_vless(limit: int = Query(default=5, ge=1, le=50)):
    """Top working VLESS nodes (internal format)."""
    nodes = _cached_top(limit=limit)
    entry = scan_service.entry()
    return {
        "status": "success",
        "count": len(nodes),
        "timestamp": entry["created_at"] if entry else None,
        "nodes": nodes,
    }


@router.get("/vless/sub")
async def get_vless_subscription(
    format: str = Query(default="base64", pattern="^(base64|raw)$"),
    rename: bool = Query(default=True),
    limit: int = Query(default=5, ge=1, le=50),
):
    """Subscription for clients (v2rayNG, NekoBox, Hiddify, Streisand, sing-box...)."""
    content = generate_subscription_content(_cached_top(limit=limit), format_type=format, rename=rename)
    title = base64.b64encode("Скат".encode()).decode()
    headers = {
        "Subscription-Userinfo": "upload=0; download=0; total=1073741824000; expire=0",
        "Profile-Title": f"base64:{title}",
        "Profile-Update-Interval": "24",
    }
    return Response(content=content, media_type="text/plain; charset=utf-8", headers=headers)


@router.get("/vless/raw", response_class=PlainTextResponse)
async def get_vless_raw(limit: int = Query(default=5, ge=1, le=50)):
    """Top VLESS links as plain text, one per line."""
    return "\n".join(n["link"] for n in _cached_top(limit=limit) if n.get("link"))


@router.get("/sources")
async def get_sources():
    """Subscription sources from config.txt and their state in the last scan."""
    sources = scan_service.scanner.read_sources()
    entry = scan_service.entry()
    return {
        "count": len(sources),
        "sources": sources,
        "sources_status": entry["sources"] if entry else [],
    }
