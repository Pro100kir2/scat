import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import config, events
from app.admin import settings as runtime_settings
from app.admin.api import APP_VERSION, public_router as admin_login_router, router as admin_router
from app.api.routes import router as api_router
from app.probes import service as probe_service_module
from app.probes.api import MAX_BODY_BYTES, router as probes_router
from app.services.scan_history import scan_history
from app.services.scan_service import ALL, scan_service
from app.services.scanner import ScanQuery

DEBUG = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


# Custom filter to deduplicate /api/scan/status logs
class DeduplicateFilter(logging.Filter):
    def __init__(self):
        super().__init__()
        self.last_log = None

    def filter(self, record):
        # Check if this is an /api/scan/status request
        if "/api/scan/status" in record.getMessage():
            current_log = record.getMessage()
            if current_log == self.last_log:
                return False  # Skip this duplicate
            self.last_log = current_log
        else:
            self.last_log = None
        return True


# Apply the filter to uvicorn access logger
uvicorn_logger = logging.getLogger("uvicorn.access")
uvicorn_logger.addFilter(DeduplicateFilter())


async def schedule_probe_round(query: ScanQuery, entry: dict) -> None:
    """A new full scan finished: its best configs get provider reachability checks.
    Only here; page loads and country searches never start probe work."""
    if query == ALL:
        summary = await probe_service_module.probe_service.schedule_scan(entry)
        scan_history.probe_round(entry.get("scan_id"), summary)


scan_service.add_listener(schedule_probe_round)
scan_service.history = scan_history


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        runtime_settings.load()
        scan_history.interrupted()
        events.record("system", "startup", f"Backend {APP_VERSION} started")
    except Exception:
        logging.getLogger("skat").exception("startup bookkeeping failed")
    background = None
    probe_maintenance = asyncio.create_task(probe_service_module.probe_service.run_forever(), name="skat-probes")
    if config.AUTO_REFRESH:
        background = asyncio.create_task(scan_service.refresh_forever(scan_now=config.AUTO_SCAN_ON_STARTUP), name="skat-refresh")
    elif config.AUTO_SCAN_ON_STARTUP:
        scan_service.request_scan()

    yield

    probe_maintenance.cancel()
    with suppress(asyncio.CancelledError):
        await probe_maintenance
    if background:
        background.cancel()
        with suppress(asyncio.CancelledError):
            await background


app = FastAPI(
    title="Скат API",
    description="Finds working VLESS configurations and serves them from a 24-hour cache.",
    version=APP_VERSION,
    lifespan=lifespan,
)

class PathScopedCORS:
    """Public data is read cross-origin without credentials (and preview origins may match the
    regex). The admin API alone allows credentials, for its session cookie, and only for the
    exact ALLOWED_ORIGINS: never the regex, never "*". The cookie is additionally
    SameSite=Strict, scoped to /api/admin, and honoured only with the X-Scat-Admin header
    (app/admin/auth.py)."""

    def __init__(self, app):
        self.public = CORSMiddleware(
            app, allow_origins=config.ALLOWED_ORIGINS, allow_origin_regex=config.ALLOWED_ORIGIN_REGEX,
            allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["Content-Type"], max_age=600)
        self.admin = CORSMiddleware(
            app, allow_origins=config.ALLOWED_ORIGINS, allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Content-Type", "X-Scat-Admin"],
            max_age=600)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/api/admin"):
            return await self.admin(scope, receive, send)
        return await self.public(scope, receive, send)


app.add_middleware(PathScopedCORS)


# The API host (JSON, /docs) is not a page for people to land on from search: only the site is.
@app.middleware("http")
async def noindex(request, call_next):
    response = await call_next(request)
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


# Probe, provider and admin endpoints accept small JSON only. Enforced before the body is read.
@app.middleware("http")
async def limit_body_size(request, call_next):
    path = request.url.path
    if request.method in ("POST", "PUT", "PATCH") and path.startswith(("/api/probes/", "/api/providers", "/api/admin")):
        length = request.headers.get("content-length")
        if length is None:
            # Admin actions often have no body at all (`curl -X POST .../approve`); a streamed
            # body of unknown size is still refused.
            if path.startswith("/api/admin") and "transfer-encoding" not in request.headers:
                return await call_next(request)
            return JSONResponse({"detail": "Content-Length required"}, status_code=411)
        if not length.isdigit() or int(length) > MAX_BODY_BYTES:
            return JSONResponse({"detail": "Request body too large"}, status_code=413)
    return await call_next(request)


app.include_router(api_router, prefix="/api")
app.include_router(probes_router, prefix="/api")
app.include_router(admin_login_router, prefix="/api")
app.include_router(admin_router, prefix="/api")


@app.get("/", include_in_schema=False)
async def root():
    return {"name": "Скат API", "docs": "/docs", "results": "/api/results"}
