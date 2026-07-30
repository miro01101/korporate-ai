from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import math
from typing import Any
from uuid import UUID

import numpy as np
import pandas as pd
from psycopg import Connection

from ml_pipeline.db import execute_many, query_frame


RISK_ENGINE_VERSION = "inventory-risk-v1"
EXPECTED_PRODUCT_COUNT = 80
FORECAST_HORIZONS = (1, 2, 3)
MAX_SUPPORTED_LEAD_TIME_DAYS = 30
PROBABILITY_METHOD = "quantile_piecewise_linear_proxy_v1"
CUMULATIVE_QUANTILE_METHOD = "sum_of_monthly_quantiles_v1"
INCOMING_POLICY = "open_purchase_order_lines_by_delivery_date_v1"


@dataclass(frozen=True)
class InventoryRiskSummary:
    model_run_id: UUID
    as_of_date: date
    product_count: int
    row_count: int
    incoming_product_count: int
    recommended_product_count: int
    total_incoming_quantity: int
    total_recommended_quantity: int


def quantile_cdf_proxy(value: float, q10: float, q50: float, q90: float) -> float:
    """Piecewise-linear CDF proxy anchored at calibrated P10/P50/P90."""

    x, low, median, high = map(float, (value, q10, q50, q90))
    values = np.asarray([x, low, median, high], dtype=float)

    if not np.isfinite(values).all():
        raise ValueError("CDF inputs must be finite.")
    if low < 0.0 or low > median or median > high:
        raise ValueError("Quantiles must satisfy 0 <= P10 <= P50 <= P90.")
    if x < 0.0:
        return 0.0

    spread = max(high - median, median - low, high, 1.0)
    raw_anchors = (
        (0.0, 0.0),
        (low, 0.10),
        (median, 0.50),
        (high, 0.90),
        (high + spread, 1.0),
    )

    # Equal quantiles represent point mass; retain the largest CDF anchor.
    merged: dict[float, float] = {}
    for anchor_x, probability in raw_anchors:
        merged[float(anchor_x)] = max(merged.get(float(anchor_x), 0.0), probability)

    ordered = sorted(merged.items())
    xp = np.asarray([item[0] for item in ordered], dtype=float)
    fp = np.asarray([item[1] for item in ordered], dtype=float)

    return float(np.clip(np.interp(x, xp, fp, left=0.0, right=1.0), 0.0, 1.0))


def stockout_probability_proxy(
    stock_position: float,
    q10: float,
    q50: float,
    q90: float,
) -> float:
    return float(
        np.clip(
            1.0 - quantile_cdf_proxy(stock_position, q10, q50, q90),
            0.0,
            1.0,
        )
    )


def round_up_to_moq(required_quantity: float, minimum_order_quantity: int) -> int:
    required = float(required_quantity)
    moq = int(minimum_order_quantity)

    if not math.isfinite(required):
        raise ValueError("Required quantity must be finite.")
    if moq <= 0:
        raise ValueError("Minimum order quantity must be positive.")
    if required <= 0.0:
        return 0

    return int(math.ceil(required / moq) * moq)


