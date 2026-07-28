#!/usr/bin/env python3
"""Process the oldest unhandled XLSX from one Google Drive folder."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any
from urllib.parse import quote
import uuid

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LOCK_KEY = 74040001
RETRYABLE_HTTP = {429, 500, 502, 503, 504}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Process one new XLSX from a Google Drive folder."
    )
    parser.add_argument("--service-account-file", type=Path, required=True)
    parser.add_argument("--folder-id-file", type=Path, required=True)
    parser.add_argument("--import-root", type=Path, required=True)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--created-by", default="google-drive-service-account")
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


def parse_timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Google timestamp nemá timezone.")
    return result


def safe_filename(value: str) -> str:
    name = Path(value).name.strip()
    name = re.sub(r"[\x00-\x1f\x7f]+", "_", name)
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"_+", "_", name).strip("._")
    if not name:
        name = "workbook.xlsx"
    if not name.lower().endswith(".xlsx"):
        name += ".xlsx"
    if len(name) > 180:
        name = f"{Path(name).stem[:170].rstrip('._')}.xlsx"
    return name


def folder_query(folder_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", folder_id):
        raise ValueError("Neplatný Google Drive folder ID.")
    return (
        f"'{folder_id}' in parents and trashed = false "
        f"and mimeType = '{XLSX_MIME}'"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def connect_db(args: argparse.Namespace) -> psycopg.Connection:
    return psycopg.connect(
        host=args.db_host,
        port=args.db_port,
        dbname=args.db_name,
        user=args.db_user,
        password=read_text(args.db_password_file, "DB password file"),
        row_factory=dict_row,
        autocommit=True,
    )


def drive_session(credentials_path: Path) -> AuthorizedSession:
    credentials = service_account.Credentials.from_service_account_file(
        str(credentials_path), scopes=[DRIVE_SCOPE]
    )
    return AuthorizedSession(credentials)


def request_with_retry(
    session: AuthorizedSession, method: str, url: str, **kwargs: Any
):
    response = None
    for attempt in range(1, 4):
        response = session.request(method, url, timeout=(10, 120), **kwargs)
        if response.status_code not in RETRYABLE_HTTP:
            return response
        if attempt < 3:
            response.close()
            time.sleep(2 ** (attempt - 1))
    assert response is not None
    return response


def list_xlsx(
    session: AuthorizedSession, folder_id: str
) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    page_token: str | None = None

    while True:
        params = {
            "q": folder_query(folder_id),
            "spaces": "drive",
            "orderBy": "modifiedTime asc,name",
            "pageSize": "1000",
            "fields": (
                "nextPageToken,"
                "files(id,name,size,createdTime,modifiedTime,"
                "capabilities(canDownload))"
            ),
        }
        if page_token:
            params["pageToken"] = page_token

        response = request_with_retry(
            session, "GET", DRIVE_FILES_URL, params=params
        )
        if response.status_code != 200:
            message = response.text[:1000]
            response.close()
            raise RuntimeError(
                "Google Drive files.list zlyhal: "
                f"HTTP {response.status_code} {message}"
            )

        payload = response.json()
        response.close()
        files.extend(payload.get("files", []))
        page_token = payload.get("nextPageToken")
        if not page_token:
            return files


def download_xlsx(
    session: AuthorizedSession,
    drive_file: dict[str, Any],
    incoming: Path,
) -> Path:
    if not drive_file.get("capabilities", {}).get("canDownload", True):
        raise RuntimeError("Google Drive zakázal download súboru.")

    incoming.mkdir(parents=True, exist_ok=True)
    file_id = drive_file["id"]
    final_path = incoming / f"{file_id}__{safe_filename(drive_file['name'])}"
    partial_path = incoming / f".{file_id}.download.part"
    partial_path.unlink(missing_ok=True)

    response = request_with_retry(
        session,
        "GET",
        f"{DRIVE_FILES_URL}/{quote(file_id, safe='')}",
        params={"alt": "media"},
        stream=True,
    )
    if response.status_code != 200:
        message = response.text[:1000]
        response.close()
        raise RuntimeError(
            "Google Drive download zlyhal: "
            f"HTTP {response.status_code} {message}"
        )

    try:
        with partial_path.open("wb") as output:
            for chunk in response.iter_content(chunk_size=4 * 1024 * 1024):
                if chunk:
                    output.write(chunk)

        actual_size = partial_path.stat().st_size
        expected_size = drive_file.get("size")
        if expected_size is not None and actual_size != int(expected_size):
            raise RuntimeError("Stiahnutá veľkosť nesedí s Drive metadata.")
        if actual_size == 0:
            raise RuntimeError("Stiahnutý XLSX je prázdny.")

        partial_path.replace(final_path)
        return final_path
    except Exception:
        partial_path.unlink(missing_ok=True)
        raise
    finally:
        response.close()


def version_stats(
    connection: psycopg.Connection, file_id: str, modified_at: datetime
) -> tuple[bool, int]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                count(*) FILTER (
                    WHERE status IN (
                        'completed', 'rejected', 'skipped_duplicate'
                    )
                ) > 0 AS terminal,
                COALESCE(max(attempt_number), 0) AS attempts
            FROM audit.pipeline_runs
            WHERE google_file_id = %s
              AND source_modified_at = %s
            """,
            (file_id, modified_at),
        )
        row = cursor.fetchone()
    return bool(row["terminal"]), int(row["attempts"])


