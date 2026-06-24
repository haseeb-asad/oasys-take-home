"""Application factory: build the FastAPI app, mount routers, wire dependencies.

Per-context routers (identity / care / organization) and dependency wiring are
added in later commits. This commit is structure only — no domain or auth logic.
"""

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Kinetic Backend", version="0.0.0")

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
