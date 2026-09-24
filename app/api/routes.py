import base64
import hmac
import re

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request, Response
from fastapi.responses import PlainTextResponse

from app import config
from app.api.rate_limit import client_ip, scan_limiter
from app.services.presenter import public_node
from app.services.scan_service import scan_service
from app.services.scanner import ScanQuery
from app.services.subscription import generate_subscription_content

router = APIRouter()


def _cached_top(query: ScanQuery = ScanQuery()) -> list[dict]:
    entry = scan_service.entry(query)
    return entry["top"] if entry else []


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def require_admin(authorization: str = Header(default="")) -> None:
    if not config.ADMIN_TOKEN:
        raise HTTPException(status_code=404)
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token, config.ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid admin token")


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
        "nodes": [public_node(node, rank) for rank, node in enumerate(_cached_top(), 1)],
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
    outcome = scan_service.request_scan()
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
        "nodes": [public_node(node, rank) for rank, node in enumerate(_cached_top(query), 1)],
    }


# Kept for the previous web UI and existing scripts.
router.add_api_route("/scan/start", request_scan, methods=["POST"], include_in_schema=False)


@router.get("/vless/top")
async def get_top_vless(limit: int = Query(default=5, ge=1, le=50)):
    """Top working VLESS nodes (internal format)."""
    nodes = _cached_top()[:limit]
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
    content = generate_subscription_content(_cached_top()[:limit], format_type=format, rename=rename)
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
    return "\n".join(n["link"] for n in _cached_top()[:limit] if n.get("link"))


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


@router.get("/admin/stats", dependencies=[Depends(require_admin)])
async def admin_stats():
    return {**scan_service.status(), "stats": scan_service.stats, "last_error": scan_service.last_error()}


@router.post("/admin/scan", dependencies=[Depends(require_admin)])
async def admin_force_scan():
    """Rescan now, ignoring the cache."""
    return {"outcome": scan_service.request_scan(force=True)}


@router.post("/admin/cache/clear", dependencies=[Depends(require_admin)])
async def admin_clear_cache():
    scan_service.clear()
    return {"status": "cleared"}
