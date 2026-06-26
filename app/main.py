"""Application factory: build the FastAPI app, mount routers, wire dependencies.

Per-context routers (identity, care, organization) and dependency wiring are
added in later commits. The factory registers the central exception handlers so
domain exceptions surface as RFC 7807 responses, and exposes a liveness probe.
It imports neither config nor database, so building the app reads no environment.
"""

from fastapi import FastAPI

from app.core.errors import register_exception_handlers


def create_app() -> FastAPI:
    app = FastAPI(title="Kinetic Backend", version="0.0.0")
    register_exception_handlers(app)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
