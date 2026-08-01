#!/usr/bin/env python3
"""Validate and idempotently merge one staging batch into core."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
from pathlib import Path
import sys
import uuid

import psycopg
from psycopg.rows import dict_row, tuple_row
from psycopg.types.json import Jsonb


LOCK_KEY = 73002011
EXCLUDED_BASE_RULES = {
    "BIZ-E051",
    "CORE-E001",
    "CORE-E002",
    "CORE-E003",
    "CORE-E004",
}


def load_base_validations() -> tuple[tuple[str, str, str], ...]:
    path = Path(__file__).with_name("validate-and-promote-staging.py")
    spec = importlib.util.spec_from_file_location("legacy_promoter", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load legacy promoter: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(
        item for item in module.VALIDATIONS
        if item[0] not in EXCLUDED_BASE_RULES
    )


BASE_VALIDATIONS = load_base_validations()


def normalized_purchase_order_id(
    source_purchase_order_id: str,
    supplier: str,
    supplier_count: int,
) -> str:
    if supplier_count <= 1:
        return source_purchase_order_id
    suffix = hashlib.md5(  # nosec B324 - deterministic identifier, not security
        supplier.strip().lower().encode("utf-8")
    ).hexdigest()[:12]
    return f"{source_purchase_order_id}--{suffix}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Idempotently merge a staging XLSX batch into core."
    )
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--batch-id", type=uuid.UUID)
    selector.add_argument("--file-sha256")
    parser.add_argument("--db-host", default="postgres")
    parser.add_argument("--db-port", type=int, default=5432)
    parser.add_argument("--db-name", default="korporate_ai")
    parser.add_argument("--db-user", default="korporate_app")
    parser.add_argument("--db-password-file", type=Path, required=True)
    return parser.parse_args()


def add_issue(
    cursor: psycopg.Cursor,
    *,
    batch_id: uuid.UUID,
    severity: str,
    rule_code: str,
    sheet_name: str,
    row: tuple[object, ...],
) -> None:
    if len(row) != 6:
        raise RuntimeError(
            f"{rule_code} returned {len(row)} validation columns; "
            "expected exactly 6."
        )

    (
        source_row_number,
        business_key,
        column_name,
        actual_value,
        expected_condition,
        message,
    ) = row
    cursor.execute(
        """
        INSERT INTO audit.import_issues (
            import_batch_id,
            severity,
            rule_code,
            sheet_name,
            source_row_number,
            business_key,
            column_name,
            actual_value,
            expected_condition,
            message
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            batch_id,
            severity,
            rule_code,
            sheet_name,
            source_row_number,
            business_key,
            column_name,
            actual_value,
            expected_condition,
            message,
        ),
    )


def scalar(cursor: psycopg.Cursor, query: str, params: tuple[object, ...]) -> int:
    with cursor.connection.cursor(row_factory=tuple_row) as scalar_cursor:
        scalar_cursor.execute(query, params)
        row = scalar_cursor.fetchone()

    if row is None or len(row) != 1:
        raise RuntimeError(
            "Scalar query must return exactly one row with one column."
        )

    return int(row[0])


def classify(
    cursor: psycopg.Cursor,
    *,
    incoming_sql: str,
    target_sql: str,
    key_join: str,
    changed_predicate: str,
    params: tuple[object, ...],
) -> dict[str, int]:
    query = f"""
        WITH incoming AS ({incoming_sql}),
        target AS ({target_sql})
        SELECT
            count(*) FILTER (WHERE target.__exists IS NULL)::integer,
            count(*) FILTER (
                WHERE target.__exists IS NOT NULL
                  AND ({changed_predicate})
            )::integer,
            count(*) FILTER (
                WHERE target.__exists IS NOT NULL
                  AND NOT ({changed_predicate})
            )::integer
        FROM incoming
        LEFT JOIN target ON {key_join}
    """
    with cursor.connection.cursor(row_factory=tuple_row) as classify_cursor:
        classify_cursor.execute(query, params)
        row = classify_cursor.fetchone()

    if row is None or len(row) != 3:
        raise RuntimeError(
            "Merge classification must return exactly "
            "inserted, updated and unchanged counts."
        )

    inserted, updated, unchanged = row
    return {
        "inserted": int(inserted),
        "updated": int(updated),
        "unchanged": int(unchanged),
        "conflicts": 0,
    }


def create_purchase_temp(cursor: psycopg.Cursor, batch_id: uuid.UUID) -> None:
    cursor.execute("DROP TABLE IF EXISTS tmp_purchase_input")
    cursor.execute(
        """
        CREATE TEMP TABLE tmp_purchase_input ON COMMIT DROP AS
        WITH supplier_counts AS (
            SELECT
                import_batch_id,
                purchase_order_id,
                count(DISTINCT supplier)::integer AS supplier_count
            FROM stg.purchases
            WHERE import_batch_id = %s
            GROUP BY
                import_batch_id,
                purchase_order_id
        ),
        source AS (
            SELECT
                p.*,
                counts.supplier_count
            FROM stg.purchases AS p
            JOIN supplier_counts AS counts
              ON counts.import_batch_id = p.import_batch_id
             AND counts.purchase_order_id = p.purchase_order_id
            WHERE p.import_batch_id = %s
        )
        SELECT
            import_batch_id,
            source_row_number,
            purchase_order_id AS source_purchase_order_id,
            CASE
                WHEN supplier_count > 1 THEN
                    purchase_order_id || '--' ||
                    substr(md5(lower(trim(supplier))), 1, 12)
                ELSE purchase_order_id
            END AS proposed_purchase_order_id,
            order_date,
            delivery_date,
            supplier,
            product_id,
            ordered_quantity,
            delivered_quantity,
            purchase_price,
            supplier_count
        FROM source
        """,
        (batch_id, batch_id),
    )
    cursor.execute(
        """
        CREATE INDEX ON tmp_purchase_input (
            source_purchase_order_id,
            supplier,
            product_id
        )
        """
    )


