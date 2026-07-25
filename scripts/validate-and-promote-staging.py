"""Validate a staging batch and promote it into the core layer."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import uuid

import psycopg
from psycopg.types.json import Jsonb


VALIDATIONS = (
    (
        "BIZ-E010",
        "sales",
        """
        SELECT
            s.source_row_number,
            s.order_id,
            'product_id',
            s.product_id,
            'product_id musi existovat v stg.products',
            'Predaj odkazuje na neexistujuci produkt.'
        FROM stg.sales s
        LEFT JOIN stg.products p
          ON p.import_batch_id = s.import_batch_id
         AND p.product_id = s.product_id
        WHERE s.import_batch_id = %s
          AND p.product_id IS NULL
        """,
    ),
    (
        "BIZ-E011",
        "inventory",
        """
        SELECT
            i.source_row_number,
            i.product_id,
            'product_id',
            i.product_id,
            'product_id musi existovat v stg.products',
            'Sklad odkazuje na neexistujuci produkt.'
        FROM stg.inventory i
        LEFT JOIN stg.products p
          ON p.import_batch_id = i.import_batch_id
         AND p.product_id = i.product_id
        WHERE i.import_batch_id = %s
          AND p.product_id IS NULL
        """,
    ),
    (
        "BIZ-E012",
        "purchases",
        """
        SELECT
            pch.source_row_number,
            pch.purchase_order_id,
            'product_id',
            pch.product_id,
            'product_id musi existovat v stg.products',
            'Nakup odkazuje na neexistujuci produkt.'
        FROM stg.purchases pch
        LEFT JOIN stg.products p
          ON p.import_batch_id = pch.import_batch_id
         AND p.product_id = pch.product_id
        WHERE pch.import_batch_id = %s
          AND p.product_id IS NULL
        """,
    ),
    (
        "BIZ-E013",
        "expedition",
        """
        SELECT
            e.source_row_number,
            e.order_id,
            'order_id',
            e.order_id,
            'order_id musi existovat v stg.sales',
            'Expedicia odkazuje na neexistujucu objednavku.'
        FROM stg.expedition e
        LEFT JOIN stg.sales s
          ON s.import_batch_id = e.import_batch_id
         AND s.order_id = e.order_id
        WHERE e.import_batch_id = %s
          AND s.order_id IS NULL
        """,
    ),
    (
        "BIZ-E014",
        "expedition",
        """
        SELECT
            e.source_row_number,
            e.order_id,
            'vehicle_id',
            e.vehicle_id,
            'vyplnene vehicle_id musi existovat v stg.vehicles',
            'Expedicia odkazuje na neexistujuce vozidlo.'
        FROM stg.expedition e
        LEFT JOIN stg.vehicles v
          ON v.import_batch_id = e.import_batch_id
         AND v.vehicle_id = e.vehicle_id
        WHERE e.import_batch_id = %s
          AND e.vehicle_id IS NOT NULL
          AND v.vehicle_id IS NULL
        """,
    ),
    (
        "BIZ-E015",
        "sales",
        """
        SELECT
            min(s.source_row_number),
            s.order_id,
            'order_id',
            s.order_id,
            'kazda vybavena objednavka musi mat expediciu',
            'Predajna objednavka nema expedicny zaznam.'
        FROM stg.sales s
        LEFT JOIN stg.expedition e
          ON e.import_batch_id = s.import_batch_id
         AND e.order_id = s.order_id
        WHERE s.import_batch_id = %s
          AND e.order_id IS NULL
        GROUP BY s.order_id
        """,
    ),
    (
        "BIZ-E020",
        "inventory",
        """
        SELECT
            source_row_number,
            product_id,
            'stock_available',
            stock_available::text,
            'stock_available = stock_actual - stock_reserved',
            'Skladova rovnica nesedi.'
        FROM stg.inventory
        WHERE import_batch_id = %s
          AND stock_available <> stock_actual - stock_reserved
        """,
    ),
    (
        "BIZ-E021",
        "inventory",
        """
        SELECT
            source_row_number,
            product_id,
            'stock_reserved',
            stock_reserved::text,
            'stock_reserved <= stock_actual',
            'Rezervovane mnozstvo prevysuje fyzicky sklad.'
        FROM stg.inventory
        WHERE import_batch_id = %s
          AND stock_reserved > stock_actual
        """,
    ),
    (
        "BIZ-E022",
        "inventory",
        """
        SELECT
            source_row_number,
            product_id,
            'min_stock',
            min_stock::text,
            'min_stock <= max_stock',
            'Minimalny sklad prevysuje maximalny sklad.'
        FROM stg.inventory
        WHERE import_batch_id = %s
          AND min_stock > max_stock
        """,
    ),
    (
        "BIZ-E023",
        "inventory",
        """
        SELECT
            source_row_number,
            product_id,
            'stock values',
            concat_ws(
                ',',
                stock_actual,
                stock_reserved,
                stock_available,
                min_stock,
                max_stock
            ),
            'vsetky skladove mnozstva musia byt nezaporne',
            'Sklad obsahuje zapornu hodnotu.'
        FROM stg.inventory
        WHERE import_batch_id = %s
          AND (
              stock_actual < 0
              OR stock_reserved < 0
              OR stock_available < 0
              OR min_stock < 0
              OR max_stock < 0
          )
        """,
    ),
    (
        "BIZ-E030",
        "products",
        """
        SELECT
            source_row_number,
            product_id,
            'minimum_order_quantity',
            minimum_order_quantity::text,
            'minimum_order_quantity > 0',
            'Minimalne objednavacie mnozstvo musi byt kladne.'
        FROM stg.products
        WHERE import_batch_id = %s
          AND minimum_order_quantity <= 0
        """,
    ),
    (
        "BIZ-E030",
        "sales",
        """
        SELECT
            source_row_number,
            order_id,
            'quantity',
            quantity::text,
            'quantity > 0',
            'Predane mnozstvo musi byt kladne.'
        FROM stg.sales
        WHERE import_batch_id = %s
          AND quantity <= 0
        """,
    ),
    (
        "BIZ-E030",
        "purchases",
        """
        SELECT
            source_row_number,
            purchase_order_id,
            'ordered_quantity',
            ordered_quantity::text,
            'ordered_quantity > 0',
            'Objednane mnozstvo musi byt kladne.'
        FROM stg.purchases
        WHERE import_batch_id = %s
          AND ordered_quantity <= 0
        """,
    ),
    (
        "BIZ-E030",
        "vehicles",
        """
        SELECT
            source_row_number,
            vehicle_id,
            'capacity',
            concat_ws(',', capacity_kg, capacity_m3),
            'capacity_kg > 0 a capacity_m3 > 0',
            'Kapacita vozidla musi byt kladna.'
        FROM stg.vehicles
        WHERE import_batch_id = %s
          AND (capacity_kg <= 0 OR capacity_m3 <= 0)
        """,
    ),
    (
        "BIZ-E031",
        "products",
        """
        SELECT
            source_row_number,
            product_id,
            'numeric values',
            concat_ws(
                ',',
                purchase_price,
                sales_price,
                lead_time_days,
                weight_kg,
                volume_m3
            ),
            'ceny, lead time, hmotnost a objem musia byt nezaporne',
            'Produkt obsahuje zapornu business hodnotu.'
        FROM stg.products
        WHERE import_batch_id = %s
          AND (
              purchase_price < 0
              OR sales_price < 0
              OR lead_time_days < 0
              OR weight_kg < 0
              OR volume_m3 < 0
          )
        """,
    ),
    (
        "BIZ-E031",
        "sales",
        """
        SELECT
            source_row_number,
            order_id,
            'unit_price',
            unit_price::text,
            'unit_price >= 0',
            'Predajna cena je zaporna.'
        FROM stg.sales
        WHERE import_batch_id = %s
          AND unit_price < 0
        """,
    ),
    (
        "BIZ-E031",
        "purchases",
        """
        SELECT
            source_row_number,
            purchase_order_id,
            'numeric values',
            concat_ws(',', delivered_quantity, purchase_price),
            'delivered_quantity a purchase_price musia byt nezaporne',
            'Nakup obsahuje zapornu business hodnotu.'
        FROM stg.purchases
        WHERE import_batch_id = %s
          AND (delivered_quantity < 0 OR purchase_price < 0)
        """,
    ),
    (
        "BIZ-E031",
        "expedition",
        """
        SELECT
            source_row_number,
            order_id,
            'weight_kg/volume_m3',
            concat_ws(',', weight_kg, volume_m3),
            'hmotnost a objem musia byt nezaporne',
            'Expedicia obsahuje zapornu hmotnost alebo objem.'
        FROM stg.expedition
        WHERE import_batch_id = %s
          AND (weight_kg < 0 OR volume_m3 < 0)
        """,
    ),
    (
        "BIZ-E031",
        "vehicles",
        """
        SELECT
            source_row_number,
            vehicle_id,
            'cost_per_km',
            cost_per_km::text,
            'cost_per_km >= 0',
            'Naklad vozidla na kilometer je zaporny.'
        FROM stg.vehicles
        WHERE import_batch_id = %s
          AND cost_per_km < 0
        """,
    ),
    (
        "BIZ-E032",
        "purchases",
        """
        SELECT
            source_row_number,
            purchase_order_id,
            'delivered_quantity',
            delivered_quantity::text,
            'delivered_quantity <= ordered_quantity',
            'Dodane mnozstvo prevysuje objednane.'
        FROM stg.purchases
        WHERE import_batch_id = %s
          AND delivered_quantity > ordered_quantity
        """,
    ),
    (
        "BIZ-E040",
        "sales",
        """
        SELECT
            source_row_number,
            order_id,
            'expedition_date',
            expedition_date::text,
            'expedition_date >= order_date',
            'Expedicia je pred datumom objednavky.'
        FROM stg.sales
        WHERE import_batch_id = %s
          AND expedition_date < order_date
        """,
    ),
    (
        "BIZ-E041",
        "purchases",
        """
        SELECT
            source_row_number,
            purchase_order_id,
            'delivery_date',
            delivery_date::text,
            'delivery_date >= order_date',
            'Dodanie je pred datumom nakupnej objednavky.'
        FROM stg.purchases
        WHERE import_batch_id = %s
          AND delivery_date < order_date
        """,
    ),
    (
        "BIZ-E042",
        "expedition",
        """
        SELECT
            source_row_number,
            order_id,
            'picked_at',
            picked_at::text,
            'picked_at >= received_at',
            'Vychystanie je pred prijatim objednavky.'
        FROM stg.expedition
        WHERE import_batch_id = %s
          AND picked_at < received_at
        """,
    ),
    (
        "BIZ-E043",
        "expedition",
        """
        SELECT
            e.source_row_number,
            e.order_id,
            'received_at',
            e.received_at::text,
            'date(received_at) >= sales.order_date',
            'Expedicny proces zacal pred datumom objednavky.'
        FROM stg.expedition e
        JOIN stg.sales s
          ON s.import_batch_id = e.import_batch_id
         AND s.order_id = e.order_id
        WHERE e.import_batch_id = %s
        GROUP BY
            e.source_row_number,
            e.order_id,
            e.received_at
        HAVING e.received_at::date < min(s.order_date)
        """,
    ),
    (
        "BIZ-E044",
        "expedition",
        """
        SELECT
            source_row_number,
            order_id,
            'expedition_date',
            expedition_date::text,
            'expedition_date >= date(received_at)',
            'Expedicia je pred prijatim objednavky.'
        FROM stg.expedition
        WHERE import_batch_id = %s
          AND expedition_date < received_at::date
        """,
    ),
    (
        "BIZ-E045",
        "expedition",
        """
        SELECT
            source_row_number,
            order_id,
            'expedition_date',
            expedition_date::text,
            'expedition_date >= date(picked_at)',
            'Expedicia je pred ukoncenim vychystania.'
        FROM stg.expedition
        WHERE import_batch_id = %s
          AND expedition_date < picked_at::date
        """,
    ),
    (
        "BIZ-E050",
        "sales",
        """
        SELECT
            min(source_row_number),
            order_id,
            'order_id',
            count(
                DISTINCT (
                    order_date,
                    customer_id,
                    customer_name,
                    region,
                    order_status,
                    expedition_date
                )
            )::text,
            'jedna konzistentna hlavicka na order_id',
            'Predajna objednavka ma nekonzistentnu hlavicku.'
        FROM stg.sales
        WHERE import_batch_id = %s
        GROUP BY order_id
        HAVING count(
            DISTINCT (
                order_date,
                customer_id,
                customer_name,
                region,
                order_status,
                expedition_date
            )
        ) > 1
        """,
    ),
    (
        "BIZ-E051",
        "purchases",
        """
        SELECT
            min(source_row_number),
            purchase_order_id,
            'purchase_order_id',
            count(DISTINCT (order_date, supplier))::text,
            'jedna konzistentna hlavicka na purchase_order_id',
            'Nakupna objednavka ma nekonzistentnu hlavicku.'
        FROM stg.purchases
        WHERE import_batch_id = %s
        GROUP BY purchase_order_id
        HAVING count(DISTINCT (order_date, supplier)) > 1
        """,
    ),
    (
        "BIZ-E060",
        "sales",
        """
        SELECT
            source_row_number,
            order_id,
            'region',
            region,
            'BA, BB, KE, NR, PO, TN, TT alebo ZA',
            'Neplatny region predaja.'
        FROM stg.sales
        WHERE import_batch_id = %s
          AND region NOT IN ('BA', 'BB', 'KE', 'NR', 'PO', 'TN', 'TT', 'ZA')
        """,
    ),
    (
        "BIZ-E060",
        "expedition",
        """
        SELECT
            source_row_number,
            order_id,
            'region',
            region,
            'BA, BB, KE, NR, PO, TN, TT alebo ZA',
            'Neplatny region expedicie.'
        FROM stg.expedition
        WHERE import_batch_id = %s
          AND region NOT IN ('BA', 'BB', 'KE', 'NR', 'PO', 'TN', 'TT', 'ZA')
        """,
    ),
    (
        "BIZ-E061",
        "sales",
        """
        SELECT
            source_row_number,
            order_id,
            'order_status',
            order_status,
            'vybavena',
            'Neplatny stav objednavky.'
        FROM stg.sales
        WHERE import_batch_id = %s
          AND lower(order_status) <> 'vybavená'
        """,
    ),
    (
        "BIZ-E062",
        "expedition",
        """
        SELECT
            source_row_number,
            order_id,
            'delivery_type',
            delivery_type,
            'vlastna, externa alebo osobny odber',
            'Neplatny typ dopravy.'
        FROM stg.expedition
        WHERE import_batch_id = %s
          AND delivery_type NOT IN ('vlastná', 'externá', 'osobný odber')
        """,
    ),
    (
        "BIZ-E063",
        "vehicles",
        """
        SELECT
            source_row_number,
            vehicle_id,
            'availability',
            availability,
            'pondelok-piatok, pondelok-sobota alebo podla objednavky',
            'Neplatna dostupnost vozidla.'
        FROM stg.vehicles
        WHERE import_batch_id = %s
          AND availability NOT IN (
              'pondelok-piatok',
              'pondelok-sobota',
              'podľa objednávky'
          )
        """,
    ),
    (
        "BIZ-E070",
        "expedition",
        """
        SELECT
            source_row_number,
            order_id,
            'vehicle_id',
            NULL,
            'pri vlastnej doprave musi byt vehicle_id vyplnene',
            'Vlastna doprava nema priradene vozidlo.'
        FROM stg.expedition
        WHERE import_batch_id = %s
          AND delivery_type = 'vlastná'
          AND vehicle_id IS NULL
        """,
    ),
    (
        "BIZ-E080",
        "expedition",
        """
        SELECT
            e.source_row_number,
            e.order_id,
            'expedition_date',
            e.expedition_date::text,
            'zhoda so sales.expedition_date',
            'Datum expedicie sa nezhoduje medzi sales a expedition.'
        FROM stg.expedition e
        JOIN stg.sales s
          ON s.import_batch_id = e.import_batch_id
         AND s.order_id = e.order_id
        WHERE e.import_batch_id = %s
        GROUP BY
            e.source_row_number,
            e.order_id,
            e.expedition_date
        HAVING count(DISTINCT s.expedition_date) <> 1
            OR min(s.expedition_date) <> e.expedition_date
        """,
    ),
    (
        "BIZ-E081",
        "expedition",
        """
        SELECT
            e.source_row_number,
            e.order_id,
            'region',
            e.region,
            'zhoda so sales.region',
            'Region sa nezhoduje medzi sales a expedition.'
        FROM stg.expedition e
        JOIN stg.sales s
          ON s.import_batch_id = e.import_batch_id
         AND s.order_id = e.order_id
        WHERE e.import_batch_id = %s
        GROUP BY
            e.source_row_number,
            e.order_id,
            e.region
        HAVING count(DISTINCT s.region) <> 1
            OR min(s.region) <> e.region
        """,
    ),
    (
        "CORE-E001",
        "sales",
        """
        SELECT
            min(s.source_row_number),
            s.order_id,
            'order_id',
            s.order_id,
            'order_id este nesmie existovat v core.sales_orders',
            'Predajna objednavka uz existuje v core.'
        FROM stg.sales s
        JOIN core.sales_orders c
          ON c.order_id = s.order_id
        WHERE s.import_batch_id = %s
        GROUP BY s.order_id
        """,
    ),
    (
        "CORE-E002",
        "purchases",
        """
        SELECT
            min(p.source_row_number),
            p.purchase_order_id,
            'purchase_order_id',
            p.purchase_order_id,
            'purchase_order_id este nesmie existovat v core.purchase_orders',
            'Nakupna objednavka uz existuje v core.'
        FROM stg.purchases p
        JOIN core.purchase_orders c
          ON c.purchase_order_id = p.purchase_order_id
        WHERE p.import_batch_id = %s
        GROUP BY p.purchase_order_id
        """,
    ),
    (
        "CORE-E003",
        "inventory",
        """
        SELECT
            min(i.source_row_number),
            i.snapshot_date::text,
            'snapshot_date',
            i.snapshot_date::text,
            'snapshot_date este nesmie existovat v core.inventory_snapshots',
            'Skladovy snapshot uz existuje v core.'
        FROM stg.inventory i
        JOIN core.inventory_snapshots c
          ON c.snapshot_date = i.snapshot_date
        WHERE i.import_batch_id = %s
        GROUP BY i.snapshot_date
        """,
    ),
    (
        "CORE-E004",
        "expedition",
        """
        SELECT
            e.source_row_number,
            e.order_id,
            'order_id',
            e.order_id,
            'order_id este nesmie existovat v core.expeditions',
            'Expedicia uz existuje v core.'
        FROM stg.expedition e
        JOIN core.expeditions c
          ON c.order_id = e.order_id
        WHERE e.import_batch_id = %s
        """,
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate one staging batch and promote it to core."
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


def add_issue(
    cursor,
    *,
    batch_id,
    rule_code,
    sheet_name,
    row,
) -> None:
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
        VALUES (%s, 'ERROR', %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            batch_id,
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


def main() -> int:
    args = parse_args()

    if not args.db_password_file.is_file():
        print(
            "CHYBA: subor s databazovym heslom neexistuje.",
            file=sys.stderr,
        )
        return 2

    password = args.db_password_file.read_text(encoding="utf-8").strip()
    batch_id = None

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
                connection.rollback()
                print("CHYBA: import batch neexistuje.", file=sys.stderr)
                return 2

            batch_id, status, row_count_core, metadata = batch

            if status == "completed":
                connection.rollback()
                print("BATCH_ALREADY_COMPLETED=ANO")
                print(f"IMPORT_BATCH_ID={batch_id}")
                print(f"CORE_ROW_COUNT={row_count_core}")
                print("IMPORT_BATCH_STATUS=completed")
                return 0

            if status != "staging_loaded":
                connection.rollback()
                print(
                    "CHYBA: batch musi mat status staging_loaded; "
                    f"aktualny status je {status}.",
                    file=sys.stderr,
                )
                return 2

            cursor.execute(
                """
                DELETE FROM audit.import_issues
                WHERE import_batch_id = %s
                """,
                (batch_id,),
            )

            error_count = 0
            rule_counts: dict[str, int] = {}

            for rule_code, sheet_name, query in VALIDATIONS:
                cursor.execute(query, (batch_id,))
                rows = cursor.fetchall()
                if rows:
                    rule_counts[rule_code] = (
                        rule_counts.get(rule_code, 0) + len(rows)
                    )
                for row in rows:
                    add_issue(
                        cursor,
                        batch_id=batch_id,
                        rule_code=rule_code,
                        sheet_name=sheet_name,
                        row=row,
                    )
                    error_count += 1

            if error_count:
                cursor.execute(
                    """
                    UPDATE audit.import_batches
                    SET status = 'rejected',
                        finished_at = now(),
                        error_count = %s,
                        metadata = metadata || %s
                    WHERE id = %s
                    """,
                    (
                        error_count,
                        Jsonb(
                            {
                                "business_validation": {
                                    "status": "rejected",
                                    "rule_counts": rule_counts,
                                }
                            }
                        ),
                        batch_id,
                    ),
                )
                connection.commit()
                print("BATCH_REJECTED=ANO")
                print(f"IMPORT_BATCH_ID={batch_id}")
                print(f"BUSINESS_ERROR_COUNT={error_count}")
                for rule_code, count in sorted(rule_counts.items()):
                    print(f"ERROR_CODE={rule_code} ERROR_COUNT={count}")
                print("IMPORT_BATCH_STATUS=rejected")
                return 1

            counts: dict[str, int] = {}

            cursor.execute(
                """
                INSERT INTO core.suppliers (
                    supplier_name,
                    source_import_batch_id
                )
                SELECT supplier_name, %s
                FROM (
                    SELECT DISTINCT supplier AS supplier_name
                    FROM stg.products
                    WHERE import_batch_id = %s
                    UNION
                    SELECT DISTINCT supplier AS supplier_name
                    FROM stg.purchases
                    WHERE import_batch_id = %s
                ) suppliers
                ON CONFLICT (supplier_name) DO UPDATE
                SET source_import_batch_id = EXCLUDED.source_import_batch_id,
                    updated_at = now()
                """,
                (batch_id, batch_id, batch_id),
            )
            counts["suppliers"] = cursor.rowcount

            cursor.execute(
                """
                INSERT INTO core.customers (
                    customer_id,
                    customer_name,
                    source_import_batch_id
                )
                SELECT
                    customer_id,
                    min(customer_name),
                    %s
                FROM stg.sales
                WHERE import_batch_id = %s
                GROUP BY customer_id
                ON CONFLICT (customer_id) DO UPDATE
                SET customer_name = EXCLUDED.customer_name,
                    source_import_batch_id = EXCLUDED.source_import_batch_id,
                    updated_at = now()
                """,
                (batch_id, batch_id),
            )
            counts["customers"] = cursor.rowcount

            cursor.execute(
                """
                INSERT INTO core.products (
                    product_id,
                    product_name,
                    category,
                    unit,
                    purchase_price,
                    sales_price,
                    supplier_id,
                    minimum_order_quantity,
                    lead_time_days,
                    weight_kg,
                    volume_m3,
                    source_import_batch_id,
                    source_row_number
                )
                SELECT
                    p.product_id,
                    p.product_name,
                    p.category,
                    p.unit,
                    p.purchase_price,
                    p.sales_price,
                    s.id,
                    p.minimum_order_quantity,
                    p.lead_time_days,
                    p.weight_kg,
                    p.volume_m3,
                    p.import_batch_id,
                    p.source_row_number
                FROM stg.products p
                JOIN core.suppliers s
                  ON s.supplier_name = p.supplier
                WHERE p.import_batch_id = %s
                ON CONFLICT (product_id) DO UPDATE
                SET product_name = EXCLUDED.product_name,
                    category = EXCLUDED.category,
                    unit = EXCLUDED.unit,
                    purchase_price = EXCLUDED.purchase_price,
                    sales_price = EXCLUDED.sales_price,
                    supplier_id = EXCLUDED.supplier_id,
                    minimum_order_quantity =
                        EXCLUDED.minimum_order_quantity,
                    lead_time_days = EXCLUDED.lead_time_days,
                    weight_kg = EXCLUDED.weight_kg,
                    volume_m3 = EXCLUDED.volume_m3,
                    source_import_batch_id =
                        EXCLUDED.source_import_batch_id,
                    source_row_number = EXCLUDED.source_row_number,
                    updated_at = now()
                """,
                (batch_id,),
            )
            counts["products"] = cursor.rowcount

            cursor.execute(
                """
                INSERT INTO core.vehicles (
                    vehicle_id,
                    capacity_kg,
                    capacity_m3,
                    availability,
                    cost_per_km,
                    driver,
                    source_import_batch_id,
                    source_row_number
                )
                SELECT
                    vehicle_id,
                    capacity_kg,
                    capacity_m3,
                    availability,
                    cost_per_km,
                    driver,
                    import_batch_id,
                    source_row_number
                FROM stg.vehicles
                WHERE import_batch_id = %s
                ON CONFLICT (vehicle_id) DO UPDATE
                SET capacity_kg = EXCLUDED.capacity_kg,
                    capacity_m3 = EXCLUDED.capacity_m3,
                    availability = EXCLUDED.availability,
                    cost_per_km = EXCLUDED.cost_per_km,
                    driver = EXCLUDED.driver,
                    source_import_batch_id =
                        EXCLUDED.source_import_batch_id,
                    source_row_number = EXCLUDED.source_row_number,
                    updated_at = now()
                """,
                (batch_id,),
            )
            counts["vehicles"] = cursor.rowcount

            cursor.execute(
                """
                INSERT INTO core.sales_orders (
                    order_id,
                    order_date,
                    customer_id,
                    region,
                    order_status,
                    expedition_date,
                    source_import_batch_id
                )
                SELECT DISTINCT
                    order_id,
                    order_date,
                    customer_id,
                    region,
                    order_status,
                    expedition_date,
                    import_batch_id
                FROM stg.sales
                WHERE import_batch_id = %s
                """,
                (batch_id,),
            )
            counts["sales_orders"] = cursor.rowcount

            cursor.execute(
                """
                INSERT INTO core.sales_order_lines (
                    order_id,
                    line_number,
                    product_id,
                    quantity,
                    unit_price,
                    source_import_batch_id,
                    source_row_number
                )
                SELECT
                    order_id,
                    row_number() OVER (
                        PARTITION BY order_id
                        ORDER BY source_row_number
                    ),
                    product_id,
                    quantity,
                    unit_price,
                    import_batch_id,
                    source_row_number
                FROM stg.sales
                WHERE import_batch_id = %s
                ORDER BY order_id, source_row_number
                """,
                (batch_id,),
            )
            counts["sales_order_lines"] = cursor.rowcount

            cursor.execute(
                """
                INSERT INTO core.purchase_orders (
                    purchase_order_id,
                    order_date,
                    supplier_id,
                    source_import_batch_id
                )
                SELECT DISTINCT
                    p.purchase_order_id,
                    p.order_date,
                    s.id,
                    p.import_batch_id
                FROM stg.purchases p
                JOIN core.suppliers s
                  ON s.supplier_name = p.supplier
                WHERE p.import_batch_id = %s
                """,
                (batch_id,),
            )
            counts["purchase_orders"] = cursor.rowcount

            cursor.execute(
                """
                INSERT INTO core.purchase_order_lines (
                    purchase_order_id,
                    line_number,
                    delivery_date,
                    product_id,
                    ordered_quantity,
                    delivered_quantity,
                    purchase_price,
                    source_import_batch_id,
                    source_row_number
                )
                SELECT
                    purchase_order_id,
                    row_number() OVER (
                        PARTITION BY purchase_order_id
                        ORDER BY source_row_number
                    ),
                    delivery_date,
                    product_id,
                    ordered_quantity,
                    delivered_quantity,
                    purchase_price,
                    import_batch_id,
                    source_row_number
                FROM stg.purchases
                WHERE import_batch_id = %s
                ORDER BY purchase_order_id, source_row_number
                """,
                (batch_id,),
            )
            counts["purchase_order_lines"] = cursor.rowcount

            cursor.execute(
                """
                INSERT INTO core.inventory_snapshots (
                    snapshot_date,
                    source_import_batch_id
                )
                SELECT DISTINCT snapshot_date, import_batch_id
                FROM stg.inventory
                WHERE import_batch_id = %s
                """,
                (batch_id,),
            )
            counts["inventory_snapshots"] = cursor.rowcount

            cursor.execute(
                """
                INSERT INTO core.inventory_snapshot_lines (
                    snapshot_id,
                    product_id,
                    stock_actual,
                    stock_reserved,
                    stock_available,
                    warehouse_location,
                    min_stock,
                    max_stock,
                    source_import_batch_id,
                    source_row_number
                )
                SELECT
                    s.id,
                    i.product_id,
                    i.stock_actual,
                    i.stock_reserved,
                    i.stock_available,
                    i.warehouse_location,
                    i.min_stock,
                    i.max_stock,
                    i.import_batch_id,
                    i.source_row_number
                FROM stg.inventory i
                JOIN core.inventory_snapshots s
                  ON s.snapshot_date = i.snapshot_date
                WHERE i.import_batch_id = %s
                """,
                (batch_id,),
            )
            counts["inventory_snapshot_lines"] = cursor.rowcount

            cursor.execute(
                """
                INSERT INTO core.expeditions (
                    order_id,
                    received_at,
                    picked_at,
                    expedition_date,
                    delivery_type,
                    vehicle_id,
                    region,
                    weight_kg,
                    volume_m3,
                    source_import_batch_id,
                    source_row_number
                )
                SELECT
                    order_id,
                    received_at,
                    picked_at,
                    expedition_date,
                    delivery_type,
                    vehicle_id,
                    region,
                    weight_kg,
                    volume_m3,
                    import_batch_id,
                    source_row_number
                FROM stg.expedition
                WHERE import_batch_id = %s
                """,
                (batch_id,),
            )
            counts["expeditions"] = cursor.rowcount

            core_total = sum(counts.values())

            cursor.execute(
                """
                UPDATE audit.import_batches
                SET status = 'completed',
                    finished_at = now(),
                    error_count = 0,
                    row_count_core = %s,
                    metadata = metadata || %s
                WHERE id = %s
                """,
                (
                    core_total,
                    Jsonb(
                        {
                            "business_validation": {
                                "status": "passed",
                                "error_count": 0,
                            },
                            "core_row_counts": counts,
                        }
                    ),
                    batch_id,
                ),
            )
            connection.commit()

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
        print(f"CHYBA: core propagacia zlyhala: {exc}", file=sys.stderr)
        return 1
    finally:
        connection.close()

    print("BATCH_ALREADY_COMPLETED=NIE")
    print(f"IMPORT_BATCH_ID={batch_id}")
    print("BUSINESS_ERROR_COUNT=0")
    for table_name, count in counts.items():
        print(f"TABLE={table_name} CORE_ROWS={count}")
    print(f"CORE_ROW_COUNT={core_total}")
    print("IMPORT_BATCH_STATUS=completed")
    print("CORE_PROMOTION_OK=ANO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
