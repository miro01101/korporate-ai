"""Read-only analytical API endpoints backed by mart tables."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import bindparam, text

from app.db import engine


router = APIRouter(
    prefix="/api/v1/analytics",
    tags=["analytics"],
)


def json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {
            key: json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def row_to_dict(row: Any) -> dict[str, Any]:
    return {
        key: json_value(value)
        for key, value in row.items()
    }


def fetch_all(
    query: str,
    parameters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(query),
            parameters or {},
        ).mappings().all()

    return [row_to_dict(row) for row in rows]


def fetch_one(
    query: str,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    with engine.connect() as connection:
        row = connection.execute(
            text(query),
            parameters or {},
        ).mappings().one_or_none()

    return row_to_dict(row) if row is not None else None


def normalize_month(value: date | None) -> date | None:
    if value is None:
        return None
    return value.replace(day=1)


def resolve_range(
    table_name: str,
    date_from: date | None,
    date_to: date | None,
) -> tuple[date, date]:
    allowed_tables = {
        "management_kpis_monthly",
        "product_sales_monthly",
        "inventory_health_monthly",
        "procurement_supplier_monthly",
        "expedition_monthly",
        "vehicle_utilization_monthly",
    }

    if table_name not in allowed_tables:
        raise RuntimeError("Unsupported analytics table.")

    bounds = fetch_one(
        f"""
        SELECT
            min(month_start) AS month_min,
            max(month_start) AS month_max
        FROM mart.{table_name}
        """
    )

    if (
        bounds is None
        or bounds["month_min"] is None
        or bounds["month_max"] is None
    ):
        raise HTTPException(
            status_code=503,
            detail="Analytical mart is empty.",
        )

    resolved_from = (
        normalize_month(date_from)
        or date.fromisoformat(bounds["month_min"])
    )
    resolved_to = (
        normalize_month(date_to)
        or date.fromisoformat(bounds["month_max"])
    )

    if resolved_from > resolved_to:
        raise HTTPException(
            status_code=422,
            detail="date_from must not be after date_to.",
        )

    return resolved_from, resolved_to


def percentage_change(
    current: float | int | None,
    previous: float | int | None,
) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round(
        100.0 * (float(current) - float(previous))
        / float(previous),
        2,
    )


@router.get("/status")
def analytics_status() -> dict[str, Any]:
    latest_refresh = fetch_one(
        """
        SELECT
            id,
            status,
            started_at,
            finished_at,
            row_counts,
            source_summary,
            error_message
        FROM mart.refresh_runs
        ORDER BY started_at DESC
        LIMIT 1
        """
    )

    table_counts = fetch_one(
        """
        SELECT
            (SELECT count(*) FROM mart.sales_monthly)
                AS sales_monthly,
            (SELECT count(*) FROM mart.product_sales_monthly)
                AS product_sales_monthly,
            (SELECT count(*) FROM mart.inventory_health_monthly)
                AS inventory_health_monthly,
            (
                SELECT count(*)
                FROM mart.procurement_supplier_monthly
            ) AS procurement_supplier_monthly,
            (SELECT count(*) FROM mart.expedition_monthly)
                AS expedition_monthly,
            (
                SELECT count(*)
                FROM mart.vehicle_utilization_monthly
            ) AS vehicle_utilization_monthly,
            (SELECT count(*) FROM mart.management_kpis_monthly)
                AS management_kpis_monthly
        """
    )

    coverage = fetch_one(
        """
        SELECT
            min(month_start) AS month_min,
            max(month_start) AS month_max,
            min(month_start) FILTER (
                WHERE revenue IS NOT NULL
            ) AS sales_month_min,
            max(month_start) FILTER (
                WHERE revenue IS NOT NULL
            ) AS sales_month_max
        FROM mart.management_kpis_monthly
        """
    )

    ready = (
        latest_refresh is not None
        and latest_refresh["status"] == "completed"
    )

    return {
        "status": "ready" if ready else "not_ready",
        "latest_refresh": latest_refresh,
        "table_counts": table_counts,
        "coverage": coverage,
    }


@router.get("/summary")
def analytics_summary(
    month: date | None = Query(default=None),
) -> dict[str, Any]:
    selected_month = normalize_month(month)

    if selected_month is None:
        latest = fetch_one(
            """
            SELECT max(month_start) AS month_start
            FROM mart.management_kpis_monthly
            WHERE revenue IS NOT NULL
            """
        )
        if latest is None or latest["month_start"] is None:
            raise HTTPException(
                status_code=503,
                detail="No sales month is available.",
            )
        selected_month = date.fromisoformat(
            latest["month_start"]
        )

    current = fetch_one(
        """
        SELECT *
        FROM mart.management_kpis_monthly
        WHERE month_start = :month_start
        """,
        {"month_start": selected_month},
    )

    if current is None:
        raise HTTPException(
            status_code=404,
            detail="Selected month is not available.",
        )

    previous_month = fetch_one(
        """
        SELECT *
        FROM mart.management_kpis_monthly
        WHERE month_start = (
            CAST(:month_start AS date) - interval '1 month'
        )::date
        """,
        {"month_start": selected_month},
    )

    previous_year = fetch_one(
        """
        SELECT *
        FROM mart.management_kpis_monthly
        WHERE month_start = (
            CAST(:month_start AS date) - interval '1 year'
        )::date
        """,
        {"month_start": selected_month},
    )

    comparison_fields = (
        "revenue",
        "gross_profit",
        "units_sold",
        "sales_order_count",
        "customer_count",
        "inventory_cost_value",
        "expedition_count",
    )

    month_over_month = {
        field: percentage_change(
            current.get(field),
            (
                previous_month.get(field)
                if previous_month is not None
                else None
            ),
        )
        for field in comparison_fields
    }

    year_over_year = {
        field: percentage_change(
            current.get(field),
            (
                previous_year.get(field)
                if previous_year is not None
                else None
            ),
        )
        for field in comparison_fields
    }

    return {
        "selected_month": selected_month.isoformat(),
        "current": current,
        "previous_month": previous_month,
        "previous_year": previous_year,
        "month_over_month_pct": month_over_month,
        "year_over_year_pct": year_over_year,
    }


@router.get("/monthly")
def analytics_monthly(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
) -> dict[str, Any]:
    resolved_from, resolved_to = resolve_range(
        "management_kpis_monthly",
        date_from,
        date_to,
    )

    rows = fetch_all(
        """
        SELECT *
        FROM mart.management_kpis_monthly
        WHERE month_start BETWEEN :date_from AND :date_to
        ORDER BY month_start
        """,
        {
            "date_from": resolved_from,
            "date_to": resolved_to,
        },
    )

    return {
        "date_from": resolved_from.isoformat(),
        "date_to": resolved_to.isoformat(),
        "count": len(rows),
        "items": rows,
    }


@router.get("/sales/products")
def analytics_sales_products(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    category: str | None = Query(default=None),
    supplier: str | None = Query(default=None),
    sort_by: str = Query(default="revenue"),
    direction: str = Query(default="desc"),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    resolved_from, resolved_to = resolve_range(
        "product_sales_monthly",
        date_from,
        date_to,
    )

    sort_columns = {
        "revenue": "revenue",
        "gross_profit": "gross_profit",
        "gross_margin_pct": "gross_margin_pct",
        "units_sold": "units_sold",
        "order_count": "order_count",
    }

    sort_column = sort_columns.get(sort_by)
    if sort_column is None:
        raise HTTPException(
            status_code=422,
            detail="Unsupported sort_by value.",
        )

    sort_direction = direction.lower()
    if sort_direction not in {"asc", "desc"}:
        raise HTTPException(
            status_code=422,
            detail="direction must be asc or desc.",
        )

    query = f"""
        SELECT
            product_id,
            product_name,
            category,
            supplier_name,
            sum(units_sold)::bigint AS units_sold,
            sum(order_count)::integer AS order_count,
            sum(customer_count)::integer AS customer_count,
            round(sum(revenue), 2) AS revenue,
            round(sum(estimated_cost), 2) AS estimated_cost,
            round(sum(gross_profit), 2) AS gross_profit,
            round(
                100.0 * sum(gross_profit)
                / nullif(sum(revenue), 0),
                4
            ) AS gross_margin_pct,
            round(
                sum(revenue) / nullif(sum(units_sold), 0),
                4
            ) AS average_unit_price
        FROM mart.product_sales_monthly
        WHERE month_start BETWEEN :date_from AND :date_to
          AND (
              CAST(:category AS text) IS NULL
              OR category = CAST(:category AS text)
          )
          AND (
              CAST(:supplier AS text) IS NULL
              OR supplier_name = CAST(:supplier AS text)
          )
        GROUP BY
            product_id,
            product_name,
            category,
            supplier_name
        ORDER BY {sort_column} {sort_direction}, product_id
        LIMIT :limit
    """

    rows = fetch_all(
        query,
        {
            "date_from": resolved_from,
            "date_to": resolved_to,
            "category": category,
            "supplier": supplier,
            "limit": limit,
        },
    )

    filters = fetch_one(
        """
        SELECT
            array_agg(DISTINCT category ORDER BY category)
                AS categories,
            array_agg(
                DISTINCT supplier_name
                ORDER BY supplier_name
            ) AS suppliers
        FROM mart.product_sales_monthly
        WHERE month_start BETWEEN :date_from AND :date_to
        """,
        {
            "date_from": resolved_from,
            "date_to": resolved_to,
        },
    )

    return {
        "date_from": resolved_from.isoformat(),
        "date_to": resolved_to.isoformat(),
        "sort_by": sort_by,
        "direction": sort_direction,
        "count": len(rows),
        "available_filters": filters,
        "items": rows,
    }


@router.get("/inventory")
def analytics_inventory(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
) -> dict[str, Any]:
    resolved_from, resolved_to = resolve_range(
        "inventory_health_monthly",
        date_from,
        date_to,
    )

    rows = fetch_all(
        """
        SELECT *
        FROM mart.inventory_health_monthly
        WHERE month_start BETWEEN :date_from AND :date_to
        ORDER BY month_start
        """,
        {
            "date_from": resolved_from,
            "date_to": resolved_to,
        },
    )

    return {
        "date_from": resolved_from.isoformat(),
        "date_to": resolved_to.isoformat(),
        "count": len(rows),
        "items": rows,
    }


@router.get("/procurement/suppliers")
def analytics_procurement_suppliers(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    resolved_from, resolved_to = resolve_range(
        "procurement_supplier_monthly",
        date_from,
        date_to,
    )

    rows = fetch_all(
        """
        SELECT
            supplier_id,
            supplier_name,
            sum(purchase_order_count)::integer
                AS purchase_order_count,
            sum(purchase_line_count)::integer
                AS purchase_line_count,
            sum(ordered_units)::bigint AS ordered_units,
            sum(delivered_units)::bigint AS delivered_units,
            sum(undelivered_units)::bigint AS undelivered_units,
            round(sum(ordered_value), 2) AS ordered_value,
            round(sum(delivered_value), 2) AS delivered_value,
            round(
                100.0 * sum(delivered_units)
                / nullif(sum(ordered_units), 0),
                4
            ) AS fill_rate_pct,
            round(
                sum(
                    average_actual_lead_time_days
                    * purchase_line_count
                )
                / nullif(sum(purchase_line_count), 0),
                2
            ) AS average_actual_lead_time_days,
            round(
                sum(
                    average_standard_lead_time_days
                    * purchase_line_count
                )
                / nullif(sum(purchase_line_count), 0),
                2
            ) AS average_standard_lead_time_days,
            round(
                100.0
                * sum(purchase_line_count - late_line_count)
                / nullif(sum(purchase_line_count), 0),
                4
            ) AS within_standard_lead_time_pct,
            sum(late_line_count)::integer AS late_line_count
        FROM mart.procurement_supplier_monthly
        WHERE month_start BETWEEN :date_from AND :date_to
        GROUP BY supplier_id, supplier_name
        ORDER BY delivered_value DESC, supplier_name
        LIMIT :limit
        """,
        {
            "date_from": resolved_from,
            "date_to": resolved_to,
            "limit": limit,
        },
    )

    return {
        "date_from": resolved_from.isoformat(),
        "date_to": resolved_to.isoformat(),
        "count": len(rows),
        "items": rows,
    }


@router.get("/expeditions")
def analytics_expeditions(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
) -> dict[str, Any]:
    resolved_from, resolved_to = resolve_range(
        "expedition_monthly",
        date_from,
        date_to,
    )

    rows = fetch_all(
        """
        SELECT *
        FROM mart.expedition_monthly
        WHERE month_start BETWEEN :date_from AND :date_to
        ORDER BY month_start
        """,
        {
            "date_from": resolved_from,
            "date_to": resolved_to,
        },
    )

    return {
        "date_from": resolved_from.isoformat(),
        "date_to": resolved_to.isoformat(),
        "count": len(rows),
        "items": rows,
    }


@router.get("/vehicles")
def analytics_vehicles(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
) -> dict[str, Any]:
    resolved_from, resolved_to = resolve_range(
        "vehicle_utilization_monthly",
        date_from,
        date_to,
    )

    rows = fetch_all(
        """
        SELECT
            vehicle_id,
            driver,
            max(capacity_kg)::integer AS capacity_kg,
            max(capacity_m3) AS capacity_m3,
            sum(trip_count)::integer AS trip_count,
            sum(active_day_count)::integer AS active_day_count,
            round(sum(transported_weight_kg), 2)
                AS transported_weight_kg,
            round(sum(transported_volume_m3), 3)
                AS transported_volume_m3,
            round(
                sum(
                    average_weight_utilization_pct
                    * trip_count
                )
                / nullif(sum(trip_count), 0),
                4
            ) AS average_weight_utilization_pct,
            round(
                sum(
                    average_volume_utilization_pct
                    * trip_count
                )
                / nullif(sum(trip_count), 0),
                4
            ) AS average_volume_utilization_pct,
            max(maximum_weight_utilization_pct)
                AS maximum_weight_utilization_pct,
            max(maximum_volume_utilization_pct)
                AS maximum_volume_utilization_pct,
            sum(overloaded_trips)::integer AS overloaded_trips
        FROM mart.vehicle_utilization_monthly
        WHERE month_start BETWEEN :date_from AND :date_to
        GROUP BY vehicle_id, driver
        ORDER BY trip_count DESC, vehicle_id
        """,
        {
            "date_from": resolved_from,
            "date_to": resolved_to,
        },
    )

    return {
        "date_from": resolved_from.isoformat(),
        "date_to": resolved_to.isoformat(),
        "count": len(rows),
        "items": rows,
    }
