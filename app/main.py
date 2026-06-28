"""Application factory: build the FastAPI app, mount routers, wire dependencies.

The factory mounts the identity, care, and demo routers, registers the central
exception handlers so domain exceptions surface as RFC 7807 responses, and
exposes a liveness probe. Building the app still reads no environment: the
router's config/database dependencies are lazy factories that only run per
request.
"""

from fastapi import FastAPI

from app.care.router import router as care_router
from app.core.errors import register_exception_handlers
from app.demo.router import router as demo_router
from app.identity.router import router as identity_router


def create_app() -> FastAPI:
    app = FastAPI(title="Kinetic Backend", version="0.0.0")
    register_exception_handlers(app)
    app.include_router(identity_router)
    app.include_router(care_router)
    app.include_router(demo_router)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
