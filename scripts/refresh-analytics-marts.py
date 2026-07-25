"""Refresh all analytical mart tables from the core layer."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


LOCK_KEY = 73003007

MART_TABLES = (
    "sales_monthly",
    "product_sales_monthly",
    "inventory_health_monthly",
    "procurement_supplier_monthly",
    "expedition_monthly",
    "vehicle_utilization_monthly",
    "management_kpis_monthly",
)


SALES_MONTHLY_SQL = """
INSERT INTO mart.sales_monthly (
    month_start,
    revenue,
    estimated_cost,
    gross_profit,
    gross_margin_pct,
    units_sold,
    order_count,
    customer_count,
    average_order_value,
    sales_line_count,
    historical_cost_line_count,
    fallback_cost_line_count,
    historical_cost_coverage_pct
)
WITH priced_sales AS (
    SELECT
        date_trunc('month', o.order_date)::date AS month_start,
        o.order_id,
        o.customer_id,
        l.quantity,
        l.unit_price,
        coalesce(historical.purchase_price, p.purchase_price)
            AS unit_cost,
        historical.purchase_price IS NOT NULL
            AS has_historical_cost
    FROM core.sales_orders o
    JOIN core.sales_order_lines l
      ON l.order_id = o.order_id
    JOIN core.products p
      ON p.product_id = l.product_id
    LEFT JOIN LATERAL (
        SELECT pol.purchase_price
        FROM core.purchase_order_lines pol
        JOIN core.purchase_orders po
          ON po.purchase_order_id = pol.purchase_order_id
        WHERE pol.product_id = l.product_id
          AND po.order_date <= o.order_date
        ORDER BY
            po.order_date DESC,
            pol.delivery_date DESC,
            pol.id DESC
        LIMIT 1
    ) historical ON true
)
SELECT
    month_start,
    round(sum(quantity * unit_price), 2),
    round(sum(quantity * unit_cost), 2),
    round(sum(quantity * (unit_price - unit_cost)), 2),
    round(
        100.0
        * sum(quantity * (unit_price - unit_cost))
        / nullif(sum(quantity * unit_price), 0),
        4
    ),
    sum(quantity)::bigint,
    count(DISTINCT order_id)::integer,
    count(DISTINCT customer_id)::integer,
    round(
        sum(quantity * unit_price)
        / nullif(count(DISTINCT order_id), 0),
        2
    ),
    count(*)::integer,
    count(*) FILTER (WHERE has_historical_cost)::integer,
    count(*) FILTER (WHERE NOT has_historical_cost)::integer,
    round(
        100.0
        * count(*) FILTER (WHERE has_historical_cost)
        / nullif(count(*), 0),
        4
    )
FROM priced_sales
GROUP BY month_start
ORDER BY month_start
"""


PRODUCT_SALES_MONTHLY_SQL = """
INSERT INTO mart.product_sales_monthly (
    month_start,
    product_id,
    product_name,
    category,
    supplier_name,
    units_sold,
    order_count,
    customer_count,
    revenue,
    estimated_cost,
    gross_profit,
    gross_margin_pct,
    average_unit_price,
    historical_cost_coverage_pct
)
WITH priced_sales AS (
    SELECT
        date_trunc('month', o.order_date)::date AS month_start,
        o.order_id,
        o.customer_id,
        l.product_id,
        p.product_name,
        p.category,
        s.supplier_name,
        l.quantity,
        l.unit_price,
        coalesce(historical.purchase_price, p.purchase_price)
            AS unit_cost,
        historical.purchase_price IS NOT NULL
            AS has_historical_cost
    FROM core.sales_orders o
    JOIN core.sales_order_lines l
      ON l.order_id = o.order_id
    JOIN core.products p
      ON p.product_id = l.product_id
    JOIN core.suppliers s
      ON s.id = p.supplier_id
    LEFT JOIN LATERAL (
        SELECT pol.purchase_price
        FROM core.purchase_order_lines pol
        JOIN core.purchase_orders po
          ON po.purchase_order_id = pol.purchase_order_id
        WHERE pol.product_id = l.product_id
          AND po.order_date <= o.order_date
        ORDER BY
            po.order_date DESC,
            pol.delivery_date DESC,
            pol.id DESC
        LIMIT 1
    ) historical ON true
)
SELECT
    month_start,
    product_id,
    product_name,
    category,
    supplier_name,
    sum(quantity)::bigint,
    count(DISTINCT order_id)::integer,
    count(DISTINCT customer_id)::integer,
    round(sum(quantity * unit_price), 2),
    round(sum(quantity * unit_cost), 2),
    round(sum(quantity * (unit_price - unit_cost)), 2),
    round(
        100.0
        * sum(quantity * (unit_price - unit_cost))
        / nullif(sum(quantity * unit_price), 0),
        4
    ),
    round(
        sum(quantity * unit_price)
        / nullif(sum(quantity), 0),
        4
    ),
    round(
        100.0
        * count(*) FILTER (WHERE has_historical_cost)
        / nullif(count(*), 0),
        4
    )
