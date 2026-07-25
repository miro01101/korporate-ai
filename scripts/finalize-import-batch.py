"""Finalize a completed XLSX import with archive and JSON report."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


RAW_TABLES = (
    "xlsx_products",
    "xlsx_sales",
    "xlsx_inventory",
    "xlsx_purchases",
    "xlsx_expedition",
    "xlsx_vehicles",
)

STAGING_TABLES = (
    "products",
    "sales",
    "inventory",
    "purchases",
    "expedition",
    "vehicles",
)

CORE_TABLES = (
    "suppliers",
    "customers",
    "products",
    "vehicles",
    "sales_orders",
    "sales_order_lines",
    "purchase_orders",
    "purchase_order_lines",
    "inventory_snapshots",
    "inventory_snapshot_lines",
    "expeditions",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Archive the source workbook and create a JSON report "
            "for one completed import batch."
        )
    )
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--batch-id", type=uuid.UUID)
    selector.add_argument("--file-sha256")

    parser.add_argument("--source-file", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--report-root", type=Path, required=True)
    parser.add_argument("--move-source", action="store_true")

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


def json_default(value: object) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def ensure_archive(
    *,
    source_file: Path,
    archive_file: Path,
    expected_sha256: str,
    expected_size: int,
) -> str:
    archive_file.parent.mkdir(parents=True, exist_ok=True)

    if archive_file.exists():
        if archive_file.stat().st_size != expected_size:
            raise RuntimeError(
                "Existujuci archivny subor ma inu velkost."
            )
        if sha256_file(archive_file) != expected_sha256:
            raise RuntimeError(
                "Existujuci archivny subor ma iny SHA-256."
            )
        return "existing"

    if not source_file.is_file():
        raise FileNotFoundError(
            f"Zdrojovy workbook neexistuje: {source_file}"
        )

    if source_file.stat().st_size != expected_size:
        raise RuntimeError(
            "Velkost zdrojoveho workbooku sa nezhoduje s audit batchom."
        )

    if sha256_file(source_file) != expected_sha256:
        raise RuntimeError(
            "SHA-256 zdrojoveho workbooku sa nezhoduje s audit batchom."
        )

    temporary_file: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=archive_file.parent,
            prefix=f".{archive_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_file = Path(temporary.name)
            with source_file.open("rb") as source:
                shutil.copyfileobj(source, temporary, 1024 * 1024)
            temporary.flush()
            os.fsync(temporary.fileno())

        if temporary_file.stat().st_size != expected_size:
            raise RuntimeError(
                "Docasna archivna kopia ma inu velkost."
            )

        if sha256_file(temporary_file) != expected_sha256:
            raise RuntimeError(
                "Docasna archivna kopia ma iny SHA-256."
            )

        os.replace(temporary_file, archive_file)
        temporary_file = None

        directory_fd = os.open(
            archive_file.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    finally:
        if temporary_file and temporary_file.exists():
            temporary_file.unlink()

    return "created"


def write_report(report_file: Path, report: dict[str, object]) -> None:
    report_file.parent.mkdir(parents=True, exist_ok=True)

    temporary_file: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=report_file.parent,
            prefix=f".{report_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_file = Path(temporary.name)
            json.dump(
                report,
                temporary,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                default=json_default,
            )
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())

        os.replace(temporary_file, report_file)
        temporary_file = None

        directory_fd = os.open(
            report_file.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    finally:
        if temporary_file and temporary_file.exists():
            temporary_file.unlink()


def table_counts(
    cursor,
    *,
    schema_name: str,
    table_names: tuple[str, ...],
    batch_id: uuid.UUID | None,
    batch_column: str | None,
) -> dict[str, int]:
    counts: dict[str, int] = {}

    for table_name in table_names:
        if batch_id is None or batch_column is None:
            cursor.execute(
                f"SELECT count(*) AS count FROM {schema_name}.{table_name}"
            )
        else:
            cursor.execute(
                f"""
                SELECT count(*) AS count
                FROM {schema_name}.{table_name}
                WHERE {batch_column} = %s
                """,
                (batch_id,),
            )

        counts[table_name] = int(cursor.fetchone()["count"])

    return counts


def main() -> int:
    args = parse_args()

    if not args.db_password_file.is_file():
        print(
            "CHYBA: subor s databazovym heslom neexistuje.",
            file=sys.stderr,
        )
        return 2

    password = args.db_password_file.read_text(
        encoding="utf-8"
    ).strip()

    connection = psycopg.connect(
        host=args.db_host,
        port=args.db_port,
        dbname=args.db_name,
        user=args.db_user,
        password=password,
        row_factory=dict_row,
    )

    try:
        with connection.cursor() as cursor:
            if args.batch_id:
                cursor.execute(
                    """
                    SELECT *
                    FROM audit.import_batches
                    WHERE id = %s
                    """,
                    (args.batch_id,),
                )
            else:
                cursor.execute(
                    """
                    SELECT *
                    FROM audit.import_batches
                    WHERE file_sha256 = %s
                    """,
                    (args.file_sha256.lower(),),
                )

            batch = cursor.fetchone()
            if not batch:
                print(
                    "CHYBA: import batch neexistuje.",
                    file=sys.stderr,
                )
                return 2

            if batch["status"] != "completed":
                print(
                    "CHYBA: finalizovat mozno iba completed batch; "
                    f"aktualny status je {batch['status']}.",
                    file=sys.stderr,
                )
                return 2

            batch_id = batch["id"]
            finished_at = batch["finished_at"] or datetime.now().astimezone()
            archive_directory = (
                args.archive_root
                / f"{finished_at.year:04d}"
                / f"{finished_at.month:02d}"
            )
            archive_name = (
                f"{batch['file_sha256']}__{batch['original_filename']}"
            )
            archive_file = archive_directory / archive_name
            report_file = args.report_root / f"{batch_id}.json"

            archive_result = ensure_archive(
                source_file=args.source_file,
                archive_file=archive_file,
                expected_sha256=batch["file_sha256"],
                expected_size=batch["file_size_bytes"],
            )

            raw_counts = table_counts(
                cursor,
                schema_name="raw",
                table_names=RAW_TABLES,
                batch_id=batch_id,
                batch_column="import_batch_id",
            )
            staging_counts = table_counts(
                cursor,
                schema_name="stg",
                table_names=STAGING_TABLES,
                batch_id=batch_id,
                batch_column="import_batch_id",
            )
            core_counts = table_counts(
                cursor,
                schema_name="core",
                table_names=CORE_TABLES,
                batch_id=batch_id,
                batch_column="source_import_batch_id",
            )

            cursor.execute(
                """
                SELECT severity, rule_code, count(*) AS count
                FROM audit.import_issues
                WHERE import_batch_id = %s
                GROUP BY severity, rule_code
                ORDER BY severity, rule_code
                """,
                (batch_id,),
            )
            issue_summary = cursor.fetchall()

            cursor.execute(
                """
                SELECT key, value
                FROM meta.system_info
                ORDER BY key
                """
            )
            system_info = {
                row["key"]: row["value"]
                for row in cursor.fetchall()
            }

            report: dict[str, object] = {
                "batch": {
                    "id": batch_id,
                    "status": batch["status"],
                    "source_type": batch["source_type"],
                    "original_filename": batch["original_filename"],
                    "source_path": batch["source_path"],
                    "archive_path": str(archive_file),
                    "file_sha256": batch["file_sha256"],
                    "file_size_bytes": batch["file_size_bytes"],
                    "platform_version_at_registration": (
                        batch["platform_version"]
                    ),
                    "schema_revision_at_registration": (
                        batch["schema_revision"]
                    ),
                    "started_at": batch["started_at"],
                    "finished_at": batch["finished_at"],
                    "created_by": batch["created_by"],
                    "error_count": batch["error_count"],
                    "warning_count": batch["warning_count"],
                    "row_count_raw": batch["row_count_raw"],
                    "row_count_core": batch["row_count_core"],
                    "metadata": batch["metadata"],
                },
                "system_info_current": system_info,
                "row_counts": {
                    "raw": raw_counts,
                    "staging": staging_counts,
                    "core": core_counts,
                    "raw_total": sum(raw_counts.values()),
                    "staging_total": sum(staging_counts.values()),
                    "core_total": sum(core_counts.values()),
                },
                "issues": issue_summary,
                "artifacts": {
                    "archive_file": str(archive_file),
                    "report_file": str(report_file),
                    "archive_result": archive_result,
                },
            }

            write_report(report_file, report)

            cursor.execute(
                """
                UPDATE audit.import_batches
                SET archive_path = %s,
                    metadata = metadata || %s
                WHERE id = %s
                """,
                (
                    str(archive_file),
                    Jsonb(
                        {
                            "finalization": {
                                "archive_path": str(archive_file),
                                "report_path": str(report_file),
                                "archive_result": archive_result,
                            }
                        }
                    ),
                    batch_id,
                ),
            )
            connection.commit()

    except Exception as exc:
        connection.rollback()
        print(
            f"CHYBA: finalizacia importu zlyhala: {exc}",
            file=sys.stderr,
        )
        return 1
    finally:
        connection.close()

    source_removed = False
    if args.move_source and args.source_file.exists():
        try:
            if sha256_file(args.source_file) != batch["file_sha256"]:
                raise RuntimeError(
                    "Zdroj sa pred odstranenim zmenil."
                )
            args.source_file.unlink()
            source_removed = True
        except Exception as exc:
            print(
                "CHYBA: archiv a report su vytvorene, ale zdroj "
                f"sa nepodarilo odstranit: {exc}",
                file=sys.stderr,
            )
            return 1

    print(f"IMPORT_BATCH_ID={batch_id}")
    print("IMPORT_BATCH_STATUS=completed")
    print(f"ARCHIVE_RESULT={archive_result}")
    print(f"ARCHIVE_PATH={archive_file}")
    print(f"REPORT_PATH={report_file}")
    print(f"SOURCE_REMOVED={'ANO' if source_removed else 'NIE'}")
    print(f"RAW_ROW_COUNT={sum(raw_counts.values())}")
    print(f"STAGING_ROW_COUNT={sum(staging_counts.values())}")
    print(f"CORE_ROW_COUNT={sum(core_counts.values())}")
    print("IMPORT_FINALIZATION_OK=ANO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