def collect_conflicts(
    cursor: psycopg.Cursor,
    batch_id: uuid.UUID,
) -> tuple[int, dict[str, int]]:
    rules: list[tuple[str, str, str, tuple[object, ...]]] = []
    for rule_code, sheet_name, query in BASE_VALIDATIONS:
        rules.append((rule_code, sheet_name, query, (batch_id,)))

    rules.extend(
        [
            (
                "MERGE-E001",
                "sales",
                """
                SELECT
                    min(source_row_number),
                    order_id,
                    'product_id',
                    product_id,
                    'jeden produkt najviac raz v objednavke',
                    'Full-workbook merge vyzaduje unikatny order_id + product_id.'
                FROM stg.sales
                WHERE import_batch_id = %s
                GROUP BY order_id, product_id
                HAVING count(*) > 1
                """,
                (batch_id,),
            ),
            (
                "MERGE-E002",
                "purchases",
                """
                SELECT
                    min(source_row_number),
                    proposed_purchase_order_id,
                    'product_id',
                    product_id,
                    'jeden produkt najviac raz v normalizovanej PO',
                    'Full-workbook merge vyzaduje unikatny purchase_order + product.'
                FROM tmp_purchase_input
                GROUP BY proposed_purchase_order_id, product_id
                HAVING count(*) > 1
                """,
                (),
            ),
            (
                "MERGE-E010",
                "sales",
                """
                SELECT
                    min(s.source_row_number),
                    s.order_id,
                    'order header',
                    concat_ws(',', s.order_date, s.customer_id, s.region,
                              s.order_status, s.expedition_date),
                    'zhoda s existujucou historickou objednavkou',
                    'Historicka predajna objednavka sa nesmie potichu prepisat.'
                FROM stg.sales s
                JOIN core.sales_orders c ON c.order_id = s.order_id
                WHERE s.import_batch_id = %s
                  AND (
                    c.order_date IS DISTINCT FROM s.order_date OR
                    c.customer_id IS DISTINCT FROM s.customer_id OR
                    c.region IS DISTINCT FROM s.region OR
                    c.order_status IS DISTINCT FROM s.order_status OR
                    c.expedition_date IS DISTINCT FROM s.expedition_date
                  )
                GROUP BY s.order_id, s.order_date, s.customer_id, s.region,
                         s.order_status, s.expedition_date
                """,
                (batch_id,),
            ),
            (
                "MERGE-W011",
                "sales",
                """
                SELECT
                    s.source_row_number,
                    s.order_id,
                    'sales line',
                    concat_ws(',', s.product_id, s.quantity, s.unit_price),
                    'zhoda s existujucim historickym riadkom',
                    'Historicky predajny riadok sa nesmie potichu prepisat.'
                FROM stg.sales s
                JOIN core.sales_order_lines c
                  ON c.order_id = s.order_id
                 AND c.product_id = s.product_id
                WHERE s.import_batch_id = %s
                  AND (
                    c.quantity IS DISTINCT FROM s.quantity OR
                    c.unit_price IS DISTINCT FROM s.unit_price
                  )
                """,
                (batch_id,),
            ),
            (
                "MERGE-E012",
                "sales",
                """
                SELECT
                    s.source_row_number,
                    s.order_id,
                    'product_id',
                    s.product_id,
                    'existujuca objednavka nesmie dostat novy historicky riadok',
                    'Doplnenie riadku do existujucej sales objednavky je konflikt.'
                FROM stg.sales s
                JOIN core.sales_orders o ON o.order_id = s.order_id
                LEFT JOIN core.sales_order_lines c
                  ON c.order_id = s.order_id
                 AND c.product_id = s.product_id
                WHERE s.import_batch_id = %s
                  AND c.id IS NULL
                """,
                (batch_id,),
            ),
            (
                "MERGE-E020",
                "expedition",
                """
                SELECT
                    e.source_row_number,
                    e.order_id,
                    'expedition',
                    concat_ws(',', e.received_at, e.picked_at, e.expedition_date,
                              e.delivery_type, e.vehicle_id, e.region,
                              e.weight_kg, e.volume_m3),
                    'zhoda s existujucou expediciou',
                    'Historicka expedicia sa nesmie potichu prepisat.'
                FROM stg.expedition e
                JOIN core.expeditions c ON c.order_id = e.order_id
                WHERE e.import_batch_id = %s
                  AND (
                    c.received_at IS DISTINCT FROM e.received_at OR
                    c.picked_at IS DISTINCT FROM e.picked_at OR
                    c.expedition_date IS DISTINCT FROM e.expedition_date OR
                    c.delivery_type IS DISTINCT FROM e.delivery_type OR
                    c.vehicle_id IS DISTINCT FROM e.vehicle_id OR
                    c.region IS DISTINCT FROM e.region OR
                    c.weight_kg IS DISTINCT FROM e.weight_kg OR
                    c.volume_m3 IS DISTINCT FROM e.volume_m3
                  )
                """,
                (batch_id,),
            ),
            (
                "MERGE-E030",
                "purchases",
                """
                SELECT
                    min(t.source_row_number),
                    t.source_purchase_order_id,
                    'order_date',
                    t.order_date::text,
                    'zhoda s existujucou PO pre rovnakeho dodavatela',
                    'Datum existujucej nakupnej objednavky sa nesmie zmenit.'
                FROM tmp_purchase_input t
                JOIN core.suppliers s ON s.supplier_name = t.supplier
                JOIN core.purchase_orders po
                  ON po.source_purchase_order_id = t.source_purchase_order_id
                 AND po.supplier_id = s.id
                WHERE po.order_date IS DISTINCT FROM t.order_date
                GROUP BY t.source_purchase_order_id, t.order_date
                """,
                (),
            ),
            (
                "MERGE-E031",
                "purchases",
                """
                SELECT
                    t.source_row_number,
                    t.source_purchase_order_id,
                    'purchase line',
                    concat_ws(',', t.product_id, t.ordered_quantity,
                              t.delivered_quantity, t.purchase_price),
                    'ordered_quantity a purchase_price stabilne; delivered neklesa',
                    'Nakupny riadok obsahuje nepovolenu historicku zmenu.'
                FROM tmp_purchase_input t
                JOIN core.suppliers s ON s.supplier_name = t.supplier
                JOIN core.purchase_orders po
                  ON po.source_purchase_order_id = t.source_purchase_order_id
                 AND po.supplier_id = s.id
                JOIN core.purchase_order_lines l
                  ON l.purchase_order_id = po.purchase_order_id
                 AND l.product_id = t.product_id
                WHERE l.ordered_quantity IS DISTINCT FROM t.ordered_quantity
                   OR l.purchase_price IS DISTINCT FROM t.purchase_price
                   OR t.delivered_quantity < l.delivered_quantity
                """,
                (),
            ),
            (
                "MERGE-E032",
                "purchases",
                """
                SELECT
                    min(t.source_row_number),
                    t.proposed_purchase_order_id,
                    'purchase_order_id',
                    t.proposed_purchase_order_id,
                    'interny identifikator bez kolizie',
                    'Deterministicky interny PO identifikator koliduje s inou PO.'
                FROM tmp_purchase_input t
                JOIN core.purchase_orders po
                  ON po.purchase_order_id = t.proposed_purchase_order_id
                JOIN core.suppliers s ON s.id = po.supplier_id
                WHERE po.source_purchase_order_id IS DISTINCT FROM
                          t.source_purchase_order_id
                   OR s.supplier_name IS DISTINCT FROM t.supplier
                GROUP BY t.proposed_purchase_order_id
                """,
                (),
            ),
        ]
    )

    error_count = 0
    warning_count = 0
    error_rule_counts: dict[str, int] = {}
    warning_rule_counts: dict[str, int] = {}

    # Validation queries intentionally return six positional columns.
    # A dict_row cursor is unsafe here because repeated SQL column names
    # such as "?column?" are collapsed into fewer dictionary keys.
    with cursor.connection.cursor(row_factory=tuple_row) as validation_cursor:
        for rule_code, sheet_name, query, params in rules:
            validation_cursor.execute(query, params)
            rows = validation_cursor.fetchall()
            is_warning = rule_code.startswith("MERGE-W")
            severity = "WARNING" if is_warning else "ERROR"
            target_counts = (
                warning_rule_counts if is_warning else error_rule_counts
            )

            if rows:
                target_counts[rule_code] = (
                    target_counts.get(rule_code, 0) + len(rows)
                )

            for row in rows:
                add_issue(
                    cursor,
                    batch_id=batch_id,
                    severity=severity,
                    rule_code=rule_code,
                    sheet_name=sheet_name,
                    row=row,
                )
                if is_warning:
                    warning_count += 1
                else:
                    error_count += 1

    return (
        error_count,
        warning_count,
        error_rule_counts,
        warning_rule_counts,
    )