def create_run(
    connection: psycopg.Connection,
    drive_file: dict[str, Any],
    modified_at: datetime,
) -> tuple[uuid.UUID, int]:
    _, attempts = version_stats(connection, drive_file["id"], modified_at)
    run_id = uuid.uuid4()
    attempt = attempts + 1

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO audit.pipeline_runs (
                id, trigger_type, status, attempt_number,
                google_file_id, source_filename, source_modified_at, metadata
            )
            VALUES (
                %s, 'google_drive_timer', 'running', %s,
                %s, %s, %s, %s
            )
            """,
            (
                run_id,
                attempt,
                drive_file["id"],
                drive_file["name"],
                modified_at,
                Jsonb({"google_drive": drive_file}),
            ),
        )
    return run_id, attempt


def finish_run(
    connection: psycopg.Connection,
    run_id: uuid.UUID,
    status: str,
    *,
    sha256: str | None = None,
    batch_id: uuid.UUID | None = None,
    refresh_id: uuid.UUID | None = None,
    error_stage: str | None = None,
    error_message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE audit.pipeline_runs
            SET status = %s,
                finished_at = now(),
                source_sha256 = COALESCE(%s, source_sha256),
                import_batch_id = COALESCE(%s, import_batch_id),
                mart_refresh_run_id = COALESCE(%s, mart_refresh_run_id),
                error_stage = %s,
                error_message = %s,
                metadata = metadata || %s
            WHERE id = %s
            """,
            (
                status,
                sha256,
                batch_id,
                refresh_id,
                error_stage,
                error_message[:4000] if error_message else None,
                Jsonb(metadata or {}),
                run_id,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(f"Run {run_id} sa neaktualizoval.")


def record_poll(
    connection: psycopg.Connection,
    status: str,
    *,
    error_stage: str | None = None,
    error_message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> uuid.UUID:
    run_id = uuid.uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO audit.pipeline_runs (
                id, trigger_type, status, attempt_number, finished_at,
                error_stage, error_message, metadata
            )
            VALUES (
                %s, 'google_drive_timer', %s, 1, now(), %s, %s, %s
            )
            """,
            (
                run_id,
                status,
                error_stage,
                error_message[:4000] if error_message else None,
                Jsonb(metadata or {}),
            ),
        )
    return run_id


def fetch_batch(
    connection: psycopg.Connection, sha256: str
) -> dict[str, Any] | None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, status, archive_path, error_message
            FROM audit.import_batches
            WHERE file_sha256 = %s
            """,
            (sha256,),
        )
        return cursor.fetchone()


def fetch_refresh(
    connection: psycopg.Connection, started_after: datetime
) -> dict[str, Any] | None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, status, error_message
            FROM mart.refresh_runs
            WHERE started_at >= %s
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (started_after,),
        )
        return cursor.fetchone()


def db_now(connection: psycopg.Connection) -> datetime:
    with connection.cursor() as cursor:
        cursor.execute("SELECT now() AS current_time")
        return cursor.fetchone()["current_time"]


