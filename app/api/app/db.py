from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.config import get_settings


settings = get_settings()

engine: Engine = create_engine(
    settings.database_url,
    pool_size=3,
    max_overflow=1,
    pool_timeout=10,
    pool_recycle=1800,
    pool_pre_ping=True,
)


def get_database_health() -> dict[str, Any]:
    query = text(
        """
        SELECT
            current_database() AS database_name,
            current_user AS database_user,
            current_setting('server_version') AS server_version,
            now() AS database_time
        """
    )

    with engine.connect() as connection:
        result = connection.execute(query).mappings().one()

    return {
        "status": "ok",
        "database_name": result["database_name"],
        "database_user": result["database_user"],
        "server_version": result["server_version"],
        "database_time": result["database_time"].isoformat(),
    }
