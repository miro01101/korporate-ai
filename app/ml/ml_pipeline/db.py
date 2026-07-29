from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from typing import Any, Iterator

import pandas as pd
import psycopg
from psycopg import Connection

from ml_pipeline.config import DatabaseConfig


@contextmanager
def database_connection(
    config: DatabaseConfig,
) -> Iterator[Connection[Any]]:
    connection = psycopg.connect(
        host=config.host,
        port=config.port,
        dbname=config.database,
        user=config.user,
        password=config.read_password(),
        connect_timeout=10,
        application_name="korporate-ai-ml",
    )

    try:
        yield connection
    finally:
        connection.close()


def query_frame(
    connection: Connection[Any],
    query: str,
    parameters: Sequence[Any] | None = None,
) -> pd.DataFrame:
    with connection.cursor() as cursor:
        cursor.execute(query, parameters)

        rows = cursor.fetchall()
        columns = [
            description.name
            for description in cursor.description or ()
        ]

    return pd.DataFrame(rows, columns=columns)


def execute_many(
    connection: Connection[Any],
    query: str,
    rows: Iterable[Sequence[Any]],
) -> None:
    with connection.cursor() as cursor:
        cursor.executemany(query, rows)


def load_source_frames(
    connection: Connection[Any],
) -> dict[str, pd.DataFrame]:
    products = query_frame(
        connection,
        """
        SELECT
            product_id,
            product_name,
            category,
            supplier_id,
            purchase_price,
            sales_price,
            minimum_order_quantity,
            lead_time_days
        FROM core.products
        ORDER BY product_id
        """,
    )

    sales = query_frame(
        connection,
        """
        SELECT
            month_start,
            product_id,
            units_sold,
            order_count,
            customer_count,
            revenue,
            gross_profit
        FROM mart.product_sales_monthly
        ORDER BY product_id, month_start
        """,
    )

    inventory = query_frame(
        connection,
        """
        SELECT
            date_trunc(
                'month',
                snapshots.snapshot_date
            )::date AS month_start,
            lines.product_id,
            sum(lines.stock_actual)::bigint
                AS stock_actual,
            sum(lines.stock_reserved)::bigint
                AS stock_reserved,
            sum(lines.stock_available)::bigint
                AS stock_available,
            sum(lines.min_stock)::bigint
                AS min_stock,
            sum(lines.max_stock)::bigint
                AS max_stock
        FROM core.inventory_snapshots AS snapshots
        JOIN core.inventory_snapshot_lines AS lines
          ON lines.snapshot_id = snapshots.id
        GROUP BY
            date_trunc(
                'month',
                snapshots.snapshot_date
            )::date,
            lines.product_id
        ORDER BY
            lines.product_id,
            month_start
        """,
    )

    purchases = query_frame(
        connection,
        """
        SELECT
            purchase_orders.order_date,
            purchase_lines.delivery_date,
            purchase_lines.product_id,
            purchase_lines.ordered_quantity,
            purchase_lines.delivered_quantity,
            purchase_lines.purchase_price
        FROM core.purchase_orders AS purchase_orders
        JOIN core.purchase_order_lines AS purchase_lines
          ON purchase_lines.purchase_order_id
           = purchase_orders.purchase_order_id
        ORDER BY
            purchase_lines.product_id,
            purchase_orders.order_date,
            purchase_lines.delivery_date
        """,
    )

    return {
        "products": products,
        "sales": sales,
        "inventory": inventory,
        "purchases": purchases,
    }
