"""Minimal process-liveness API; no business or provider functionality."""

from fastapi import FastAPI

from ai_tender_intelligence.config import Settings, load_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the local application without starting external resources."""
    configuration = load_settings() if settings is None else settings
    app = FastAPI(
        title="AI Tender Intelligence",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = configuration

    @app.get("/health/live")
    def liveness() -> dict[str, str]:
        # Process liveness is not database or integration readiness.
        return {"status": "ok"}

    return app