FROM priced_sales
GROUP BY
    month_start,
    product_id,
    product_name,
    category,
    supplier_name
ORDER BY month_start, product_id
"""


INVENTORY_HEALTH_MONTHLY_SQL = """
INSERT INTO mart.inventory_health_monthly (
    month_start,
    product_count,
    stockout_products,
    below_min_products,
    above_max_products,
    healthy_products,
    inventory_cost_value,
    inventory_sales_value,
    inventory_potential_margin_value,
    products_with_month_sales,
    products_without_month_sales,
    historical_cost_product_count,
    fallback_cost_product_count,
    days_cover_coverage_pct,
    average_days_of_cover,
    median_days_of_cover
)
WITH monthly_sales AS (
    SELECT
        date_trunc('month', o.order_date)::date AS month_start,
        l.product_id,
        sum(l.quantity)::numeric AS units_sold
    FROM core.sales_orders o
    JOIN core.sales_order_lines l
      ON l.order_id = o.order_id
    GROUP BY
        date_trunc('month', o.order_date),
        l.product_id
),
inventory_detail AS (
    SELECT
        snapshots.snapshot_date AS month_start,
        lines.product_id,
        lines.stock_available,
        lines.min_stock,
        lines.max_stock,
        p.sales_price,
        coalesce(historical.purchase_price, p.purchase_price)
            AS unit_cost,
        historical.purchase_price IS NOT NULL
            AS has_historical_cost,
        monthly_sales.units_sold,
        CASE
            WHEN monthly_sales.units_sold > 0
            THEN lines.stock_available
                 / (monthly_sales.units_sold / 30.4375)
        END AS days_of_cover
    FROM core.inventory_snapshots snapshots
    JOIN core.inventory_snapshot_lines lines
      ON lines.snapshot_id = snapshots.id
    JOIN core.products p
      ON p.product_id = lines.product_id
    LEFT JOIN monthly_sales
      ON monthly_sales.month_start = snapshots.snapshot_date
     AND monthly_sales.product_id = lines.product_id
    LEFT JOIN LATERAL (
        SELECT pol.purchase_price
        FROM core.purchase_order_lines pol
        JOIN core.purchase_orders po
          ON po.purchase_order_id = pol.purchase_order_id
        WHERE pol.product_id = lines.product_id
          AND po.order_date <= snapshots.snapshot_date
        ORDER BY
            po.order_date DESC,
            pol.delivery_date DESC,
            pol.id DESC
        LIMIT 1
    ) historical ON true
)
SELECT
    month_start,
    count(*)::integer,
    count(*) FILTER (
        WHERE stock_available = 0
    )::integer,
    count(*) FILTER (
        WHERE stock_available < min_stock
    )::integer,
    count(*) FILTER (
        WHERE stock_available > max_stock
    )::integer,
    count(*) FILTER (
        WHERE stock_available BETWEEN min_stock AND max_stock
    )::integer,
    round(sum(stock_available * unit_cost), 2),
    round(sum(stock_available * sales_price), 2),
    round(sum(stock_available * (sales_price - unit_cost)), 2),
    count(*) FILTER (
        WHERE units_sold > 0
    )::integer,
    count(*) FILTER (
        WHERE coalesce(units_sold, 0) = 0
    )::integer,
    count(*) FILTER (
        WHERE has_historical_cost
    )::integer,
    count(*) FILTER (
        WHERE NOT has_historical_cost
    )::integer,
    round(
        100.0
        * count(*) FILTER (WHERE units_sold > 0)
        / nullif(count(*), 0),
        4
    ),
    round(avg(days_of_cover), 2),
    round(
        percentile_cont(0.5) WITHIN GROUP (
            ORDER BY days_of_cover
        )::numeric,
        2
    )
