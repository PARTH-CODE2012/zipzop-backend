"""Application factory.

The gateway validates, authorises, writes state and enqueues. It never touches
media and never does heavy work in a request handler — see
docs/03-backend-architecture.md §3.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.routes import auth, health, media
from app.config import assert_production_safe, settings
from app.db import engine
from app.logging import RequestContextMiddleware, configure_logging, get_logger

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    configure_logging()
    assert_production_safe()
    log.info("starting", environment=settings.environment)
    yield
    await engine.dispose()
    log.info("stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="ZipZop API",
        version="0.1.0",
        description=(
            "AI Video Editor. The contract for this API is documented in "
            "docs/05-api-contract.md; this schema is generated from it and is "
            "the source the frontend generates its types from."
        ),
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    register_exception_handlers(app)

    # Health sits outside /v1 — it describes the process, not the product.
    app.include_router(health.router)

    # Product routes are versioned. Everything from M2 onward lands here.
    app.include_router(auth.router, prefix="/v1")
    app.include_router(auth.me_router, prefix="/v1")
    app.include_router(media.router, prefix="/v1")
    # app.include_router(projects.router, prefix="/v1")   # M3
    # app.include_router(jobs.router, prefix="/v1")       # M4
    # app.include_router(billing.router, prefix="/v1")    # M6

    return app


app = create_app()
