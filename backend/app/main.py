import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import auth
from .config import BASE_DIR, settings
from .db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("housespotter")

FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    log.info("HouseSpotter up — db=%s", settings.db_path)
    from .scheduler import start_scheduler, stop_scheduler

    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="HouseSpotter", lifespan=lifespan)

app.include_router(auth.router)


@app.get("/health")
def health():
    return {"status": "ok"}


def _register_api_routers():
    from .api import (
        routes_areas, routes_chat, routes_config, routes_lists, routes_milestones,
        routes_profiles, routes_properties, routes_system,
    )

    for mod in (
        routes_profiles, routes_properties, routes_lists, routes_areas,
        routes_chat, routes_system, routes_milestones, routes_config,
    ):
        app.include_router(mod.router)


_register_api_routers()

# --- Frontend (built static files), registered last so /api wins ---
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        candidate = FRONTEND_DIST / path
        if path and candidate.is_file() and candidate.resolve().is_relative_to(FRONTEND_DIST.resolve()):
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
else:

    @app.get("/", include_in_schema=False)
    def no_frontend():
        return {"detail": "Frontend not built. Run: cd frontend && npm run build"}
