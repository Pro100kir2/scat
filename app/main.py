import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.api.routes import router as api_router
from app.services.scanner import scanner_manager


periodic_task = None

async def periodic_scan_loop():
    """Background loop to periodically run scanner."""
    while True:
        try:
            await asyncio.sleep(config.AUTO_SCAN_INTERVAL_SECONDS)
            print("[Lifecycle] Starting scheduled periodic scan...")
            await scanner_manager.run_scan()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"[Lifecycle] Periodic scan error: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global periodic_task
    print("[Lifecycle] VLESS Scanner Web App starting...")

    # Load results
    scanner_manager.load_results()

    # Trigger scan if no existing results or auto-scan configured
    if config.AUTO_SCAN_ON_STARTUP or not scanner_manager.top_nodes:
        print("[Lifecycle] Triggering initial background scan on startup...")
        asyncio.create_task(scanner_manager.run_scan())

    if config.AUTO_SCAN_INTERVAL_SECONDS > 0:
        periodic_task = asyncio.create_task(periodic_scan_loop())

    yield

    if periodic_task:
        periodic_task.cancel()
    print("[Lifecycle] VLESS Scanner Web App shut down.")


app = FastAPI(
    title="VLESS Configs Finder",
    description="Fast, stable and modern web service to find top working VLESS configs.",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
static_dir = BASE_DIR / "static"
templates_dir = BASE_DIR / "templates"

static_dir.mkdir(exist_ok=True)
templates_dir.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
templates = Jinja2Templates(directory=str(templates_dir))

# Include API routes
app.include_router(api_router, prefix="/api")


@app.get("/", include_in_schema=False)
async def home_page(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")
