"""FastAPI application factory: middleware, routers, static UI, lifecycle."""
from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, ORJSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.api.v1 import api_router
from app.core.config import settings
from app.core.logging import get_logger, request_id_ctx, setup_logging

setup_logging()
log = get_logger("app.main")

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(
        "starting panel",
        extra={
            "version": __version__,
            "environment": settings.environment,
            "database": settings.sqlalchemy_url.split("://")[0],
        },
    )
    from app.db.session import init_db

    init_db()

    if settings.live_proxy_enabled:
        try:
            from app.db.session import SessionLocal
            from app.services.live_proxy import ensure_default_relay_endpoint

            with SessionLocal() as db:
                ensure_default_relay_endpoint(db)
        except Exception as exc:  # pragma: no cover
            log.exception("built-in WebSocket relay bootstrap failed", extra={"error": str(exc)})

    # Bootstrap the owner account on first boot so the panel is never locked out.
    try:
        from app.cli import ensure_superadmin
        from app.db.session import SessionLocal

        with SessionLocal() as db:
            ensure_superadmin(db)
    except Exception as exc:  # pragma: no cover
        log.warning("superadmin bootstrap skipped", extra={"error": str(exc)})

    scheduler = None
    if settings.enable_scheduler:
        from app.workers.scheduler import start_scheduler

        scheduler = start_scheduler()

    # Register the Telegram webhook so the bot works without manual steps.
    # Credentials may live in the database (set from the panel UI) or in the
    # environment, so the runtime resolver is the only source consulted here.
    try:
        from app.services import runtime_config

        config = runtime_config.telegram_config(fresh=True)
        if config.get("token") and config.get("enabled") and config.get("auto_set_webhook"):
            from app.api.v1.telegram import register_commands
            from app.bot.telegram import TelegramClient

            client = TelegramClient()
            url = runtime_config.webhook_url()
            if client.set_webhook(url, secret_token=config.get("webhook_secret") or None):
                register_commands(client)
                log.info("telegram webhook registered", extra={"url": url})
            else:
                log.warning("telegram webhook registration failed - use POST /api/v1/telegram/setup")
        elif config.get("token"):
            log.info("telegram bot configured, webhook auto-registration is off")
        else:
            log.info("telegram bot not configured - add a token under Telegram bot in the panel")
    except Exception as exc:  # pragma: no cover
        log.warning("telegram setup skipped", extra={"error": str(exc)})

    yield

    log.info("shutting down panel")
    if scheduler is not None:
        from app.workers.scheduler import stop_scheduler

        stop_scheduler()


def create_app() -> FastAPI:
    app = FastAPI(
        title=f"{settings.app_name} API",
        description=(
            "Control plane for an Xray-based proxy/VPN service: users, plans, nodes, "
            "configs, quotas, payments, reporting and a Telegram bot."
        ),
        version=__version__,
        default_response_class=ORJSONResponse,
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url="/api/redoc" if not settings.is_production else None,
        openapi_url="/api/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ------------------------------------------------------------- middleware
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    if settings.trusted_host_list != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials="*" not in settings.cors_origin_list,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-Id"],
        expose_headers=["X-Request-Id", "Subscription-Userinfo"],
        max_age=600,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request_id_ctx.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:  # pragma: no cover - handled by the exception handlers
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["X-Request-Id"] = rid
        response.headers["X-Process-Time-Ms"] = str(duration_ms)

        # Security headers (Render terminates TLS in front of the service).
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        elif request.url.path.startswith("/assets/"):
            # Fingerprint-free static files: short cache so deploys propagate fast.
            response.headers.setdefault("Cache-Control", "public, max-age=600")
        elif request.url.path in ("/", "/index.html", "/login", "/panel"):
            response.headers.setdefault("Cache-Control", "no-cache")
        if duration_ms > 2000:
            log.warning("slow request", extra={"path": request.url.path, "ms": duration_ms})
        return response

    # ------------------------------------------------------------ error shapes
    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=jsonable_encoder({"detail": "Validation error", "errors": exc.errors()}),
        )

    @app.exception_handler(SQLAlchemyError)
    async def db_handler(request: Request, exc: SQLAlchemyError):
        log.exception("database error", extra={"path": request.url.path})
        return JSONResponse(status_code=500, content={"detail": "Database error"})

    # ------------------------------------------------------------------ routes
    app.include_router(api_router, prefix="/api/v1")

    # Short, user-facing subscription path: /sub/<token>
    # MUST be registered before the SPA catch-all, otherwise clients would
    # receive the panel's index.html instead of their configuration.
    from app.api.v1.subscription import short_router

    app.include_router(short_router)

    # Built-in VLESS WebSocket relay. It shares the Railway HTTP service and
    # therefore needs no separately deployed node-agent process.
    from app.services.live_proxy import router as live_proxy_router

    app.include_router(live_proxy_router)

    @app.get("/api", include_in_schema=False)
    def api_index() -> dict:
        return {
            "app": settings.app_name,
            "version": __version__,
            "docs": "/api/docs" if not settings.is_production else None,
            "health": "/api/v1/health/live",
        }

    # ------------------------------------------------------------------ UI
    if STATIC_DIR.exists():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

        @app.get("/", include_in_schema=False)
        @app.get("/login", include_in_schema=False)
        @app.get("/panel", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

        @app.get("/favicon.svg", include_in_schema=False)
        def favicon() -> FileResponse:
            return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa_fallback(full_path: str):
            """Serve a real file when it exists, otherwise hand the SPA its shell.

            Anything under `api/` or `sub/` must 404 as JSON/plain instead of
            silently returning HTML - otherwise a typo in a config URL looks like
            a successful response to a client that then fails to parse it.
            """
            if full_path.startswith(("api/", "sub/")):
                return JSONResponse(status_code=404, content={"detail": "Not found"})
            candidate = (STATIC_DIR / full_path).resolve()
            if candidate.is_file() and STATIC_DIR.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
