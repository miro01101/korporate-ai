#!/usr/bin/env python3
"""Finalize a deferred Google Drive pipeline audit row after ML processing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from uuid import UUID

VALID_STATUSES = {"completed", "failed"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--status", choices=sorted(VALID_STATUSES), required=True)
    parser.add_argument("--summary-file", type=Path, required=True)
    parser.add_argument("--error-message", default=None)
    parser.add_argument("--db-host", default="postgres")
    parser.add_argument("--db-port", type=int, default=5432)
    parser.add_argument("--db-name", default="korporate_ai")
    parser.add_argument("--db-user", default="korporate_app")
    parser.add_argument("--db-password-file", type=Path, required=True)
    return parser.parse_args()


def read_text(path: Path, label: str) -> str:
    if not path.is_file():
        raise RuntimeError(f"{label} neexistuje: {path}")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError(f"{label} je prázdny: {path}")
    return value


def load_summary(path: Path) -> dict[str, Any]:
    payload = json.loads(read_text(path, "ML summary file"))
    if not isinstance(payload, dict):
        raise RuntimeError("ML summary musí byť JSON objekt.")

    status = payload.get("status")
    if status not in VALID_STATUSES:
        raise RuntimeError("ML summary má neplatný status.")

    stages = payload.get("stages")
    if not isinstance(stages, dict):
        raise RuntimeError("ML summary neobsahuje stages objekt.")

    return payload


def main() -> int:
    args = parse_args()
    summary = load_summary(args.summary_file)

    if summary["status"] != args.status:
        raise RuntimeError(
            "Status argumentu nezodpovedá statusu v ML summary."
        )

    import psycopg
    from psycopg.types.json import Jsonb

    password = read_text(args.db_password_file, "DB password file")
    error_stage = None
    error_message = None

    if args.status == "failed":
        failed_stage = str(summary.get("failed_stage") or "unknown")
        error_stage = f"ml_{failed_stage}"[:200]
        error_message = (
            args.error_message
            or str(summary.get("error_message") or "ML orchestration failed.")
        )[:4000]

    with psycopg.connect(
        host=args.db_host,
        port=args.db_port,
        dbname=args.db_name,
        user=args.db_user,
        password=password,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE audit.pipeline_runs
                SET status = %s,
                    finished_at = now(),
                    error_stage = %s,
                    error_message = %s,
                    metadata = metadata || %s
                WHERE id = %s
                  AND status = 'running'
                  AND finished_at IS NULL
                """,
                (
                    args.status,
                    error_stage,
                    error_message,
                    Jsonb({"ml": summary}),
                    args.run_id,
                ),
            )

            if cursor.rowcount != 1:
                raise RuntimeError(
                    "Deferred pipeline run sa nepodarilo jednoznačne finalizovať."
                )

        connection.commit()

    print(f"PIPELINE_RUN_ID={args.run_id}")
    print(f"PIPELINE_FINAL_STATUS={args.status}")
    print("PIPELINE_ML_AUDIT_FINALIZED=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
