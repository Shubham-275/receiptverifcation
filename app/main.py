"""
FastAPI application factory.

Start the server:
    uvicorn app.main:app --reload

Or via the run.py convenience script at the project root.
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from app.api.router import router
from app.config import get_settings
from app.database import close_db
from app.exceptions import AppError, app_error_handler
from app.logging_config import configure_logging, get_logger
from app.services.auth_seed import seed_default_user


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle hooks."""
    # ── Startup ────────────────────────────────────────────────────────────────
    configure_logging()
    log = get_logger("app.startup")
    settings = get_settings()
    log.info(
        "server_starting",
        app=settings.app_name,
        version=settings.app_version,
        env=settings.environment,
    )
    await seed_default_user()
    yield
    # ── Shutdown ───────────────────────────────────────────────────────────────
    log = get_logger("app.shutdown")
    log.info("server_shutting_down")
    await close_db()
    log.info("server_stopped")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "AI-powered supplier receipt intelligence system. "
            "Converts arbitrary supplier receipts into structured purchasing data."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # ── CORS (open in dev; tighten in production) ──────────────────────────────
    origins = ["*"] if settings.environment == "development" else []
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Exception handlers ─────────────────────────────────────────────────────
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]

    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
        log.error("database_error_occurred", error=str(exc))
        return JSONResponse(
            status_code=503,
            content={
                "error": "DATABASE_ERROR",
                "message": "PostgreSQL database is currently offline or unreachable.",
                "detail": (
                    "Could not connect to PostgreSQL on localhost:5432. "
                    "Switch to 'Preview Only' mode to verify receipts without persistence, "
                    "or start your PostgreSQL database service."
                ),
            },
        )

    # ── Routers ────────────────────────────────────────────────────────────────
    app.include_router(router)

    # ── Dashboard & Static Assets ──────────────────────────────────────────────
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
        app.mount(
            "/fonts",
            StaticFiles(directory=str(static_dir / "fonts")),
            name="fonts",
        )

        # Relative asset URLs keep index.html portable to static previews.
        # These root routes serve the same files when the app runs under FastAPI.
        @app.get("/styles.css", include_in_schema=False)
        async def root_styles():
            return FileResponse(
                str(static_dir / "styles.css"),
                media_type="text/css",
            )

        @app.get("/app.js", include_in_schema=False)
        async def root_script():
            return FileResponse(
                str(static_dir / "app.js"),
                media_type="text/javascript",
            )

        @app.get("/", include_in_schema=False)
        async def root():
            return FileResponse(
                str(static_dir / "index.html"),
                headers={"Cache-Control": "no-store"},
            )

        @app.get("/dashboard", include_in_schema=False)
        async def dashboard():
            return FileResponse(
                str(static_dir / "index.html"),
                headers={"Cache-Control": "no-store"},
            )

    return app


app = create_app()