def _validate_sources(
    products: pd.DataFrame,
    forecasts: pd.DataFrame,
    purchases: pd.DataFrame,
    *,
    expected_product_count: int,
) -> None:
    required_products = {
        "product_id",
        "lead_time_days",
        "minimum_order_quantity",
        "stock_available",
        "max_stock",
        "snapshot_date",
    }
    required_forecasts = {
        "product_id",
        "forecast_month",
        "horizon",
        "forecast_p10",
        "forecast_p50",
        "forecast_p90",
    }
    required_purchases = {"product_id", "delivery_date", "outstanding_quantity"}

    for name, missing in (
        ("product", required_products - set(products.columns)),
        ("forecast", required_forecasts - set(forecasts.columns)),
        ("purchase", required_purchases - set(purchases.columns)),
    ):
        if missing:
            raise ValueError(f"Missing {name} columns: {', '.join(sorted(missing))}")

    if len(products) != expected_product_count:
        raise ValueError(
            f"Unexpected product count: {len(products)} != {expected_product_count}."
        )
    if products["product_id"].astype(str).nunique() != expected_product_count:
        raise ValueError("Product context must contain unique products.")
    if products["snapshot_date"].nunique() != 1:
        raise ValueError("Product context must use one snapshot date.")

    expected_forecasts = expected_product_count * len(FORECAST_HORIZONS)
    if len(forecasts) != expected_forecasts:
        raise ValueError(
            f"Unexpected forecast count: {len(forecasts)} != {expected_forecasts}."
        )
    if forecasts.duplicated(["product_id", "horizon"]).any():
        raise ValueError("Duplicate product-horizon forecasts.")

    expected_horizons = tuple(FORECAST_HORIZONS)
    horizon_sets = forecasts.groupby("product_id", observed=True)["horizon"].apply(
        lambda values: tuple(sorted(int(value) for value in values))
    )
    if not horizon_sets.apply(lambda value: value == expected_horizons).all():
        raise ValueError("Every product must have horizons 1, 2, 3.")

    product_ids = set(products["product_id"].astype(str))
    forecast_ids = set(forecasts["product_id"].astype(str))
    if product_ids != forecast_ids:
        raise ValueError("Product and forecast identifiers differ.")


