"""Transform a raw XLSX import batch into typed staging tables."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import uuid

import psycopg
from psycopg.types.json import Jsonb


TABLES = (
    "products",
    "sales",
    "inventory",
    "purchases",
    "expedition",
    "vehicles",
)


INSERT_SQL = {
    "products": """
        INSERT INTO stg.products (
            import_batch_id,
            source_row_number,
            product_id,
            product_name,
            category,
            unit,
            purchase_price,
            sales_price,
            supplier,
            minimum_order_quantity,
            lead_time_days,
            weight_kg,
            volume_m3
        )
        SELECT
            import_batch_id,
            source_row_number,
            source_data ->> 'product_id',
            source_data ->> 'product_name',
            source_data ->> 'category',
            source_data ->> 'unit',
            (source_data ->> 'purchase_price')::numeric(10,2),
            (source_data ->> 'sales_price')::numeric(10,2),
            source_data ->> 'supplier',
            (source_data ->> 'minimum_order_quantity')::integer,
            (source_data ->> 'lead_time_days')::integer,
            (source_data ->> 'weight_kg')::numeric(10,3),
            (source_data ->> 'volume_m3')::numeric(10,5)
        FROM raw.xlsx_products
        WHERE import_batch_id = %s
        ORDER BY source_row_number
    """,
    "sales": """
        INSERT INTO stg.sales (
            import_batch_id,
            source_row_number,
            order_id,
            order_date,
            product_id,
            quantity,
            unit_price,
            customer_id,
            customer_name,
            region,
            order_status,
            expedition_date
        )
        SELECT
            import_batch_id,
            source_row_number,
            source_data ->> 'order_id',
            (source_data ->> 'order_date')::timestamp::date,
            source_data ->> 'product_id',
            (source_data ->> 'quantity')::integer,
            (source_data ->> 'unit_price')::numeric(10,2),
            source_data ->> 'customer_id',
            source_data ->> 'customer_name',
            source_data ->> 'region',
            source_data ->> 'order_status',
            (source_data ->> 'expedition_date')::timestamp::date
        FROM raw.xlsx_sales
        WHERE import_batch_id = %s
        ORDER BY source_row_number
    """,
    "inventory": """
        INSERT INTO stg.inventory (
            import_batch_id,
            source_row_number,
            snapshot_date,
            product_id,
            stock_actual,
            stock_reserved,
            stock_available,
            warehouse_location,
            min_stock,
            max_stock
        )
        SELECT
            import_batch_id,
            source_row_number,
            (source_data ->> 'snapshot_date')::timestamp::date,
            source_data ->> 'product_id',
            (source_data ->> 'stock_actual')::integer,
            (source_data ->> 'stock_reserved')::integer,
            (source_data ->> 'stock_available')::integer,
            source_data ->> 'warehouse_location',
            (source_data ->> 'min_stock')::integer,
            (source_data ->> 'max_stock')::integer
        FROM raw.xlsx_inventory
        WHERE import_batch_id = %s
        ORDER BY source_row_number
    """,
    "purchases": """
        INSERT INTO stg.purchases (
            import_batch_id,
            source_row_number,
            purchase_order_id,
            order_date,
            delivery_date,
            supplier,
            product_id,
            ordered_quantity,
            delivered_quantity,
            purchase_price
        )
        SELECT
            import_batch_id,
            source_row_number,
            source_data ->> 'purchase_order_id',
            (source_data ->> 'order_date')::timestamp::date,
            (source_data ->> 'delivery_date')::timestamp::date,
            source_data ->> 'supplier',
            source_data ->> 'product_id',
            (source_data ->> 'ordered_quantity')::integer,
            (source_data ->> 'delivered_quantity')::integer,
            (source_data ->> 'purchase_price')::numeric(10,2)
        FROM raw.xlsx_purchases
        WHERE import_batch_id = %s
        ORDER BY source_row_number
    """,
    "expedition": """
        INSERT INTO stg.expedition (
            import_batch_id,
            source_row_number,
            order_id,
            received_at,
            picked_at,
            expedition_date,
            delivery_type,
            vehicle_id,
            region,
            weight_kg,
            volume_m3
        )
        SELECT
            import_batch_id,
            source_row_number,
            source_data ->> 'order_id',
            (source_data ->> 'received_at')::timestamp,
            (source_data ->> 'picked_at')::timestamp,
            (source_data ->> 'expedition_date')::timestamp::date,
            source_data ->> 'delivery_type',
            NULLIF(source_data ->> 'vehicle_id', ''),
            source_data ->> 'region',
            (source_data ->> 'weight_kg')::numeric(12,2),
            (source_data ->> 'volume_m3')::numeric(12,3)
        FROM raw.xlsx_expedition
        WHERE import_batch_id = %s
        ORDER BY source_row_number
    """,
    "vehicles": """
        INSERT INTO stg.vehicles (
            import_batch_id,
            source_row_number,
            vehicle_id,
            capacity_kg,
            capacity_m3,
            availability,
            cost_per_km,
            driver
        )
        SELECT
            import_batch_id,
            source_row_number,
            source_data ->> 'vehicle_id',
            (source_data ->> 'capacity_kg')::integer,
            (source_data ->> 'capacity_m3')::numeric(10,2),
            source_data ->> 'availability',
            (source_data ->> 'cost_per_km')::numeric(10,2),
            source_data ->> 'driver'
        FROM raw.xlsx_vehicles
        WHERE import_batch_id = %s
        ORDER BY source_row_number
    """,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform one raw import batch into typed staging."
    )
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--batch-id", type=uuid.UUID)
    selector.add_argument("--file-sha256")
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


def staging_counts(cursor, batch_id: uuid.UUID) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table_name in TABLES:
        cursor.execute(
            f"""
            SELECT count(*)
            FROM stg.{table_name}
            WHERE import_batch_id = %s
            """,
            (batch_id,),
        )
        counts[table_name] = cursor.fetchone()[0]
    return counts


def raw_counts(cursor, batch_id: uuid.UUID) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table_name in TABLES:
        cursor.execute(
            f"""
            SELECT count(*)
            FROM raw.xlsx_{table_name}
            WHERE import_batch_id = %s
            """,
            (batch_id,),
        )
        counts[table_name] = cursor.fetchone()[0]
    return counts


def main() -> int:
    args = parse_args()

    if not args.db_password_file.is_file():
        print(
            "CHYBA: subor s databazovym heslom neexistuje: "
            f"{args.db_password_file}",
            file=sys.stderr,
        )
        return 2

    password = args.db_password_file.read_text(encoding="utf-8").strip()
    connection = psycopg.connect(
        host=args.db_host,
        port=args.db_port,
        dbname=args.db_name,
        user=args.db_user,
        password=password,
    )

    try:
        with connection.cursor() as cursor:
            if args.batch_id:
                cursor.execute(
                    """
                    SELECT id, status, row_count_raw
                    FROM audit.import_batches
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (args.batch_id,),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, status, row_count_raw
                    FROM audit.import_batches
                    WHERE file_sha256 = %s
                    FOR UPDATE
                    """,
                    (args.file_sha256.lower(),),
                )

            batch = cursor.fetchone()
            if not batch:
                connection.rollback()
                print("CHYBA: import batch neexistuje.", file=sys.stderr)
                return 2

            batch_id, status, row_count_raw = batch
            current_staging_counts = staging_counts(cursor, batch_id)
            current_staging_total = sum(current_staging_counts.values())

            if status == "staging_loaded":
                connection.rollback()
                if current_staging_total != row_count_raw:
                    print(
                        "CHYBA: batch je staging_loaded, ale pocty nesedia.",
                        file=sys.stderr,
                    )
                    return 1

                print("BATCH_ALREADY_STAGED=ANO")
                print(f"IMPORT_BATCH_ID={batch_id}")
                for table_name in TABLES:
                    print(
                        f"TABLE={table_name} "
                        f"STAGING_ROWS={current_staging_counts[table_name]}"
                    )
                print(f"STAGING_ROW_COUNT={current_staging_total}")
                print("IMPORT_BATCH_STATUS=staging_loaded")
                return 0

            if status != "raw_loaded":
                connection.rollback()
                print(
                    "CHYBA: batch musi mat status raw_loaded; "
                    f"aktualny status je {status}.",
                    file=sys.stderr,
                )
                return 2

            if current_staging_total:
                connection.rollback()
                print(
                    "CHYBA: batch raw_loaded uz ma staging riadky.",
                    file=sys.stderr,
                )
                return 1

            current_raw_counts = raw_counts(cursor, batch_id)
            current_raw_total = sum(current_raw_counts.values())
            if current_raw_total != row_count_raw:
                connection.rollback()
                print(
                    "CHYBA: raw pocty sa nezhoduju s audit batchom.",
                    file=sys.stderr,
                )
                print(f"AUDIT_RAW_ROW_COUNT={row_count_raw}", file=sys.stderr)
                print(f"ACTUAL_RAW_ROW_COUNT={current_raw_total}", file=sys.stderr)
                return 1

            inserted_counts: dict[str, int] = {}
            for table_name in TABLES:
                cursor.execute(INSERT_SQL[table_name], (batch_id,))
                inserted_counts[table_name] = cursor.rowcount

            staging_total = sum(inserted_counts.values())
            if staging_total != row_count_raw:
                raise RuntimeError(
                    "Pocet staging riadkov sa nezhoduje s raw batchom: "
                    f"{staging_total} != {row_count_raw}"
                )

            cursor.execute(
                """
                UPDATE audit.import_batches
                SET status = 'staging_loaded',
                    metadata = metadata || %s
                WHERE id = %s
                """,
                (
                    Jsonb({"staging_row_counts": inserted_counts}),
                    batch_id,
                ),
            )
            connection.commit()

    except Exception as exc:
        connection.rollback()
        print(f"CHYBA: staging transformacia zlyhala: {exc}", file=sys.stderr)
        return 1
    finally:
        connection.close()

    print("BATCH_ALREADY_STAGED=NIE")
    print(f"IMPORT_BATCH_ID={batch_id}")
    for table_name in TABLES:
        print(
            f"TABLE={table_name} "
            f"STAGING_ROWS={inserted_counts[table_name]}"
        )
    print(f"STAGING_ROW_COUNT={staging_total}")
    print("IMPORT_BATCH_STATUS=staging_loaded")
    print("STAGING_TRANSFORM_OK=ANO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
