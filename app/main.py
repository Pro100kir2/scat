import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.api.routes import router as api_router
from app.services.scan_service import scan_service

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    background = None
    if config.AUTO_REFRESH:
        background = asyncio.create_task(scan_service.refresh_forever(scan_now=config.AUTO_SCAN_ON_STARTUP), name="skat-refresh")
    elif config.AUTO_SCAN_ON_STARTUP:
        scan_service.request_scan()

    yield

    if background:
        background.cancel()
        with suppress(asyncio.CancelledError):
            await background


app = FastAPI(
    title="Скат API",
    description="Finds working VLESS configurations and serves them from a 24-hour cache.",
    version="3.0.0",
    lifespan=lifespan,
)

# The browser app only reads public data, so no credentials are allowed cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_origin_regex=config.ALLOWED_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    max_age=600,
)



# The API host (JSON, /docs) is not a page for people to land on from search: only the site is.
@app.middleware("http")
async def noindex(request, call_next):
    response = await call_next(request)
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


app.include_router(api_router, prefix="/api")


@app.get("/", include_in_schema=False)
async def root():
    return {"name": "Скат API", "docs": "/docs", "results": "/api/results"}
