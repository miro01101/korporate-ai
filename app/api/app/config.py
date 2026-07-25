from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Korporate AI Logistics Platform"
    app_env: str = "production"
    app_version: str = "0.1.0"

    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "korporate_ai"
    db_user: str = "korporate_app"
    db_password_file: str = "/run/secrets/postgres_app_password"

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def db_password(self) -> str:
        path = Path(self.db_password_file)

        if not path.is_file():
            raise RuntimeError(
                f"Database password file does not exist: {path}"
            )

        password = path.read_text(encoding="utf-8").strip()

        if not password:
            raise RuntimeError("Database password file is empty.")

        return password

    @property
    def database_url(self) -> str:
        user = quote_plus(self.db_user)
        password = quote_plus(self.db_password)
        host = self.db_host
        database = quote_plus(self.db_name)

        return (
            f"postgresql+psycopg://{user}:{password}"
            f"@{host}:{self.db_port}/{database}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