FROM inventory_detail
GROUP BY month_start
ORDER BY month_start
"""


PROCUREMENT_SUPPLIER_MONTHLY_SQL = """
INSERT INTO mart.procurement_supplier_monthly (
    month_start,
    supplier_id,
    supplier_name,
    purchase_order_count,
    purchase_line_count,
    ordered_units,
    delivered_units,
    undelivered_units,
    ordered_value,
    delivered_value,
    fill_rate_pct,
    average_actual_lead_time_days,
    average_standard_lead_time_days,
    within_standard_lead_time_pct,
    late_line_count
)
SELECT
    date_trunc('month', o.order_date)::date AS month_start,
    s.id,
    s.supplier_name,
    count(DISTINCT o.purchase_order_id)::integer,
    count(*)::integer,
    sum(l.ordered_quantity)::bigint,
    sum(l.delivered_quantity)::bigint,
    sum(l.ordered_quantity - l.delivered_quantity)::bigint,
    round(sum(l.ordered_quantity * l.purchase_price), 2),
    round(sum(l.delivered_quantity * l.purchase_price), 2),
    round(
        100.0
        * sum(l.delivered_quantity)
        / nullif(sum(l.ordered_quantity), 0),
        4
    ),
    round(avg(l.delivery_date - o.order_date), 2),
    round(avg(p.lead_time_days), 2),
    round(
        100.0
        * count(*) FILTER (
            WHERE l.delivery_date - o.order_date
                  <= p.lead_time_days
        )
        / nullif(count(*), 0),
        4
    ),
    count(*) FILTER (
        WHERE l.delivery_date - o.order_date
              > p.lead_time_days
    )::integer
FROM core.purchase_orders o
JOIN core.purchase_order_lines l
  ON l.purchase_order_id = o.purchase_order_id
JOIN core.suppliers s
  ON s.id = o.supplier_id
JOIN core.products p
  ON p.product_id = l.product_id
GROUP BY
    date_trunc('month', o.order_date),
    s.id,
    s.supplier_name
ORDER BY month_start, s.id
"""


EXPEDITION_MONTHLY_SQL = """
INSERT INTO mart.expedition_monthly (
    month_start,
    expedition_count,
    own_delivery_expeditions,
    external_delivery_expeditions,
    pickup_expeditions,
    total_weight_kg,
    total_volume_m3,
    average_picking_hours,
    median_picking_hours,
    average_order_to_expedition_days,
    same_day_order_expedition_pct
)
SELECT
    date_trunc('month', e.expedition_date)::date,
    count(*)::integer,
    count(*) FILTER (
        WHERE e.delivery_type = 'vlastná'
    )::integer,
    count(*) FILTER (
        WHERE e.delivery_type = 'externá'
    )::integer,
    count(*) FILTER (
        WHERE e.delivery_type = 'osobný odber'
    )::integer,
    round(sum(e.weight_kg), 2),
    round(sum(e.volume_m3), 3),
    round(
        avg(
            extract(epoch FROM e.picked_at - e.received_at)
            / 3600.0
        )::numeric,
        2
    ),
    round(
        percentile_cont(0.5) WITHIN GROUP (
            ORDER BY
                extract(epoch FROM e.picked_at - e.received_at)
                / 3600.0
        )::numeric,
        2
    ),
    round(avg(e.expedition_date - o.order_date), 2),
    round(
        100.0
        * count(*) FILTER (
            WHERE e.expedition_date = o.order_date
        )
        / nullif(count(*), 0),
        4
    )
FROM core.expeditions e
JOIN core.sales_orders o
  ON o.order_id = e.order_id
