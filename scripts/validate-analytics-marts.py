"""Validate analytical mart row counts and reconciliations."""

from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path
import sys

import psycopg
from psycopg.rows import dict_row


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate analytical marts against core data."
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


def scalar(cursor, query: str):
    cursor.execute(query)
    row = cursor.fetchone()
    return next(iter(row.values()))


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

    failures: list[str] = []

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, status, started_at, finished_at, row_counts
                FROM mart.refresh_runs
                ORDER BY started_at DESC
                LIMIT 1
                """
            )
            latest_run = cursor.fetchone()

            if latest_run is None:
                failures.append("MART-E001 missing refresh run")
            elif latest_run["status"] != "completed":
                failures.append(
                    "MART-E002 latest refresh is not completed"
                )

            checks = (
                (
                    "sales_months",
                    """
                    SELECT count(DISTINCT date_trunc(
                        'month', order_date
                    ))
                    FROM core.sales_orders
                    """,
                    """
                    SELECT count(*) FROM mart.sales_monthly
                    """,
                ),
                (
                    "product_sales_months",
                    """
                    SELECT count(*)
                    FROM (
                        SELECT DISTINCT
                            date_trunc('month', o.order_date),
                            l.product_id
                        FROM core.sales_orders o
                        JOIN core.sales_order_lines l
                          ON l.order_id = o.order_id
                    ) expected
                    """,
                    """
                    SELECT count(*)
                    FROM mart.product_sales_monthly
                    """,
                ),
                (
                    "inventory_months",
                    """
                    SELECT count(*)
                    FROM core.inventory_snapshots
                    """,
                    """
                    SELECT count(*)
                    FROM mart.inventory_health_monthly
                    """,
                ),
                (
                    "procurement_supplier_months",
                    """
                    SELECT count(*)
                    FROM (
                        SELECT DISTINCT
                            date_trunc('month', o.order_date),
                            o.supplier_id
                        FROM core.purchase_orders o
                    ) expected
                    """,
                    """
                    SELECT count(*)
                    FROM mart.procurement_supplier_monthly
                    """,
                ),
                (
                    "expedition_months",
                    """
                    SELECT count(DISTINCT date_trunc(
                        'month', expedition_date
                    ))
                    FROM core.expeditions
                    """,
                    """
                    SELECT count(*)
                    FROM mart.expedition_monthly
                    """,
                ),
                (
                    "vehicle_months",
                    """
                    SELECT
                        count(DISTINCT date_trunc(
                            'month', expedition_date
                        ))
                        * (SELECT count(*) FROM core.vehicles)
                    FROM core.expeditions
                    """,
                    """
                    SELECT count(*)
                    FROM mart.vehicle_utilization_monthly
                    """,
                ),
            )

            for name, expected_query, actual_query in checks:
                expected = scalar(cursor, expected_query)
                actual = scalar(cursor, actual_query)
                print(
                    f"CHECK={name} EXPECTED={expected} ACTUAL={actual}"
                )
                if expected != actual:
                    failures.append(
                        f"MART-E010 {name}: {actual} != {expected}"
                    )

            core_revenue = scalar(
                cursor,
                """
                SELECT round(sum(quantity * unit_price), 2)
                FROM core.sales_order_lines
                """,
            )
            sales_revenue = scalar(
                cursor,
                """
                SELECT round(sum(revenue), 2)
                FROM mart.sales_monthly
                """,
            )
            product_revenue = scalar(
                cursor,
                """
                SELECT round(sum(revenue), 2)
                FROM mart.product_sales_monthly
                """,
            )

            print(f"CORE_REVENUE={core_revenue}")
            print(f"SALES_MART_REVENUE={sales_revenue}")
            print(f"PRODUCT_MART_REVENUE={product_revenue}")

            if core_revenue != sales_revenue:
                failures.append("MART-E020 sales revenue mismatch")
            if core_revenue != product_revenue:
                failures.append("MART-E021 product revenue mismatch")

            gross_profit = scalar(
                cursor,
                """
                SELECT round(sum(gross_profit), 2)
                FROM mart.sales_monthly
                """,
            )
            estimated_cost = scalar(
                cursor,
                """
                SELECT round(sum(estimated_cost), 2)
                FROM mart.sales_monthly
                """,
            )

            print(f"ESTIMATED_COST={estimated_cost}")
            print(f"GROSS_PROFIT={gross_profit}")

            if (
                core_revenue is not None
                and estimated_cost is not None
                and gross_profit is not None
                and core_revenue - estimated_cost != gross_profit
            ):
                failures.append("MART-E022 gross profit mismatch")

            procurement_delivered = scalar(
                cursor,
                """
                SELECT sum(delivered_units)
                FROM mart.procurement_supplier_monthly
                """,
            )
            core_delivered = scalar(
                cursor,
                """
                SELECT sum(delivered_quantity)
                FROM core.purchase_order_lines
                """,
            )
            if procurement_delivered != core_delivered:
                failures.append(
                    "MART-E030 procurement delivered units mismatch"
                )

            expedition_count = scalar(
                cursor,
                """
                SELECT sum(expedition_count)
                FROM mart.expedition_monthly
                """,
            )
            core_expeditions = scalar(
                cursor,
                """
                SELECT count(*) FROM core.expeditions
                """,
            )
            if expedition_count != core_expeditions:
                failures.append("MART-E040 expedition count mismatch")

            fleet_trips = scalar(
                cursor,
                """
                SELECT sum(trip_count)
                FROM mart.vehicle_utilization_monthly
                """,
            )
            own_deliveries = scalar(
                cursor,
                """
                SELECT count(*)
                FROM core.expeditions
                WHERE delivery_type = 'vlastná'
                """,
            )
            if fleet_trips != own_deliveries:
                failures.append("MART-E041 fleet trip mismatch")

            invalid_percentages = scalar(
                cursor,
                """
                SELECT
                    (
                        SELECT count(*)
                        FROM mart.sales_monthly
                        WHERE historical_cost_coverage_pct
                              NOT BETWEEN 0 AND 100
                    )
                    +
                    (
                        SELECT count(*)
                        FROM mart.inventory_health_monthly
                        WHERE days_cover_coverage_pct
                              NOT BETWEEN 0 AND 100
                    )
                    +
                    (
                        SELECT count(*)
                        FROM mart.procurement_supplier_monthly
                        WHERE fill_rate_pct NOT BETWEEN 0 AND 100
                           OR within_standard_lead_time_pct
                              NOT BETWEEN 0 AND 100
                    )
                    +
                    (
                        SELECT count(*)
                        FROM mart.expedition_monthly
                        WHERE same_day_order_expedition_pct
                              NOT BETWEEN 0 AND 100
                    )
                """,
            )
            if invalid_percentages != 0:
                failures.append(
                    "MART-E050 percentage outside 0-100"
                )

            cursor.execute(
                """
                SELECT
                    count(*) AS months,
                    min(month_start) AS month_min,
                    max(month_start) AS month_max,
                    round(sum(revenue), 2) AS revenue,
                    round(sum(gross_profit), 2) AS gross_profit
                FROM mart.management_kpis_monthly
                """
            )
            management = cursor.fetchone()

            print(f"MANAGEMENT_MONTHS={management['months']}")
            print(f"MANAGEMENT_MIN={management['month_min']}")
            print(f"MANAGEMENT_MAX={management['month_max']}")
            print(f"MANAGEMENT_REVENUE={management['revenue']}")
            print(
                "MANAGEMENT_GROSS_PROFIT="
                f"{management['gross_profit']}"
            )

    finally:
        connection.close()

    print(f"MART_VALIDATION_ERROR_COUNT={len(failures)}")
    for failure in failures:
        print(f"ERROR={failure}")

    if failures:
        print("MART_VALID= NIE".replace(" ", ""))
        return 1

    print("MART_VALID=ANO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
