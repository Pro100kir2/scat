from fastapi import APIRouter, BackgroundTasks, Query, Response, HTTPException
from fastapi.responses import PlainTextResponse
from typing import Optional, List, Dict, Any

from app.services.scanner import scanner_manager
from app.services.subscription import generate_subscription_content
from app import config

router = APIRouter()


@router.get("/vless/top")
async def get_top_vless(limit: int = Query(default=5, ge=1, le=50)):
    """Return the top fastest working VLESS nodes."""
    nodes = scanner_manager.top_nodes[:limit]
    return {
        "status": "success",
        "count": len(nodes),
        "timestamp": scanner_manager.last_scan_time,
        "nodes": nodes,
    }


@router.get("/vless/sub")
async def get_vless_subscription(
    format: str = Query(default="base64", pattern="^(base64|raw)$"),
    rename: bool = Query(default=True),
    limit: int = Query(default=5, ge=1, le=50),
):
    """Return VLESS subscription for clients (NekoBox, v2rayNG, Sing-Box, etc.)."""
    nodes = scanner_manager.top_nodes[:limit]
    sub_content = generate_subscription_content(nodes, format_type=format, rename=rename)

    media_type = "text/plain; charset=utf-8" if format == "raw" else "text/plain; charset=utf-8"
    headers = {
        "Subscription-Userinfo": f"upload=0; download=0; total=1073741824000; expire=0",
        "Profile-Title": "Fastest VLESS Configs",
        "Profile-Update-Interval": "1",
    }

    return Response(content=sub_content, media_type=media_type, headers=headers)


@router.get("/vless/raw", response_class=PlainTextResponse)
async def get_vless_raw(limit: int = Query(default=5, ge=1, le=50)):
    """Return top VLESS links as raw plain text line-by-line."""
    nodes = scanner_manager.top_nodes[:limit]
    raw_lines = "\n".join([n.get("original", "") for n in nodes if n.get("original")])
    return Response(content=raw_lines, media_type="text/plain; charset=utf-8")


@router.get("/scan/status")
async def get_scan_status():
    """Return current scanning status and progress statistics."""
    return scanner_manager.get_status()


@router.post("/scan/start")
async def start_scan(background_tasks: BackgroundTasks):
    """Trigger a new scan run in background."""
    if scanner_manager.is_scanning:
        return {
            "status": "already_running",
            "message": "Scan is currently in progress",
            "progress": scanner_manager.progress,
        }

    background_tasks.add_task(scanner_manager.run_scan)
    return {
        "status": "initiated",
        "message": "Scan process started in background",
    }


@router.get("/sources")
async def get_sources():
    """Return list of subscription sources configured in config.txt."""
    sources = scanner_manager.read_sources()
    return {
        "count": len(sources),
        "sources": sources,
        "sources_status": scanner_manager.sources_status,
    }