def build_inventory_risk_frame(
    products: pd.DataFrame,
    forecasts: pd.DataFrame,
    purchases: pd.DataFrame,
    *,
    model_run_id: UUID,
    as_of_date: date,
    expected_product_count: int = EXPECTED_PRODUCT_COUNT,
) -> pd.DataFrame:
    products = products.copy()
    forecasts = forecasts.copy()
    purchases = purchases.copy()

    for frame in (products, forecasts, purchases):
        frame["product_id"] = frame["product_id"].astype(str)

    for column in (
        "lead_time_days",
        "minimum_order_quantity",
        "stock_available",
        "max_stock",
    ):
        products[column] = pd.to_numeric(products[column], errors="raise")

    for column in ("horizon", "forecast_p10", "forecast_p50", "forecast_p90"):
        forecasts[column] = pd.to_numeric(forecasts[column], errors="raise")

    purchases["outstanding_quantity"] = pd.to_numeric(
        purchases["outstanding_quantity"], errors="raise"
    )
    products["snapshot_date"] = pd.to_datetime(
        products["snapshot_date"], errors="raise"
    ).dt.date
    forecasts["forecast_month"] = pd.to_datetime(
        forecasts["forecast_month"], errors="raise"
    ).dt.date
    purchases["delivery_date"] = pd.to_datetime(
        purchases["delivery_date"], errors="raise"
    ).dt.date

    _validate_sources(
        products,
        forecasts,
        purchases,
        expected_product_count=expected_product_count,
    )

    if products["snapshot_date"].iloc[0] != as_of_date:
        raise ValueError("Snapshot date differs from as-of date.")

    expected_first_month = (
        pd.Timestamp(as_of_date) + pd.offsets.MonthBegin(1)
    ).date()
    if min(forecasts["forecast_month"]) != expected_first_month:
        raise ValueError(
            "First forecast month must immediately follow the as-of month."
        )

    if (purchases["outstanding_quantity"] < 0).any():
        raise ValueError("Outstanding purchase quantity is negative.")

    quantiles = forecasts[
        ["forecast_p10", "forecast_p50", "forecast_p90"]
    ].astype(float)
    if not np.isfinite(quantiles.to_numpy()).all():
        raise ValueError("Forecast quantiles must be finite.")
    if (
        (quantiles["forecast_p10"] < 0)
        | (quantiles["forecast_p10"] > quantiles["forecast_p50"])
        | (quantiles["forecast_p50"] > quantiles["forecast_p90"])
    ).any():
        raise ValueError("Forecast quantile ordering is invalid.")

    purchase_groups = {
        str(product_id): group.sort_values("delivery_date", kind="stable")
        for product_id, group in purchases.groupby(
            "product_id", observed=True, sort=True
        )
    }

    records: list[dict[str, Any]] = []

    for product in products.sort_values("product_id", kind="stable").itertuples(
        index=False
    ):
        product_id = str(product.product_id)
        lead_time_days = int(product.lead_time_days)
        moq = int(product.minimum_order_quantity)
        stock_available = int(product.stock_available)
        max_stock = int(product.max_stock)

        if not 1 <= lead_time_days <= MAX_SUPPORTED_LEAD_TIME_DAYS:
            raise ValueError(
                f"MVP supports lead time 1-{MAX_SUPPORTED_LEAD_TIME_DAYS} days; "
                f"{product_id} has {lead_time_days}."
            )
        if moq <= 0:
            raise ValueError(f"MOQ must be positive for {product_id}.")
        if stock_available < 0 or max_stock < 0:
            raise ValueError(f"Inventory quantities must be nonnegative for {product_id}.")

        product_forecasts = forecasts[
            forecasts["product_id"] == product_id
        ].sort_values("horizon", kind="stable")

        p10 = product_forecasts["forecast_p10"].to_numpy(dtype=float)
        p50 = product_forecasts["forecast_p50"].to_numpy(dtype=float)
        p90 = product_forecasts["forecast_p90"].to_numpy(dtype=float)
        cumulative_p10 = np.cumsum(p10)
        cumulative_p50 = np.cumsum(p50)
        cumulative_p90 = np.cumsum(p90)

        product_purchases = purchase_groups.get(product_id)

        def incoming_by(days: int) -> int:
            if product_purchases is None:
                return 0
            limit = as_of_date + timedelta(days=days)
            eligible = product_purchases[
                product_purchases["delivery_date"] <= limit
            ]
            return int(eligible["outstanding_quantity"].sum())

        incoming_lead_time = incoming_by(lead_time_days)
        incoming_30, incoming_60, incoming_90 = (
            incoming_by(30),
            incoming_by(60),
            incoming_by(90),
        )

        # All current lead times are <=30 days, so calibrated horizon-1
        # P50/P90 are the lead-time demand and reorder point.
        expected_demand = float(p50[0])
        reorder_point = float(p90[0])
        safety_stock = max(reorder_point - expected_demand, 0.0)

        lead_time_position = stock_available + incoming_lead_time
        raw_order = max(reorder_point - lead_time_position, 0.0)
        recommended_quantity = round_up_to_moq(raw_order, moq)

        positions = (
            stock_available + incoming_30,
            stock_available + incoming_60,
            stock_available + incoming_90,
        )
        stockout_probabilities = tuple(
            stockout_probability_proxy(
                position,
                cumulative_p10[index],
                cumulative_p50[index],
                cumulative_p90[index],
            )
            for index, position in enumerate(positions)
        )

        # P(ending stock after 90d > max_stock)
        overstock_threshold = max(positions[2] - max_stock, 0)
        overstock_probability = (
            quantile_cdf_proxy(
                overstock_threshold,
                cumulative_p10[2],
                cumulative_p50[2],
                cumulative_p90[2],
            )
            if overstock_threshold > 0
            else 0.0
        )

        records.append(
            {
                "model_run_id": model_run_id,
                "product_id": product_id,
                "as_of_date": as_of_date,
                "stock_available": stock_available,
                "incoming_quantity": incoming_lead_time,
                "expected_lead_time_demand": round(expected_demand, 10),
                "safety_stock": round(safety_stock, 10),
                "reorder_point": round(reorder_point, 10),
                "stockout_probability_30d": round(
                    stockout_probabilities[0], 10
                ),
                "stockout_probability_60d": round(
                    stockout_probabilities[1], 10
                ),
                "stockout_probability_90d": round(
                    stockout_probabilities[2], 10
                ),
                "overstock_probability_90d": round(
                    overstock_probability, 10
                ),
                "recommended_order_quantity": recommended_quantity,
                "recommended_order_date": (
                    as_of_date if recommended_quantity > 0 else None
                ),
                "minimum_order_quantity": moq,
            }
        )

    output = pd.DataFrame(records)
    validate_inventory_risk_frame(
        output, expected_product_count=expected_product_count
    )
    return output