GROUP BY date_trunc('month', e.expedition_date)
ORDER BY date_trunc('month', e.expedition_date)
"""


VEHICLE_UTILIZATION_MONTHLY_SQL = """
INSERT INTO mart.vehicle_utilization_monthly (
    month_start,
    vehicle_id,
    driver,
    capacity_kg,
    capacity_m3,
    trip_count,
    active_day_count,
    transported_weight_kg,
    transported_volume_m3,
    average_weight_utilization_pct,
    average_volume_utilization_pct,
    maximum_weight_utilization_pct,
    maximum_volume_utilization_pct,
    overloaded_trips
)
WITH months AS (
    SELECT DISTINCT
        date_trunc('month', expedition_date)::date AS month_start
    FROM core.expeditions
)
SELECT
    months.month_start,
    v.vehicle_id,
    v.driver,
    v.capacity_kg,
    v.capacity_m3,
    count(e.order_id)::integer,
    count(DISTINCT e.expedition_date)::integer,
    round(coalesce(sum(e.weight_kg), 0), 2),
    round(coalesce(sum(e.volume_m3), 0), 3),
    round(
        (
            100.0
            * avg(e.weight_kg / nullif(v.capacity_kg, 0))
        )::numeric,
        4
    ),
    round(
        (
            100.0
            * avg(e.volume_m3 / nullif(v.capacity_m3, 0))
        )::numeric,
        4
    ),
    round(
        (
            100.0
            * max(e.weight_kg / nullif(v.capacity_kg, 0))
        )::numeric,
        4
    ),
    round(
        (
            100.0
            * max(e.volume_m3 / nullif(v.capacity_m3, 0))
        )::numeric,
        4
    ),
    count(e.order_id) FILTER (
        WHERE e.weight_kg > v.capacity_kg
           OR e.volume_m3 > v.capacity_m3
    )::integer
FROM months
CROSS JOIN core.vehicles v
LEFT JOIN core.expeditions e
  ON date_trunc('month', e.expedition_date)::date
     = months.month_start
 AND e.vehicle_id = v.vehicle_id
 AND e.delivery_type = 'vlastná'
GROUP BY
    months.month_start,
    v.vehicle_id,
    v.driver,
    v.capacity_kg,
    v.capacity_m3
ORDER BY months.month_start, v.vehicle_id
"""


MANAGEMENT_KPIS_MONTHLY_SQL = """
INSERT INTO mart.management_kpis_monthly (
    month_start,
    revenue,
    estimated_cost,
    gross_profit,
    gross_margin_pct,
    units_sold,
    sales_order_count,
    customer_count,
    inventory_cost_value,
    stockout_products,
    below_min_products,
    above_max_products,
    average_days_of_cover,
    procurement_delivered_value,
    procurement_fill_rate_pct,
    average_procurement_lead_time_days,
    procurement_within_standard_pct,
    expedition_count,
    own_delivery_expeditions,
    external_delivery_expeditions,
    pickup_expeditions,
    average_picking_hours,
    own_fleet_trip_count,
    average_vehicle_weight_utilization_pct,
    average_vehicle_volume_utilization_pct
)
WITH all_months AS (
    SELECT month_start FROM mart.sales_monthly
    UNION
    SELECT month_start FROM mart.inventory_health_monthly
    UNION
    SELECT month_start FROM mart.procurement_supplier_monthly
    UNION
    SELECT month_start FROM mart.expedition_monthly
),
bounds AS (
    SELECT min(month_start) AS min_month, max(month_start) AS max_month
    FROM all_months
),
months AS (
    SELECT generate_series(
        min_month,
        max_month,
        interval '1 month'
    )::date AS month_start
    FROM bounds
),
procurement AS (
    SELECT
        month_start,
        sum(delivered_value) AS delivered_value,
        100.0
        * sum(delivered_units)
        / nullif(sum(ordered_units), 0) AS fill_rate_pct,
        sum(
            average_actual_lead_time_days
            * purchase_line_count
        )
        / nullif(sum(purchase_line_count), 0)
            AS average_actual_lead_time_days,
        100.0
        * sum(
            purchase_line_count - late_line_count
        )
        / nullif(sum(purchase_line_count), 0)
            AS within_standard_pct
    FROM mart.procurement_supplier_monthly
    GROUP BY month_start
),
vehicles AS (
    SELECT
        month_start,
        sum(trip_count)::integer AS trip_count,
        sum(
            average_weight_utilization_pct * trip_count
        )
        / nullif(sum(trip_count), 0)
            AS average_weight_utilization_pct,
        sum(
            average_volume_utilization_pct * trip_count
        )
        / nullif(sum(trip_count), 0)
            AS average_volume_utilization_pct
    FROM mart.vehicle_utilization_monthly
    GROUP BY month_start
)
SELECT
    months.month_start,
    sales.revenue,
    sales.estimated_cost,
    sales.gross_profit,
    sales.gross_margin_pct,
    sales.units_sold,
    sales.order_count,
    sales.customer_count,
    inventory.inventory_cost_value,
    inventory.stockout_products,
    inventory.below_min_products,
    inventory.above_max_products,
    inventory.average_days_of_cover,
    round(procurement.delivered_value, 2),
    round(procurement.fill_rate_pct, 4),
    round(procurement.average_actual_lead_time_days, 2),
    round(procurement.within_standard_pct, 4),
    expedition.expedition_count,
    expedition.own_delivery_expeditions,
    expedition.external_delivery_expeditions,
    expedition.pickup_expeditions,
    expedition.average_picking_hours,
    vehicles.trip_count,
    round(vehicles.average_weight_utilization_pct, 4),
    round(vehicles.average_volume_utilization_pct, 4)
