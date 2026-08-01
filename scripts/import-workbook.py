"""Run the complete manual XLSX import pipeline."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import subprocess
import sys
from typing import Sequence

import psycopg
from psycopg.rows import dict_row


SCRIPT_DIRECTORY = Path(__file__).resolve().parent

VALIDATION_SCRIPTS = (
    "validate-workbook-structure.py",
    "validate-workbook-values.py",
    "validate-workbook-business.py",
)

PIPELINE_SCRIPTS = {
    "raw": "import-workbook-raw.py",
    "staging": "transform-raw-to-staging.py",
    "core": "merge-and-promote-staging.py",
    "finalize": "finalize-import-batch.py",
}

TERMINAL_FAILURE_STATUSES = {
    "rejected",
    "failed",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and import one XLSX workbook through "
            "raw, staging, core, report and archive."
        )
    )
    parser.add_argument("workbook", type=Path)
    parser.add_argument(
        "--source-path",
        default=None,
        help="Host-visible source path stored in the audit batch.",
    )
    parser.add_argument(
        "--archive-root",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--report-root",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--move-source",
        action="store_true",
        help="Remove the incoming source after verified archival.",
    )
    parser.add_argument(
        "--created-by",
        default="manual-cli",
    )
    parser.add_argument("--db-host", default="postgres")
    parser.add_argument("--db-port", type=int, default=5432)
    parser.add_argument("--db-name", default="korporate_ai")
    parser.add_argument("--db-user", default="korporate_app")
    parser.add_argument(
        "--db-password-file",
        type=Path,
        required=True,
    )
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_step(name: str, command: Sequence[str]) -> None:
    print()
    print("=" * 72)
    print(f"PIPELINE_STEP={name}")
    print("=" * 72)
    sys.stdout.flush()

    result = subprocess.run(
        list(command),
        check=False,
    )

    if result.returncode != 0:
        print(
            f"PIPELINE_STEP_FAILED={name} "
            f"EXIT_CODE={result.returncode}",
            file=sys.stderr,
        )
        raise SystemExit(result.returncode)


def connect(args: argparse.Namespace):
    password = args.db_password_file.read_text(
        encoding="utf-8"
    ).strip()

    return psycopg.connect(
        host=args.db_host,
        port=args.db_port,
        dbname=args.db_name,
        user=args.db_user,
        password=password,
        row_factory=dict_row,
    )


def fetch_batch(
    args: argparse.Namespace,
    file_sha256: str,
) -> dict[str, object] | None:
    with connect(args) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    status,
                    row_count_raw,
                    row_count_core,
                    error_count,
                    warning_count,
                    archive_path
                FROM audit.import_batches
                WHERE file_sha256 = %s
                """,
                (file_sha256,),
            )
            return cursor.fetchone()