def validate_inventory_risk_frame(
    frame: pd.DataFrame,
    *,
    expected_product_count: int = EXPECTED_PRODUCT_COUNT,
) -> None:
    if len(frame) != expected_product_count:
        raise ValueError("Risk frame row count is invalid.")
    if frame["product_id"].astype(str).nunique() != expected_product_count:
        raise ValueError("Risk frame product count is invalid.")
    if frame.duplicated(["model_run_id", "product_id", "as_of_date"]).any():
        raise ValueError("Duplicate risk rows detected.")

    probability_columns = (
        "stockout_probability_30d",
        "stockout_probability_60d",
        "stockout_probability_90d",
        "overstock_probability_90d",
    )
    probabilities = frame[list(probability_columns)].astype(float)
    if not np.isfinite(probabilities.to_numpy()).all():
        raise ValueError("Risk probabilities must be finite.")
    if ((probabilities < 0.0) | (probabilities > 1.0)).any().any():
        raise ValueError("Risk probabilities must be in [0, 1].")

    quantity_columns = (
        "stock_available",
        "incoming_quantity",
        "expected_lead_time_demand",
        "safety_stock",
        "reorder_point",
        "recommended_order_quantity",
    )
    quantities = frame[list(quantity_columns)].astype(float)
    if not np.isfinite(quantities.to_numpy()).all():
        raise ValueError("Risk quantities must be finite.")
    if (quantities < 0.0).any().any():
        raise ValueError("Risk quantities must be nonnegative.")

    formula_error = np.abs(
        frame["reorder_point"].astype(float)
        - frame["expected_lead_time_demand"].astype(float)
        - frame["safety_stock"].astype(float)
    )
    if (formula_error > 1e-8).any():
        raise ValueError("Reorder-point formula is invalid.")

    recommended = frame["recommended_order_quantity"].astype(int)
    positive = recommended > 0
    if frame.loc[positive, "recommended_order_date"].isna().any():
        raise ValueError("Positive orders require an order date.")
    if frame.loc[~positive, "recommended_order_date"].notna().any():
        raise ValueError("Zero orders must not have an order date.")

    moq = frame["minimum_order_quantity"].astype(int)
    if (moq <= 0).any():
        raise ValueError("MOQ must be positive.")
    if ((recommended[positive] % moq[positive]) != 0).any():
        raise ValueError("Recommended quantity violates MOQ.")