def main() -> int:
    args = parse_args()
    if not args.db_password_file.is_file():
        print("CHYBA: subor s databazovym heslom neexistuje.", file=sys.stderr)
        return 2

    password = args.db_password_file.read_text(encoding="utf-8").strip()
    connection = psycopg.connect(
        host=args.db_host,
        port=args.db_port,
        dbname=args.db_name,
        user=args.db_user,
        password=password,
        row_factory=dict_row,
    )

    batch_id: uuid.UUID | None = None
    lock_acquired = False
    summary: dict[str, dict[str, int]] = {}

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (LOCK_KEY,))
            lock_acquired = bool(cursor.fetchone()["acquired"])
        if not lock_acquired:
            print("CHYBA: iny full-workbook merge uz prebieha.", file=sys.stderr)
            return 2

        with connection.transaction():
            with connection.cursor() as cursor:
                if args.batch_id:
                    cursor.execute(
                        """
                        SELECT id, status, row_count_core, metadata
                        FROM audit.import_batches
                        WHERE id = %s
                        FOR UPDATE
                        """,
                        (args.batch_id,),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT id, status, row_count_core, metadata
                        FROM audit.import_batches
                        WHERE file_sha256 = %s
                        FOR UPDATE
                        """,
                        (args.file_sha256.lower(),),
                    )
                batch = cursor.fetchone()
                if not batch:
                    raise RuntimeError("Import batch neexistuje.")
                batch_id = batch["id"]
                status = batch["status"]
                if status == "completed":
                    print("BATCH_ALREADY_COMPLETED=ANO")
                    print(f"IMPORT_BATCH_ID={batch_id}")
                    print(f"CORE_ROW_COUNT={batch['row_count_core']}")
                    print("IMPORT_BATCH_STATUS=completed")
                    return 0
                if status != "staging_loaded":
                    raise RuntimeError(
                        "Batch musi mat status staging_loaded; "
                        f"aktualny status je {status}."
                    )

                cursor.execute(
                    "DELETE FROM audit.import_issues WHERE import_batch_id = %s",
                    (batch_id,),
                )
                create_purchase_temp(cursor, batch_id)
                (
                    error_count,
                    warning_count,
                    rule_counts,
                    warning_rule_counts,
                ) = collect_conflicts(cursor, batch_id)
                if error_count:
                    cursor.execute(
                        """
                        UPDATE audit.import_batches
                        SET status = 'rejected',
                            finished_at = now(),
                            error_count = %s,
                            warning_count = %s,
                            metadata = metadata || %s
                        WHERE id = %s
                        """,
                        (
                            error_count,
                            warning_count,
                            Jsonb(
                                {
                                    "merge_mode": "full_workbook_v1",
                                    "business_validation": {
                                        "status": "rejected",
                                        "error_count": error_count,
                                        "warning_count": warning_count,
                                        "rule_counts": rule_counts,
                                        "warning_rule_counts": warning_rule_counts,
                                    },
                                }
                            ),
                            batch_id,
                        ),
                    )
                    print("BATCH_REJECTED=ANO")
                    print(f"IMPORT_BATCH_ID={batch_id}")
                    print(f"BUSINESS_ERROR_COUNT={error_count}")
                    for rule_code, count in sorted(rule_counts.items()):
                        print(f"ERROR_CODE={rule_code} ERROR_COUNT={count}")
                    print("IMPORT_BATCH_STATUS=rejected")
                    return 1

                # Suppliers
                summary["suppliers"] = classify(
                    cursor,
                    incoming_sql="""
                        SELECT DISTINCT supplier_name
                        FROM (
                            SELECT supplier AS supplier_name
                            FROM stg.products WHERE import_batch_id = %s
                            UNION
                            SELECT supplier AS supplier_name
                            FROM stg.purchases WHERE import_batch_id = %s
                        ) q
                    """,
                    target_sql="""
                        SELECT supplier_name, true AS __exists
                        FROM core.suppliers
                    """,
                    key_join="target.supplier_name = incoming.supplier_name",
                    changed_predicate="false",
                    params=(batch_id, batch_id),
                )
                cursor.execute(
                    """
                    INSERT INTO core.suppliers (supplier_name, source_import_batch_id)
                    SELECT DISTINCT supplier_name, %s
                    FROM (
                        SELECT supplier AS supplier_name
                        FROM stg.products WHERE import_batch_id = %s
                        UNION
                        SELECT supplier AS supplier_name
                        FROM stg.purchases WHERE import_batch_id = %s
                    ) q
                    ON CONFLICT (supplier_name) DO NOTHING
                    """,
                    (batch_id, batch_id, batch_id),
                )

                # Customers
                summary["customers"] = classify(
                    cursor,
                    incoming_sql="""
                        SELECT customer_id, min(customer_name) AS customer_name
                        FROM stg.sales WHERE import_batch_id = %s
                        GROUP BY customer_id
                    """,
                    target_sql="""
                        SELECT customer_id, customer_name, true AS __exists
                        FROM core.customers
                    """,
                    key_join="target.customer_id = incoming.customer_id",
                    changed_predicate=(
                        "target.customer_name IS DISTINCT FROM incoming.customer_name"
                    ),
                    params=(batch_id,),
                )
                cursor.execute(
                    """
                    INSERT INTO core.customers (
                        customer_id, customer_name, source_import_batch_id
                    )
                    SELECT customer_id, min(customer_name), %s
                    FROM stg.sales WHERE import_batch_id = %s
                    GROUP BY customer_id
                    ON CONFLICT (customer_id) DO UPDATE
                    SET customer_name = EXCLUDED.customer_name,
                        source_import_batch_id = EXCLUDED.source_import_batch_id,
                        updated_at = now()
                    WHERE core.customers.customer_name IS DISTINCT FROM
                          EXCLUDED.customer_name
                    """,
                    (batch_id, batch_id),
                )

                # Products
                summary["products"] = classify(
                    cursor,
                    incoming_sql="""
                        SELECT p.*, s.id AS supplier_id
                        FROM stg.products p
                        JOIN core.suppliers s ON s.supplier_name = p.supplier
                        WHERE p.import_batch_id = %s
                    """,
                    target_sql="""
                        SELECT p.*, true AS __exists FROM core.products p
                    """,
                    key_join="target.product_id = incoming.product_id",
                    changed_predicate="""
                        target.product_name IS DISTINCT FROM incoming.product_name OR
                        target.category IS DISTINCT FROM incoming.category OR
                        target.unit IS DISTINCT FROM incoming.unit OR
                        target.purchase_price IS DISTINCT FROM incoming.purchase_price OR
                        target.sales_price IS DISTINCT FROM incoming.sales_price OR
                        target.supplier_id IS DISTINCT FROM incoming.supplier_id OR
                        target.minimum_order_quantity IS DISTINCT FROM incoming.minimum_order_quantity OR
                        target.lead_time_days IS DISTINCT FROM incoming.lead_time_days OR
                        target.weight_kg IS DISTINCT FROM incoming.weight_kg OR
                        target.volume_m3 IS DISTINCT FROM incoming.volume_m3
                    """,
                    params=(batch_id,),
                )
                cursor.execute(
                    """
                    INSERT INTO core.products (
                        product_id, product_name, category, unit,
                        purchase_price, sales_price, supplier_id,
                        minimum_order_quantity, lead_time_days,
                        weight_kg, volume_m3,
                        source_import_batch_id, source_row_number
                    )
                    SELECT
                        p.product_id, p.product_name, p.category, p.unit,
                        p.purchase_price, p.sales_price, s.id,
                        p.minimum_order_quantity, p.lead_time_days,
                        p.weight_kg, p.volume_m3,
                        p.import_batch_id, p.source_row_number
                    FROM stg.products p
                    JOIN core.suppliers s ON s.supplier_name = p.supplier
                    WHERE p.import_batch_id = %s
                    ON CONFLICT (product_id) DO UPDATE
                    SET product_name = EXCLUDED.product_name,
                        category = EXCLUDED.category,
                        unit = EXCLUDED.unit,
                        purchase_price = EXCLUDED.purchase_price,
                        sales_price = EXCLUDED.sales_price,
                        supplier_id = EXCLUDED.supplier_id,
                        minimum_order_quantity = EXCLUDED.minimum_order_quantity,
                        lead_time_days = EXCLUDED.lead_time_days,
                        weight_kg = EXCLUDED.weight_kg,
                        volume_m3 = EXCLUDED.volume_m3,
                        source_import_batch_id = EXCLUDED.source_import_batch_id,
                        source_row_number = EXCLUDED.source_row_number,
                        updated_at = now()
                    WHERE ROW(
                        core.products.product_name,
                        core.products.category,
                        core.products.unit,
                        core.products.purchase_price,
                        core.products.sales_price,
                        core.products.supplier_id,
                        core.products.minimum_order_quantity,
                        core.products.lead_time_days,
                        core.products.weight_kg,
                        core.products.volume_m3
                    ) IS DISTINCT FROM ROW(
                        EXCLUDED.product_name,
                        EXCLUDED.category,
                        EXCLUDED.unit,
                        EXCLUDED.purchase_price,
                        EXCLUDED.sales_price,
                        EXCLUDED.supplier_id,
                        EXCLUDED.minimum_order_quantity,
                        EXCLUDED.lead_time_days,
                        EXCLUDED.weight_kg,
                        EXCLUDED.volume_m3
                    )
                    """,
                    (batch_id,),
                )

                # Vehicles
                summary["vehicles"] = classify(
                    cursor,
                    incoming_sql="SELECT * FROM stg.vehicles WHERE import_batch_id = %s",
                    target_sql="SELECT v.*, true AS __exists FROM core.vehicles v",
                    key_join="target.vehicle_id = incoming.vehicle_id",
                    changed_predicate="""
                        target.capacity_kg IS DISTINCT FROM incoming.capacity_kg OR
                        target.capacity_m3 IS DISTINCT FROM incoming.capacity_m3 OR
                        target.availability IS DISTINCT FROM incoming.availability OR
                        target.cost_per_km IS DISTINCT FROM incoming.cost_per_km OR
                        target.driver IS DISTINCT FROM incoming.driver
                    """,
                    params=(batch_id,),
                )
                cursor.execute(
                    """
                    INSERT INTO core.vehicles (
                        vehicle_id, capacity_kg, capacity_m3, availability,
                        cost_per_km, driver,
                        source_import_batch_id, source_row_number
                    )
                    SELECT vehicle_id, capacity_kg, capacity_m3, availability,
                           cost_per_km, driver, import_batch_id, source_row_number
                    FROM stg.vehicles WHERE import_batch_id = %s
                    ON CONFLICT (vehicle_id) DO UPDATE
                    SET capacity_kg = EXCLUDED.capacity_kg,
                        capacity_m3 = EXCLUDED.capacity_m3,
                        availability = EXCLUDED.availability,
                        cost_per_km = EXCLUDED.cost_per_km,
                        driver = EXCLUDED.driver,
                        source_import_batch_id = EXCLUDED.source_import_batch_id,
                        source_row_number = EXCLUDED.source_row_number,
                        updated_at = now()
                    WHERE ROW(
                        core.vehicles.capacity_kg,
                        core.vehicles.capacity_m3,
                        core.vehicles.availability,
                        core.vehicles.cost_per_km,
                        core.vehicles.driver
                    ) IS DISTINCT FROM ROW(
                        EXCLUDED.capacity_kg,
                        EXCLUDED.capacity_m3,
                        EXCLUDED.availability,
                        EXCLUDED.cost_per_km,
                        EXCLUDED.driver
                    )
                    """,
                    (batch_id,),
                )

                cursor.execute(
                    """
                    CREATE TEMP TABLE tmp_sales_orders_input ON COMMIT DROP AS
                    SELECT DISTINCT
                        order_id, order_date, customer_id, region,
                        order_status, expedition_date, import_batch_id
                    FROM stg.sales WHERE import_batch_id = %s
                    """,
                    (batch_id,),
                )
                summary["sales_orders"] = classify(
                    cursor,
                    incoming_sql="SELECT * FROM tmp_sales_orders_input",
                    target_sql="SELECT o.*, true AS __exists FROM core.sales_orders o",
                    key_join="target.order_id = incoming.order_id",
                    changed_predicate="false",
                    params=(),
                )
                cursor.execute(
                    """
                    INSERT INTO core.sales_orders (
                        order_id, order_date, customer_id, region,
                        order_status, expedition_date, source_import_batch_id
                    )
                    SELECT order_id, order_date, customer_id, region,
                           order_status, expedition_date, import_batch_id
                    FROM tmp_sales_orders_input
                    ON CONFLICT (order_id) DO NOTHING
                    """
                )

                summary["sales_order_lines"] = classify(
                    cursor,
                    incoming_sql="SELECT * FROM stg.sales WHERE import_batch_id = %s",
                    target_sql="""
                        SELECT order_id, product_id, quantity, unit_price,
                               true AS __exists
                        FROM core.sales_order_lines
                    """,
                    key_join="""
                        target.order_id = incoming.order_id AND
                        target.product_id = incoming.product_id
                    """,
                    changed_predicate="false",
                    params=(batch_id,),
                )
                cursor.execute(
                    """
                    WITH missing AS (
                        SELECT s.*,
                               row_number() OVER (
                                   PARTITION BY s.order_id
                                   ORDER BY s.source_row_number
                               ) AS new_line_number
                        FROM stg.sales s
                        LEFT JOIN core.sales_order_lines c
                          ON c.order_id = s.order_id
                         AND c.product_id = s.product_id
                        WHERE s.import_batch_id = %s
                          AND c.id IS NULL
                    )
                    INSERT INTO core.sales_order_lines (
                        order_id, line_number, product_id, quantity, unit_price,
                        source_import_batch_id, source_row_number
                    )
                    SELECT order_id, new_line_number, product_id, quantity,
                           unit_price, import_batch_id, source_row_number
                    FROM missing
                    """,
                    (batch_id,),
                )

                cursor.execute(
                    """
                    CREATE TEMP TABLE tmp_purchase_orders_input ON COMMIT DROP AS
                    SELECT DISTINCT
                        t.source_purchase_order_id,
                        coalesce(po.purchase_order_id,
                                 t.proposed_purchase_order_id)
                            AS purchase_order_id,
                        t.order_date,
                        s.id AS supplier_id,
                        t.import_batch_id
                    FROM tmp_purchase_input t
                    JOIN core.suppliers s ON s.supplier_name = t.supplier
                    LEFT JOIN core.purchase_orders po
                      ON po.source_purchase_order_id = t.source_purchase_order_id
                     AND po.supplier_id = s.id
                    """
                )
                summary["purchase_orders"] = classify(
                    cursor,
                    incoming_sql="SELECT * FROM tmp_purchase_orders_input",
                    target_sql="""
                        SELECT source_purchase_order_id, supplier_id,
                               true AS __exists
                        FROM core.purchase_orders
                    """,
                    key_join="""
                        target.source_purchase_order_id = incoming.source_purchase_order_id
                        AND target.supplier_id = incoming.supplier_id
                    """,
                    changed_predicate="false",
                    params=(),
                )
                cursor.execute(
                    """
                    INSERT INTO core.purchase_orders (
                        purchase_order_id, source_purchase_order_id,
                        order_date, supplier_id, source_import_batch_id
                    )
                    SELECT purchase_order_id, source_purchase_order_id,
                           order_date, supplier_id, import_batch_id
                    FROM tmp_purchase_orders_input
                    ON CONFLICT (source_purchase_order_id, supplier_id) DO NOTHING
                    """
                )

                cursor.execute(
                    """
                    CREATE TEMP TABLE tmp_purchase_lines_input ON COMMIT DROP AS
                    SELECT
                        o.purchase_order_id,
                        t.product_id,
                        t.delivery_date,
                        t.ordered_quantity,
                        t.delivered_quantity,
                        t.purchase_price,
                        t.import_batch_id,
                        t.source_row_number
                    FROM tmp_purchase_input t
                    JOIN core.suppliers s ON s.supplier_name = t.supplier
                    JOIN tmp_purchase_orders_input o
                      ON o.source_purchase_order_id = t.source_purchase_order_id
                     AND o.supplier_id = s.id
                    """
                )
                summary["purchase_order_lines"] = classify(
                    cursor,
                    incoming_sql="SELECT * FROM tmp_purchase_lines_input",
                    target_sql="""
                        SELECT purchase_order_id, product_id, delivery_date,
                               ordered_quantity, delivered_quantity,
                               purchase_price, true AS __exists
                        FROM core.purchase_order_lines
                    """,
                    key_join="""
                        target.purchase_order_id = incoming.purchase_order_id AND
                        target.product_id = incoming.product_id
                    """,
                    changed_predicate="""
                        target.delivery_date IS DISTINCT FROM incoming.delivery_date OR
                        target.delivered_quantity IS DISTINCT FROM incoming.delivered_quantity
                    """,
                    params=(),
                )
                cursor.execute(
                    """
                    WITH missing AS (
                        SELECT i.*,
                               coalesce(max_line.max_line, 0) +
                               row_number() OVER (
                                   PARTITION BY i.purchase_order_id
                                   ORDER BY i.source_row_number
                               ) AS line_number
                        FROM tmp_purchase_lines_input i
                        LEFT JOIN core.purchase_order_lines c
                          ON c.purchase_order_id = i.purchase_order_id
                         AND c.product_id = i.product_id
                        LEFT JOIN LATERAL (
                            SELECT max(line_number) AS max_line
                            FROM core.purchase_order_lines existing
                            WHERE existing.purchase_order_id = i.purchase_order_id
                        ) max_line ON true
                        WHERE c.id IS NULL
                    )
                    INSERT INTO core.purchase_order_lines (
                        purchase_order_id, line_number, delivery_date, product_id,
                        ordered_quantity, delivered_quantity, purchase_price,
                        source_import_batch_id, source_row_number
                    )
                    SELECT purchase_order_id, line_number, delivery_date, product_id,
                           ordered_quantity, delivered_quantity, purchase_price,
                           import_batch_id, source_row_number
                    FROM missing
                    """
                )
                cursor.execute(
                    """
                    UPDATE core.purchase_order_lines c
                    SET delivery_date = i.delivery_date,
                        delivered_quantity = i.delivered_quantity,
                        source_import_batch_id = i.import_batch_id,
                        source_row_number = i.source_row_number
                    FROM tmp_purchase_lines_input i
                    WHERE c.purchase_order_id = i.purchase_order_id
                      AND c.product_id = i.product_id
                      AND (
                        c.delivery_date IS DISTINCT FROM i.delivery_date OR
                        c.delivered_quantity IS DISTINCT FROM i.delivered_quantity
                      )
                    """
                )

                summary["inventory_snapshots"] = classify(
                    cursor,
                    incoming_sql="""
                        SELECT DISTINCT snapshot_date, import_batch_id
                        FROM stg.inventory WHERE import_batch_id = %s
                    """,
                    target_sql="""
                        SELECT snapshot_date, true AS __exists
                        FROM core.inventory_snapshots
                    """,
                    key_join="target.snapshot_date = incoming.snapshot_date",
                    changed_predicate="false",
                    params=(batch_id,),
                )
                cursor.execute(
                    """
                    INSERT INTO core.inventory_snapshots (
                        snapshot_date, source_import_batch_id
                    )
                    SELECT DISTINCT snapshot_date, import_batch_id
                    FROM stg.inventory WHERE import_batch_id = %s
                    ON CONFLICT (snapshot_date) DO NOTHING
                    """,
                    (batch_id,),
                )

                summary["inventory_snapshot_lines"] = classify(
                    cursor,
                    incoming_sql="""
                        SELECT s.id AS snapshot_id, i.*
                        FROM stg.inventory i
                        JOIN core.inventory_snapshots s
                          ON s.snapshot_date = i.snapshot_date
                        WHERE i.import_batch_id = %s
                    """,
                    target_sql="""
                        SELECT l.*, true AS __exists
                        FROM core.inventory_snapshot_lines l
                    """,
                    key_join="""
                        target.snapshot_id = incoming.snapshot_id AND
                        target.product_id = incoming.product_id
                    """,
                    changed_predicate="""
                        target.stock_actual IS DISTINCT FROM incoming.stock_actual OR
                        target.stock_reserved IS DISTINCT FROM incoming.stock_reserved OR
                        target.stock_available IS DISTINCT FROM incoming.stock_available OR
                        target.warehouse_location IS DISTINCT FROM incoming.warehouse_location OR
                        target.min_stock IS DISTINCT FROM incoming.min_stock OR
                        target.max_stock IS DISTINCT FROM incoming.max_stock
                    """,
                    params=(batch_id,),
                )
                cursor.execute(
                    """
                    INSERT INTO core.inventory_snapshot_lines (
                        snapshot_id, product_id, stock_actual, stock_reserved,
                        stock_available, warehouse_location, min_stock, max_stock,
                        source_import_batch_id, source_row_number
                    )
                    SELECT s.id, i.product_id, i.stock_actual, i.stock_reserved,
                           i.stock_available, i.warehouse_location,
                           i.min_stock, i.max_stock,
                           i.import_batch_id, i.source_row_number
                    FROM stg.inventory i
                    JOIN core.inventory_snapshots s
                      ON s.snapshot_date = i.snapshot_date
                    WHERE i.import_batch_id = %s
                    ON CONFLICT (snapshot_id, product_id) DO UPDATE
                    SET stock_actual = EXCLUDED.stock_actual,
                        stock_reserved = EXCLUDED.stock_reserved,
                        stock_available = EXCLUDED.stock_available,
                        warehouse_location = EXCLUDED.warehouse_location,
                        min_stock = EXCLUDED.min_stock,
                        max_stock = EXCLUDED.max_stock,
                        source_import_batch_id = EXCLUDED.source_import_batch_id,
                        source_row_number = EXCLUDED.source_row_number
                    WHERE ROW(
                        core.inventory_snapshot_lines.stock_actual,
                        core.inventory_snapshot_lines.stock_reserved,
                        core.inventory_snapshot_lines.stock_available,
                        core.inventory_snapshot_lines.warehouse_location,
                        core.inventory_snapshot_lines.min_stock,
                        core.inventory_snapshot_lines.max_stock
                    ) IS DISTINCT FROM ROW(
                        EXCLUDED.stock_actual,
                        EXCLUDED.stock_reserved,
                        EXCLUDED.stock_available,
                        EXCLUDED.warehouse_location,
                        EXCLUDED.min_stock,
                        EXCLUDED.max_stock
                    )
                    """,
                    (batch_id,),
                )

                summary["expeditions"] = classify(
                    cursor,
                    incoming_sql="SELECT * FROM stg.expedition WHERE import_batch_id = %s",
                    target_sql="SELECT e.*, true AS __exists FROM core.expeditions e",
                    key_join="target.order_id = incoming.order_id",
                    changed_predicate="false",
                    params=(batch_id,),
                )
                cursor.execute(
                    """
                    INSERT INTO core.expeditions (
                        order_id, received_at, picked_at, expedition_date,
                        delivery_type, vehicle_id, region, weight_kg, volume_m3,
                        source_import_batch_id, source_row_number
                    )
                    SELECT order_id, received_at, picked_at, expedition_date,
                           delivery_type, vehicle_id, region, weight_kg, volume_m3,
                           import_batch_id, source_row_number
                    FROM stg.expedition WHERE import_batch_id = %s
                    ON CONFLICT (order_id) DO NOTHING
                    """,
                    (batch_id,),
                )

                total_changed = sum(
                    item["inserted"] + item["updated"]
                    for item in summary.values()
                )
                cursor.execute(
                    """
                    UPDATE audit.import_batches
                    SET status = 'completed',
                        finished_at = now(),
                        error_count = 0,
                        warning_count = %s,
                        row_count_core = %s,
                        metadata = metadata || %s
                    WHERE id = %s
                    """,
                    (
                        warning_count,
                        total_changed,
                        Jsonb(
                            {
                                "merge_mode": "full_workbook_v1",
                                "business_validation": {
                                    "status": "passed",
                                    "error_count": 0,
                                    "warning_count": warning_count,
                                    "warning_rule_counts": warning_rule_counts,
                                },
                                "merge_summary": summary,
                                "automatic_deletes": 0,
                            }
                        ),
                        batch_id,
                    ),
                )

        print("BATCH_ALREADY_COMPLETED=NIE")
        print(f"IMPORT_BATCH_ID={batch_id}")
        print("BUSINESS_ERROR_COUNT=0")
        print(f"BUSINESS_WARNING_COUNT={warning_count}")
        for rule_code, count in sorted(warning_rule_counts.items()):
            print(f"WARNING_CODE={rule_code} WARNING_COUNT={count}")
        for name, counts in summary.items():
            print(
                f"MERGE_TABLE={name} "
                f"INSERTED={counts['inserted']} "
                f"UPDATED={counts['updated']} "
                f"UNCHANGED={counts['unchanged']} "
                f"CONFLICTS={counts['conflicts']}"
            )
        print(
            "CORE_ROW_COUNT="
            + str(
                sum(
                    item["inserted"] + item["updated"]
                    for item in summary.values()
                )
            )
        )
        print("AUTOMATIC_DELETES=0")
        print("MERGE_MODE=full_workbook_v1")
        print("IMPORT_BATCH_STATUS=completed")
        print("CORE_PROMOTION_OK=ANO")
        return 0

    except Exception as exc:
        connection.rollback()
        if batch_id is not None:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE audit.import_batches
                        SET status = 'failed',
                            finished_at = now(),
                            error_message = %s
                        WHERE id = %s
                          AND status = 'staging_loaded'
                        """,
                        (str(exc)[:4000], batch_id),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
        print(f"CHYBA: full-workbook merge zlyhal: {exc}", file=sys.stderr)
        return 1
    finally:
        if lock_acquired:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
                connection.commit()
            except Exception:
                connection.rollback()
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