def db_args(args: argparse.Namespace) -> list[str]:
    return [
        "--db-host", args.db_host,
        "--db-port", str(args.db_port),
        "--db-name", args.db_name,
        "--db-user", args.db_user,
        "--db-password-file", str(args.db_password_file),
    ]


def run_command(command: list[str]) -> tuple[int, str]:
    result = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    print(result.stdout, end="")
    return result.returncode, result.stdout


def import_failure_status(
    output: str, batch: dict[str, Any] | None
) -> str:
    if batch is not None and batch["status"] == "rejected":
        return "rejected"
    if "PIPELINE_STEP_FAILED=validate-workbook-" in output:
        return "rejected"
    return "failed"


def process_file(
    args: argparse.Namespace,
    connection: psycopg.Connection,
    session: AuthorizedSession,
    folder_id: str,
    drive_file: dict[str, Any],
) -> bool:
    modified_at = parse_timestamp(drive_file["modifiedTime"])
    run_id, attempt = create_run(connection, drive_file, modified_at)
    local_path: Path | None = None
    sha256: str | None = None
    batch_id: uuid.UUID | None = None
    refresh_id: uuid.UUID | None = None

    incoming = args.import_root / "incoming"
    archive = args.import_root / "archive"
    reports = args.import_root / "reports"
    quarantine = args.import_root / "quarantine"

    print(f"PIPELINE_RUN_ID={run_id}")
    print(f"ATTEMPT_NUMBER={attempt}")
    print(f"GOOGLE_FILE_ID={drive_file['id']}")
    print(f"GOOGLE_FILE_NAME={drive_file['name']}")

    try:
        local_path = download_xlsx(session, drive_file, incoming)
        sha256 = sha256_file(local_path)
        batch = fetch_batch(connection, sha256)
        reuse_completed_batch = (
            batch is not None
            and batch["status"] == "completed"
            and attempt > 1
        )

        if (
            batch is not None
            and batch["status"] == "completed"
            and not reuse_completed_batch
        ):
            local_path.unlink(missing_ok=True)
            finish_run(
                connection,
                run_id,
                "skipped_duplicate",
                sha256=sha256,
                batch_id=batch["id"],
                metadata={"reason": "completed_import_batch_exists"},
            )
            print("PIPELINE_STATUS=skipped_duplicate")
            return True

        if reuse_completed_batch:
            batch_id = batch["id"]
            local_path.unlink(missing_ok=True)
            import_code = 0
            import_output = ""
            print("IMPORT_REUSED_COMPLETED_BATCH=ANO")
        else:
            import_code, import_output = run_command(
                [
                    sys.executable,
                    "/app/scripts/import-workbook.py",
                    str(local_path),
                    "--source-path",
                    (
                        f"gdrive://{folder_id}/"
                        f"{drive_file['id']}/{drive_file['name']}"
                    ),
                    "--archive-root",
                    str(archive),
                    "--report-root",
                    str(reports),
                    "--move-source",
                    "--created-by",
                    args.created_by,
                    *db_args(args),
                ]
            )

            batch = fetch_batch(connection, sha256)
            if batch is not None:
                batch_id = batch["id"]

        if import_code != 0 or batch is None or batch["status"] != "completed":
            status = import_failure_status(import_output, batch)
            quarantine_path = None

            if status == "rejected" and local_path.exists():
                quarantine.mkdir(parents=True, exist_ok=True)
                quarantine_path = quarantine / f"{run_id}__{local_path.name}"
                local_path.replace(quarantine_path)
            elif local_path.exists():
                local_path.unlink()

            finish_run(
                connection,
                run_id,
                status,
                sha256=sha256,
                batch_id=batch_id,
                error_stage="import",
                error_message=(
                    (batch.get("error_message") if batch is not None else None)
                    or f"Import exit code: {import_code}"
                ),
                metadata={
                    "import_exit_code": import_code,
                    "quarantine_path": (
                        str(quarantine_path) if quarantine_path else None
                    ),
                },
            )
            print(f"PIPELINE_STATUS={status}")
            return False

        refresh_started = db_now(connection)
        refresh_code, refresh_output = run_command(
            [
                sys.executable,
                "/app/scripts/refresh-analytics-marts.py",
                *db_args(args),
            ]
        )
        refresh = fetch_refresh(connection, refresh_started)
        if refresh is not None:
            refresh_id = refresh["id"]

        if (
            refresh_code != 0
            or refresh is None
            or refresh["status"] != "completed"
        ):
            finish_run(
                connection,
                run_id,
                "failed",
                sha256=sha256,
                batch_id=batch_id,
                refresh_id=refresh_id,
                error_stage="mart_refresh",
                error_message=(
                    (
                        refresh.get("error_message")
                        if refresh is not None
                        else None
                    )
                    or f"Mart refresh exit code: {refresh_code}"
                ),
                metadata={"refresh_output_tail": refresh_output[-4000:]},
            )
            print("PIPELINE_STATUS=failed")
            return False

        finish_run(
            connection,
            run_id,
            "completed",
            sha256=sha256,
            batch_id=batch_id,
            refresh_id=refresh_id,
            metadata={
                "archive_path": batch["archive_path"],
                "transactional_mart_validation": True,
            },
        )
        print(f"IMPORT_BATCH_ID={batch_id}")
        print(f"MART_REFRESH_RUN_ID={refresh_id}")
        print("PIPELINE_STATUS=completed")
        print("GOOGLE_DRIVE_PIPELINE_OK=ANO")
        return True

    except Exception as exc:
        if local_path is not None and local_path.exists():
            local_path.unlink()
        finish_run(
            connection,
            run_id,
            "failed",
            sha256=sha256,
            batch_id=batch_id,
            refresh_id=refresh_id,
            error_stage="unexpected",
            error_message=str(exc),
        )
        print(f"CHYBA: {exc}", file=sys.stderr)
        print("PIPELINE_STATUS=failed")
        return False