def verify_database(args: argparse.Namespace) -> None:
    required_relations = (
        "audit.import_batches",
        "audit.import_issues",
        "raw.xlsx_products",
        "stg.products",
        "core.products",
        "core.sales_orders",
    )

    with connect(args) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT key, value
                FROM meta.system_info
                WHERE key IN (
                    'application',
                    'platform_version',
                    'schema_revision'
                )
                ORDER BY key
                """
            )
            system_info = {
                row["key"]: row["value"]
                for row in cursor.fetchall()
            }

            missing_relations: list[str] = []
            for relation in required_relations:
                cursor.execute(
                    "SELECT to_regclass(%s) AS relation",
                    (relation,),
                )
                if cursor.fetchone()["relation"] is None:
                    missing_relations.append(relation)

    if missing_relations:
        raise RuntimeError(
            "Databaza nema potrebne importne tabulky: "
            + ", ".join(missing_relations)
        )

    print(f"APPLICATION={system_info.get('application', '')}")
    print(
        "PLATFORM_VERSION="
        f"{system_info.get('platform_version', '')}"
    )
    print(
        "SCHEMA_REVISION="
        f"{system_info.get('schema_revision', '')}"
    )


def common_database_arguments(
    args: argparse.Namespace,
) -> list[str]:
    return [
        "--db-host",
        args.db_host,
        "--db-port",
        str(args.db_port),
        "--db-name",
        args.db_name,
        "--db-user",
        args.db_user,
        "--db-password-file",
        str(args.db_password_file),
    ]


def require_batch(
    args: argparse.Namespace,
    file_sha256: str,
) -> dict[str, object]:
    batch = fetch_batch(args, file_sha256)
    if batch is None:
        raise RuntimeError(
            "Po raw kroku nebol najdeny audit import batch."
        )
    return batch


def print_batch_state(batch: dict[str, object]) -> None:
    print(f"IMPORT_BATCH_ID={batch['id']}")
    print(f"IMPORT_BATCH_STATUS={batch['status']}")
    print(f"RAW_ROW_COUNT={batch['row_count_raw']}")
    print(f"CORE_ROW_COUNT={batch['row_count_core']}")
    print(f"ERROR_COUNT={batch['error_count']}")
    print(f"WARNING_COUNT={batch['warning_count']}")


def main() -> int:
    args = parse_args()

    workbook = args.workbook.resolve()
    if not workbook.is_file():
        print(
            f"CHYBA: workbook neexistuje: {workbook}",
            file=sys.stderr,
        )
        return 2

    if not args.db_password_file.is_file():
        print(
            "CHYBA: subor s databazovym heslom neexistuje: "
            f"{args.db_password_file}",
            file=sys.stderr,
        )
        return 2

    for script_name in (
        *VALIDATION_SCRIPTS,
        *PIPELINE_SCRIPTS.values(),
    ):
        script_path = SCRIPT_DIRECTORY / script_name
        if not script_path.is_file():
            print(
                f"CHYBA: chyba pipeline skript: {script_path}",
                file=sys.stderr,
            )
            return 2

    file_sha256 = sha256_file(workbook)
    source_path = args.source_path or str(workbook)

    print(f"WORKBOOK={workbook.name}")
    print(f"WORKBOOK_PATH={workbook}")
    print(f"FILE_SHA256={file_sha256}")
    print(f"FILE_SIZE_BYTES={workbook.stat().st_size}")
    print(f"MOVE_SOURCE={'ANO' if args.move_source else 'NIE'}")

    try:
        verify_database(args)
    except Exception as exc:
        print(
            f"CHYBA: databazovy preflight zlyhal: {exc}",
            file=sys.stderr,
        )
        return 2

    for script_name in VALIDATION_SCRIPTS:
        run_step(
            script_name,
            [
                sys.executable,
                str(SCRIPT_DIRECTORY / script_name),
                str(workbook),
            ],
        )

    database_arguments = common_database_arguments(args)

    run_step(
        "raw_import",
        [
            sys.executable,
            str(SCRIPT_DIRECTORY / PIPELINE_SCRIPTS["raw"]),
            str(workbook),
            *database_arguments,
            "--created-by",
            args.created_by,
            "--source-path",
            source_path,
            "--expected-sha256",
            file_sha256,
        ],
    )

    try:
        batch = require_batch(args, file_sha256)
    except Exception as exc:
        print(f"CHYBA: {exc}", file=sys.stderr)
        return 1

    print_batch_state(batch)
    status = str(batch["status"])

    if status in TERMINAL_FAILURE_STATUSES:
        print(
            f"CHYBA: batch je v terminalnom stave {status}.",
            file=sys.stderr,
        )
        return 1

    if status == "raw_loaded":
        run_step(
            "raw_to_staging",
            [
                sys.executable,
                str(
                    SCRIPT_DIRECTORY
                    / PIPELINE_SCRIPTS["staging"]
                ),
                "--file-sha256",
                file_sha256,
                *database_arguments,
            ],
        )
        batch = require_batch(args, file_sha256)
        status = str(batch["status"])
        print_batch_state(batch)

    if status == "staging_loaded":
        run_step(
            "staging_to_core",
            [
                sys.executable,
                str(
                    SCRIPT_DIRECTORY
                    / PIPELINE_SCRIPTS["core"]
                ),
                "--file-sha256",
                file_sha256,
                *database_arguments,
            ],
        )
        batch = require_batch(args, file_sha256)
        status = str(batch["status"])
        print_batch_state(batch)

    if status in TERMINAL_FAILURE_STATUSES:
        print(
            f"CHYBA: batch je v terminalnom stave {status}.",
            file=sys.stderr,
        )
        return 1

    if status != "completed":
        print(
            "CHYBA: pipeline sa zastavila v nepodporovanom "
            f"stave {status}.",
            file=sys.stderr,
        )
        return 1

    finalize_command = [
        sys.executable,
        str(SCRIPT_DIRECTORY / PIPELINE_SCRIPTS["finalize"]),
        "--file-sha256",
        file_sha256,
        "--source-file",
        str(workbook),
        "--archive-root",
        str(args.archive_root),
        "--report-root",
        str(args.report_root),
        *database_arguments,
    ]

    if args.move_source:
        finalize_command.append("--move-source")

    run_step("finalize", finalize_command)

    batch = require_batch(args, file_sha256)
    print()
    print("=" * 72)
    print("PIPELINE_RESULT")
    print("=" * 72)
    print_batch_state(batch)
    print(f"ARCHIVE_PATH={batch['archive_path']}")
    print("IMPORT_PIPELINE_OK=ANO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
