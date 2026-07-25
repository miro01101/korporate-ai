from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db import get_database_health


settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "application": settings.app_name,
        "version": settings.app_version,
        "environment": settings.app_env,
        "status": "running",
    }


@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {
        "status": "alive",
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.get("/health/ready", response_model=None)
def health_ready() -> JSONResponse | dict[str, object]:
    try:
        database = get_database_health()
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "database": {
                    "status": "unavailable",
                    "error_type": type(exc).__name__,
                },
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )

    return {
        "status": "ready",
        "application": settings.app_name,
        "version": settings.app_version,
        "environment": settings.app_env,
        "database": database,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.get("/api/v1/system/info")
def system_info() -> dict[str, object]:
    return {
        "application": settings.app_name,
        "version": settings.app_version,
        "environment": settings.app_env,
        "database": get_database_health(),
    }