def main() -> int:
    args = parse_args()
    if args.max_attempts < 1:
        print("CHYBA: --max-attempts musí byť >= 1.", file=sys.stderr)
        return 2

    folder_id = read_text(args.folder_id_file, "Google Drive folder ID file")
    folder_query(folder_id)
    connection = connect_db(args)
    lock_acquired = False
    session: AuthorizedSession | None = None

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('audit.pipeline_runs') AS rel")
            if cursor.fetchone()["rel"] is None:
                print("CHYBA: migrácia 0009 nie je aplikovaná.", file=sys.stderr)
                return 2

            cursor.execute(
                "SELECT pg_try_advisory_lock(%s) AS acquired", (LOCK_KEY,)
            )
            lock_acquired = bool(cursor.fetchone()["acquired"])

        if not lock_acquired:
            print("PIPELINE_SKIPPED=database_lock_not_acquired")
            return 0

        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE audit.pipeline_runs
                SET status = 'failed',
                    finished_at = now(),
                    error_stage = 'stale_run',
                    error_message = 'Recovered stale running audit row.'
                WHERE status = 'running'
                  AND started_at < now() - interval '2 hours'
                """
            )
            print(f"STALE_RUNS_RECOVERED={cursor.rowcount}")

        try:
            session = drive_session(args.service_account_file)
            drive_files = list_xlsx(session, folder_id)
        except Exception as exc:
            run_id = record_poll(
                connection,
                "failed",
                error_stage="drive_list",
                error_message=str(exc),
            )
            print(f"PIPELINE_RUN_ID={run_id}")
            print(f"CHYBA: {exc}", file=sys.stderr)
            return 1

        pending = None
        for drive_file in drive_files:
            modified_at = parse_timestamp(drive_file["modifiedTime"])
            terminal, attempts = version_stats(
                connection, drive_file["id"], modified_at
            )
            if not terminal and attempts < args.max_attempts:
                pending = drive_file
                break

        print(f"DRIVE_XLSX_COUNT={len(drive_files)}")

        if pending is None:
            run_id = record_poll(
                connection,
                "no_file",
                metadata={
                    "drive_xlsx_count": len(drive_files),
                    "reason": "no_processable_file_version",
                },
            )
            print(f"PIPELINE_RUN_ID={run_id}")
            print("PIPELINE_STATUS=no_file")
            return 0

        return 0 if process_file(
            args, connection, session, folder_id, pending
        ) else 1

    finally:
        if lock_acquired:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_unlock(%s)", (LOCK_KEY,)
                    )
            except Exception:
                pass
        if session is not None:
            session.close()
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