def _load_context(
    connection: Connection[Any],
) -> tuple[UUID, date, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    run = query_frame(
        connection,
        """
        SELECT id, training_cutoff, forecast_horizon_months, parameters
        FROM ml.model_runs
        WHERE status = 'completed'
          AND model_family = 'hybrid_calibrated'
          AND lower(
                coalesce(parameters ->> 'inventory_risk_ready', 'false')
              ) = 'true'
          AND lower(
                coalesce(
                    parameters ->> 'interval_calibration_required',
                    'true'
                )
              ) = 'false'
        ORDER BY finished_at DESC, started_at DESC, id DESC
        LIMIT 1
        """,
    )
    if run.empty:
        raise RuntimeError(
            "No completed inventory-risk-ready calibrated run exists."
        )

    run_row = run.iloc[0]
    model_run_id = run_row["id"]
    training_cutoff = run_row["training_cutoff"]
    if int(run_row["forecast_horizon_months"]) != len(FORECAST_HORIZONS):
        raise RuntimeError("Calibrated run must contain three horizons.")

    forecasts = query_frame(
        connection,
        """
        SELECT
            product_id,
            forecast_month,
            horizon,
            forecast_p10,
            forecast_p50,
            forecast_p90
        FROM ml.forecasts
        WHERE model_run_id = %s
        ORDER BY product_id, horizon
        """,
        (model_run_id,),
    )

    products = query_frame(
        connection,
        """
        WITH latest_snapshot AS (
            SELECT id, snapshot_date
            FROM core.inventory_snapshots
            WHERE snapshot_date <= %s
            ORDER BY snapshot_date DESC, id DESC
            LIMIT 1
        )
        SELECT
            products.product_id,
            products.lead_time_days,
            products.minimum_order_quantity,
            lines.stock_available,
            lines.max_stock,
            latest_snapshot.snapshot_date
        FROM latest_snapshot
        JOIN core.inventory_snapshot_lines AS lines
          ON lines.snapshot_id = latest_snapshot.id
        JOIN core.products AS products
          ON products.product_id = lines.product_id
        ORDER BY products.product_id
        """,
        (training_cutoff,),
    )
    if products.empty:
        raise RuntimeError("No inventory snapshot exists before the cutoff.")

    as_of_date = products.iloc[0]["snapshot_date"]

    purchases = query_frame(
        connection,
        """
        SELECT
            lines.product_id,
            lines.delivery_date,
            greatest(
                lines.ordered_quantity - lines.delivered_quantity,
                0
            )::integer AS outstanding_quantity
        FROM core.purchase_order_lines AS lines
        JOIN core.purchase_orders AS orders
          ON orders.purchase_order_id = lines.purchase_order_id
        WHERE orders.order_date <= %s
          AND lines.delivery_date >= %s
          AND lines.ordered_quantity > lines.delivered_quantity
        ORDER BY lines.product_id, lines.delivery_date, lines.id
        """,
        (as_of_date, as_of_date),
    )

    return model_run_id, as_of_date, products, forecasts, purchases


def run_inventory_risk(connection: Connection[Any]) -> InventoryRiskSummary:
    model_run_id, as_of_date, products, forecasts, purchases = _load_context(
        connection
    )

    recommendations_before = int(
        query_frame(
            connection,
            "SELECT count(*)::integer AS row_count FROM ml.recommendations",
        ).iloc[0]["row_count"]
    )

    frame = build_inventory_risk_frame(
        products,
        forecasts,
        purchases,
        model_run_id=model_run_id,
        as_of_date=as_of_date,
    )

    existing = query_frame(
        connection,
        """
        SELECT
            count(*)::integer AS row_count,
            count(DISTINCT product_id)::integer AS product_count
        FROM ml.inventory_risk
        WHERE model_run_id = %s AND as_of_date = %s
        """,
        (model_run_id, as_of_date),
    ).iloc[0]

    existing_signature = (
        int(existing["row_count"]),
        int(existing["product_count"]),
    )
    if existing_signature not in ((0, 0), (EXPECTED_PRODUCT_COUNT,) * 2):
        raise RuntimeError(
            f"Partial existing inventory-risk output: {existing_signature}."
        )

    rows = [
        (
            row.model_run_id,
            row.product_id,
            row.as_of_date,
            int(row.stock_available),
            int(row.incoming_quantity),
            float(row.expected_lead_time_demand),
            float(row.safety_stock),
            float(row.reorder_point),
            float(row.stockout_probability_30d),
            float(row.stockout_probability_60d),
            float(row.stockout_probability_90d),
            float(row.overstock_probability_90d),
            int(row.recommended_order_quantity),
            row.recommended_order_date,
        )
        for row in frame.itertuples(index=False)
    ]

    try:
        execute_many(
            connection,
            """
            INSERT INTO ml.inventory_risk (
                model_run_id,
                product_id,
                as_of_date,
                stock_available,
                incoming_quantity,
                expected_lead_time_demand,
                safety_stock,
                reorder_point,
                stockout_probability_30d,
                stockout_probability_60d,
                stockout_probability_90d,
                overstock_probability_90d,
                recommended_order_quantity,
                recommended_order_date
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (model_run_id, product_id, as_of_date)
            DO UPDATE SET
                stock_available = EXCLUDED.stock_available,
                incoming_quantity = EXCLUDED.incoming_quantity,
                expected_lead_time_demand =
                    EXCLUDED.expected_lead_time_demand,
                safety_stock = EXCLUDED.safety_stock,
                reorder_point = EXCLUDED.reorder_point,
                stockout_probability_30d =
                    EXCLUDED.stockout_probability_30d,
                stockout_probability_60d =
                    EXCLUDED.stockout_probability_60d,
                stockout_probability_90d =
                    EXCLUDED.stockout_probability_90d,
                overstock_probability_90d =
                    EXCLUDED.overstock_probability_90d,
                recommended_order_quantity =
                    EXCLUDED.recommended_order_quantity,
                recommended_order_date =
                    EXCLUDED.recommended_order_date,
                created_at = now()
            """,
            rows,
        )

        integrity = query_frame(
            connection,
            """
            SELECT
                count(*)::integer AS row_count,
                count(DISTINCT product_id)::integer AS product_count,
                count(*) FILTER (
                    WHERE
                        stockout_probability_30d NOT BETWEEN 0 AND 1
                        OR stockout_probability_60d NOT BETWEEN 0 AND 1
                        OR stockout_probability_90d NOT BETWEEN 0 AND 1
                        OR overstock_probability_90d NOT BETWEEN 0 AND 1
                )::integer AS invalid_probability_count,
                count(*) FILTER (
                    WHERE
                        stock_available < 0
                        OR incoming_quantity < 0
                        OR expected_lead_time_demand < 0
                        OR safety_stock < 0
                        OR reorder_point < 0
                        OR recommended_order_quantity < 0
                )::integer AS negative_quantity_count,
                count(*) FILTER (
                    WHERE abs(
                        reorder_point
                        - expected_lead_time_demand
                        - safety_stock
                    ) > 0.00000001
                )::integer AS formula_mismatch_count,
                count(*) FILTER (
                    WHERE
                        (recommended_order_quantity > 0
                         AND recommended_order_date IS NULL)
                        OR
                        (recommended_order_quantity = 0
                         AND recommended_order_date IS NOT NULL)
                )::integer AS order_date_mismatch_count
            FROM ml.inventory_risk
            WHERE model_run_id = %s AND as_of_date = %s
            """,
            (model_run_id, as_of_date),
        ).iloc[0]

        moq_mismatches = int(
            query_frame(
                connection,
                """
                SELECT count(*)::integer AS mismatch_count
                FROM ml.inventory_risk AS risk
                JOIN core.products AS products
                  ON products.product_id = risk.product_id
                WHERE risk.model_run_id = %s
                  AND risk.as_of_date = %s
                  AND risk.recommended_order_quantity > 0
                  AND (
                        products.minimum_order_quantity <= 0
                        OR risk.recommended_order_quantity
                           % products.minimum_order_quantity <> 0
                      )
                """,
                (model_run_id, as_of_date),
            ).iloc[0]["mismatch_count"]
        )

        checks = {
            "row_count": int(integrity["row_count"]),
            "product_count": int(integrity["product_count"]),
            "invalid_probability_count": int(
                integrity["invalid_probability_count"]
            ),
            "negative_quantity_count": int(
                integrity["negative_quantity_count"]
            ),
            "formula_mismatch_count": int(
                integrity["formula_mismatch_count"]
            ),
            "order_date_mismatch_count": int(
                integrity["order_date_mismatch_count"]
            ),
            "moq_mismatch_count": moq_mismatches,
        }

        if checks["row_count"] != EXPECTED_PRODUCT_COUNT:
            raise RuntimeError(f"Risk row count failed: {checks}.")
        if checks["product_count"] != EXPECTED_PRODUCT_COUNT:
            raise RuntimeError(f"Risk product count failed: {checks}.")
        for key in (
            "invalid_probability_count",
            "negative_quantity_count",
            "formula_mismatch_count",
            "order_date_mismatch_count",
            "moq_mismatch_count",
        ):
            if checks[key] != 0:
                raise RuntimeError(f"Risk integrity failed: {checks}.")

        recommendations_after = int(
            query_frame(
                connection,
                "SELECT count(*)::integer AS row_count FROM ml.recommendations",
            ).iloc[0]["row_count"]
        )
        if recommendations_after != recommendations_before:
            raise RuntimeError(
                "Recommendation rows changed during inventory-risk build."
            )

        connection.commit()
    except Exception:
        connection.rollback()
        raise

    incoming = frame["incoming_quantity"].astype(int)
    recommended = frame["recommended_order_quantity"].astype(int)

    return InventoryRiskSummary(
        model_run_id=model_run_id,
        as_of_date=as_of_date,
        product_count=int(frame["product_id"].nunique()),
        row_count=int(len(frame)),
        incoming_product_count=int((incoming > 0).sum()),
        recommended_product_count=int((recommended > 0).sum()),
        total_incoming_quantity=int(incoming.sum()),
        total_recommended_quantity=int(recommended.sum()),
    )