FROM months
LEFT JOIN mart.sales_monthly sales
  ON sales.month_start = months.month_start
LEFT JOIN mart.inventory_health_monthly inventory
  ON inventory.month_start = months.month_start
LEFT JOIN procurement
  ON procurement.month_start = months.month_start
LEFT JOIN mart.expedition_monthly expedition
  ON expedition.month_start = months.month_start
LEFT JOIN vehicles
  ON vehicles.month_start = months.month_start
ORDER BY months.month_start
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh all analytical marts from core tables."
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


def get_source_summary(cursor) -> dict[str, object]:
    cursor.execute(
        """
        SELECT jsonb_build_object(
            'sales_orders', (
                SELECT count(*) FROM core.sales_orders
            ),
            'sales_order_lines', (
                SELECT count(*) FROM core.sales_order_lines
            ),
            'purchase_orders', (
                SELECT count(*) FROM core.purchase_orders
            ),
            'purchase_order_lines', (
                SELECT count(*) FROM core.purchase_order_lines
            ),
            'inventory_snapshots', (
                SELECT count(*) FROM core.inventory_snapshots
            ),
            'inventory_snapshot_lines', (
                SELECT count(*) FROM core.inventory_snapshot_lines
            ),
            'expeditions', (
                SELECT count(*) FROM core.expeditions
            ),
            'vehicles', (
                SELECT count(*) FROM core.vehicles
            ),
            'sales_min', (
                SELECT min(order_date) FROM core.sales_orders
            ),
            'sales_max', (
                SELECT max(order_date) FROM core.sales_orders
            ),
            'expedition_max', (
                SELECT max(expedition_date) FROM core.expeditions
            )
        ) AS summary
        """
    )
    return cursor.fetchone()["summary"]


def get_row_counts(cursor) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table_name in MART_TABLES:
        cursor.execute(
            f"SELECT count(*) AS count FROM mart.{table_name}"
        )
        counts[table_name] = int(cursor.fetchone()["count"])
    return counts


