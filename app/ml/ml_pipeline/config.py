from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    database: str
    user: str
    password_file: Path

    @classmethod
    def from_environment(cls) -> "DatabaseConfig":
        return cls(
            host=os.getenv("DB_HOST", "postgres"),
            port=int(os.getenv("DB_PORT", "5432")),
            database=os.getenv("DB_NAME", "korporate_ai"),
            user=os.getenv("DB_USER", "korporate_app"),
            password_file=Path(
                os.getenv(
                    "DB_PASSWORD_FILE",
                    "/run/secrets/postgres_app_password",
                )
            ),
        )

    def read_password(self) -> str:
        password = self.password_file.read_text(
            encoding="utf-8"
        ).strip()

        if not password:
            raise RuntimeError("Database password file is empty.")

        return password


FEATURE_VERSION = "product-monthly-v1"
QUALITY_VERSION = "source-quality-v1"
MODEL_VERSION = "inventory-baseline-v1"