def validate_refresh(cursor) -> None:
    checks = (
        (
            "sales revenue",
            """
            SELECT
                (
                    SELECT round(sum(revenue), 2)
                    FROM mart.sales_monthly
                )
                =
                (
                    SELECT round(sum(l.quantity * l.unit_price), 2)
                    FROM core.sales_order_lines l
                ) AS valid
            """,
        ),
        (
            "product sales revenue",
            """
            SELECT
                (
                    SELECT round(sum(revenue), 2)
                    FROM mart.product_sales_monthly
                )
                =
                (
                    SELECT round(sum(revenue), 2)
                    FROM mart.sales_monthly
                ) AS valid
            """,
        ),
        (
            "sales gross profit formula",
            """
            SELECT count(*) = 0 AS valid
            FROM mart.sales_monthly
            WHERE gross_profit <> revenue - estimated_cost
            """,
        ),
        (
            "inventory month count",
            """
            SELECT
                (
                    SELECT count(*)
                    FROM mart.inventory_health_monthly
                )
                =
                (
                    SELECT count(*)
                    FROM core.inventory_snapshots
                ) AS valid
            """,
        ),
        (
            "procurement delivered units",
            """
            SELECT
                (
                    SELECT sum(delivered_units)
                    FROM mart.procurement_supplier_monthly
                )
                =
                (
                    SELECT sum(delivered_quantity)
                    FROM core.purchase_order_lines
                ) AS valid
            """,
        ),
        (
            "expedition count",
            """
            SELECT
                (
                    SELECT sum(expedition_count)
                    FROM mart.expedition_monthly
                )
                =
                (
                    SELECT count(*)
                    FROM core.expeditions
                ) AS valid
            """,
        ),
        (
            "vehicle trip count",
            """
            SELECT
                (
                    SELECT sum(trip_count)
                    FROM mart.vehicle_utilization_monthly
                )
                =
                (
                    SELECT count(*)
                    FROM core.expeditions
                    WHERE delivery_type = 'vlastná'
                ) AS valid
            """,
        ),
        (
            "management revenue reconciliation",
            """
            SELECT count(*) = 0 AS valid
            FROM mart.management_kpis_monthly management
            JOIN mart.sales_monthly sales
              ON sales.month_start = management.month_start
            WHERE management.revenue <> sales.revenue
            """,
        ),
    )

    failures: list[str] = []
    for check_name, query in checks:
        cursor.execute(query)
        if cursor.fetchone()["valid"] is not True:
            failures.append(check_name)

    if failures:
        raise RuntimeError(
            "Mart validation failed: " + ", ".join(failures)
        )


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
        autocommit=True,
    )

    refresh_run_id: uuid.UUID | None = None
    lock_acquired = False

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_lock(%s) AS acquired",
                (LOCK_KEY,),
            )
            lock_acquired = bool(cursor.fetchone()["acquired"])

            if not lock_acquired:
                print(
                    "CHYBA: iny mart refresh uz prebieha.",
                    file=sys.stderr,
                )
                return 2

            refresh_run_id = uuid.uuid4()
            source_summary = get_source_summary(cursor)

            cursor.execute(
                """
                INSERT INTO mart.refresh_runs (
                    id,
                    status,
                    source_summary
                )
                VALUES (%s, 'running', %s)
                """,
                (
                    refresh_run_id,
                    Jsonb(source_summary),
                ),
            )

        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    TRUNCATE TABLE
                        mart.management_kpis_monthly,
                        mart.vehicle_utilization_monthly,
                        mart.expedition_monthly,
                        mart.procurement_supplier_monthly,
                        mart.inventory_health_monthly,
                        mart.product_sales_monthly,
                        mart.sales_monthly
                    """
                )

                statements = (
                    ("sales_monthly", SALES_MONTHLY_SQL),
                    (
                        "product_sales_monthly",
                        PRODUCT_SALES_MONTHLY_SQL,
                    ),
                    (
                        "inventory_health_monthly",
                        INVENTORY_HEALTH_MONTHLY_SQL,
                    ),
                    (
                        "procurement_supplier_monthly",
                        PROCUREMENT_SUPPLIER_MONTHLY_SQL,
                    ),
                    (
                        "expedition_monthly",
                        EXPEDITION_MONTHLY_SQL,
                    ),
                    (
                        "vehicle_utilization_monthly",
                        VEHICLE_UTILIZATION_MONTHLY_SQL,
                    ),
                    (
                        "management_kpis_monthly",
                        MANAGEMENT_KPIS_MONTHLY_SQL,
                    ),
                )

                for table_name, statement in statements:
                    cursor.execute(statement)
                    print(
                        f"TABLE={table_name} "
                        f"REFRESHED_ROWS={cursor.rowcount}"
                    )

                validate_refresh(cursor)
                row_counts = get_row_counts(cursor)

                cursor.execute(
                    """
                    UPDATE mart.refresh_runs
                    SET status = 'completed',
                        finished_at = now(),
                        row_counts = %s
                    WHERE id = %s
                    """,
                    (
                        Jsonb(row_counts),
                        refresh_run_id,
                    ),
                )

        print(f"REFRESH_RUN_ID={refresh_run_id}")
        for table_name, row_count in row_counts.items():
            print(f"TABLE={table_name} ROW_COUNT={row_count}")
        print("MART_REFRESH_STATUS=completed")
        print("MART_REFRESH_OK=ANO")
        return 0

    except Exception as exc:
        if refresh_run_id is not None:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE mart.refresh_runs
                        SET status = 'failed',
                            finished_at = now(),
                            error_message = %s
                        WHERE id = %s
                        """,
                        (
                            str(exc)[:4000],
                            refresh_run_id,
                        ),
                    )
            except Exception:
                pass

        print(
            f"CHYBA: mart refresh zlyhal: {exc}",
            file=sys.stderr,
        )
        return 1

    finally:
        if lock_acquired:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_unlock(%s)",
                        (LOCK_KEY,),
                    )
            except Exception:
                pass
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
