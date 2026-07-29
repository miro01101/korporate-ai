from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any
from uuid import UUID, uuid4

import numpy as np
import pandas as pd
from psycopg import Connection
from psycopg.types.json import Jsonb

from ml_pipeline.db import execute_many, query_frame
from ml_pipeline.forecasting import (
    forecast_quantiles_from_backtest,
)
from ml_pipeline.lightgbm_challenger import (
    _apply_categories,
    _future_row,
    _predict_quantiles,
    fit_quantile_models,
    prepare_model_frame,
)


MODEL_FAMILY = "hybrid_calibrated"
MODEL_VERSION = "inventory-hybrid-calibrated-v1"

TARGET_COVERAGE = 0.80
ALPHA = 1.0 - TARGET_COVERAGE

ROLLING_ORIGIN_COUNT = 12
CALIBRATION_ORIGIN_COUNT = 8
HOLDOUT_ORIGIN_COUNT = 4
FORECAST_HORIZONS = (1, 2, 3)

OVERALL_COVERAGE_GATE = 0.78
HORIZON_COVERAGE_GATE = 0.75
CELL_COVERAGE_GATE = 0.70

BASELINE_FAMILY = "baseline_ensemble"
LIGHTGBM_FAMILY = "global_lightgbm_quantile"


@dataclass(frozen=True)
class CalibrationSummary:
    model_run_id: UUID
    feature_run_id: UUID
    parent_hybrid_run_id: UUID
    forecast_count: int
    metric_count: int
    calibration_prediction_count: int
    holdout_prediction_count: int
    overall_holdout_coverage: float
    minimum_horizon_coverage: float
    minimum_cell_coverage: float
    inventory_risk_ready: bool
    calibration_origins: tuple[str, ...]
    holdout_origins: tuple[str, ...]


def finite_sample_qhat(
    scores: np.ndarray,
    *,
    alpha: float = ALPHA,
) -> float:
    values = np.asarray(scores, dtype=float)

    values = values[np.isfinite(values)]

    if values.size == 0:
        raise ValueError(
            "Calibration score sample is empty."
        )

    if not 0.0 < alpha < 1.0:
        raise ValueError(
            "alpha must be between zero and one."
        )

    quantile_level = min(
        1.0,
        math.ceil(
            (values.size + 1) * (1.0 - alpha)
        )
        / values.size,
    )

    qhat = float(
        np.quantile(
            values,
            quantile_level,
            method="higher",
        )
    )

    # The current system is under-covering. For the MVP,
    # calibration may widen intervals but never shrink them.
    return max(qhat, 0.0)


def interval_score(
    actual: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    *,
    alpha: float = ALPHA,
) -> np.ndarray:
    y = np.asarray(actual, dtype=float)
    low = np.asarray(lower, dtype=float)
    high = np.asarray(upper, dtype=float)

    width = high - low

    below_penalty = (
        2.0 / alpha
    ) * np.maximum(low - y, 0.0)

    above_penalty = (
        2.0 / alpha
    ) * np.maximum(y - high, 0.0)

    return width + below_penalty + above_penalty


def apply_qhat(
    frame: pd.DataFrame,
    qhat_by_cell: dict[tuple[str, int], float],
) -> pd.DataFrame:
    output = frame.copy()

    qhats = []

    for row in output.itertuples(index=False):
        key = (
            str(row.source_family),
            int(row.horizon),
        )

        if key not in qhat_by_cell:
            raise ValueError(
                f"Missing qhat for calibration cell {key}."
            )

        qhats.append(qhat_by_cell[key])

    output["qhat"] = np.asarray(
        qhats,
        dtype=float,
    )

    output["calibrated_p10"] = np.maximum(
        0.0,
        output["p10"].astype(float)
        - output["qhat"],
    )

    output["calibrated_p50"] = (
        output["p50"].astype(float)
    )

    output["calibrated_p90"] = np.maximum(
        output["p90"].astype(float)
        + output["qhat"],
        output["calibrated_p50"],
    )

    output["covered"] = (
        output["actual"].astype(float)
        >= output["calibrated_p10"]
    ) & (
        output["actual"].astype(float)
        <= output["calibrated_p90"]
    )

    output["interval_width"] = (
        output["calibrated_p90"]
        - output["calibrated_p10"]
    )

    output["interval_score"] = interval_score(
        output["actual"].to_numpy(dtype=float),
        output["calibrated_p10"].to_numpy(
            dtype=float
        ),
        output["calibrated_p90"].to_numpy(
            dtype=float
        ),
    )

    return output


def readiness_decision(
    holdout: pd.DataFrame,
) -> tuple[bool, float, float, float]:
    if holdout.empty:
        raise ValueError(
            "Holdout prediction frame is empty."
        )

    overall_coverage = float(
        holdout["covered"].mean()
    )

    horizon_coverage = (
        holdout.groupby(
            "horizon",
            observed=True,
        )["covered"]
        .mean()
    )

    cell_coverage = (
        holdout.groupby(
            ["source_family", "horizon"],
            observed=True,
        )["covered"]
        .mean()
    )

    minimum_horizon_coverage = float(
        horizon_coverage.min()
    )

    minimum_cell_coverage = float(
        cell_coverage.min()
    )

    ready = bool(
        overall_coverage >= OVERALL_COVERAGE_GATE
        and minimum_horizon_coverage
        >= HORIZON_COVERAGE_GATE
        and minimum_cell_coverage
        >= CELL_COVERAGE_GATE
    )

    return (
        ready,
        overall_coverage,
        minimum_horizon_coverage,
        minimum_cell_coverage,
    )


def _category_cold_start_points(
    features: pd.DataFrame,
    origin: pd.Timestamp,
) -> dict[str, float]:
    history = features[
        features["month_start"] <= origin
    ].copy()

    recent_start = origin - pd.DateOffset(months=5)

    recent = history[
        history["month_start"] >= recent_start
    ]

    recent_means = (
        recent.groupby(
            "product_id",
            observed=True,
            as_index=False,
        )["units_sold"]
        .mean()
        .rename(
            columns={
                "units_sold": "recent_mean"
            }
        )
    )

    latest = (
        history.sort_values(
            ["product_id", "month_start"],
            kind="stable",
        )
        .groupby(
            "product_id",
            observed=True,
            sort=True,
        )
        .tail(1)[
            [
                "product_id",
                "category",
                "is_cold_start",
            ]
        ]
        .merge(
            recent_means,
            on="product_id",
            how="left",
            validate="one_to_one",
        )
    )

    latest["recent_mean"] = pd.to_numeric(
        latest["recent_mean"],
        errors="coerce",
    ).fillna(0.0)

    established = latest[
        ~latest["is_cold_start"].astype(bool)
    ]

    global_fallback = (
        float(established["recent_mean"].median())
        if not established.empty
        else 0.0
    )

    points: dict[str, float] = {}

    for row in latest.itertuples(index=False):
        peers = established[
            established["category"] == row.category
        ]

        if peers.empty:
            point = global_fallback
        else:
            point = float(
                peers["recent_mean"].median()
            )

        points[str(row.product_id)] = max(
            point,
            0.0,
        )

    return points


def _baseline_predictions(
    features: pd.DataFrame,
    selection: pd.DataFrame,
    origin: pd.Timestamp,
) -> list[dict[str, Any]]:
    selected = selection[
        selection["source_family"]
        == BASELINE_FAMILY
    ]

    cold_start_points = (
        _category_cold_start_points(
            features,
            origin,
        )
    )

    records: list[dict[str, Any]] = []

    for selected_row in selected.itertuples(
        index=False
    ):
        product_id = str(selected_row.product_id)
        model_name = str(
            selected_row.selected_model
        )

        product_frame = features[
            (features["product_id"] == product_id)
            & (features["month_start"] <= origin)
        ].sort_values(
            "month_start",
            kind="stable",
        )

        history = product_frame[
            "units_sold"
        ].to_numpy(dtype=float)

        recursive_history = history.copy()

        for horizon in FORECAST_HORIZONS:
            target_month = (
                origin
                + pd.offsets.MonthBegin(horizon)
            )

            if model_name == "category_cold_start":
                p50 = cold_start_points.get(
                    product_id,
                    0.0,
                )

                p10 = max(p50 * 0.50, 0.0)
                p90 = max(p50 * 1.50, p50)

            else:
                p10, p50, p90 = (
                    forecast_quantiles_from_backtest(
                        recursive_history,
                        model_name,
                    )
                )

                recursive_history = np.append(
                    recursive_history,
                    p50,
                )

            records.append(
                {
                    "origin_month": origin,
                    "target_month": target_month,
                    "product_id": product_id,
                    "source_family": BASELINE_FAMILY,
                    "horizon": horizon,
                    "p10": float(p10),
                    "p50": float(p50),
                    "p90": float(p90),
                }
            )

    return records


def _lightgbm_predictions(
    features: pd.DataFrame,
    selection: pd.DataFrame,
    origin: pd.Timestamp,
) -> list[dict[str, Any]]:
    selected_ids = set(
        selection.loc[
            selection["source_family"]
            == LIGHTGBM_FAMILY,
            "product_id",
        ].astype(str)
    )

    if not selected_ids:
        return []

    training_raw = features[
        features["month_start"] <= origin
    ].copy()

    training, levels = prepare_model_frame(
        training_raw
    )

    models = fit_quantile_models(training)

    base_month = training["month_start"].min()

    latest_rows = (
        training.sort_values(
            ["product_id", "month_start"],
            kind="stable",
        )
        .groupby(
            "product_id",
            observed=True,
            sort=True,
        )
        .tail(1)
    )

    histories: dict[str, list[float]] = {
        str(product_id): (
            product_frame.sort_values(
                "month_start",
                kind="stable",
            )["units_sold"]
            .astype(float)
            .tolist()
        )
        for product_id, product_frame
        in training.groupby(
            "product_id",
            observed=True,
            sort=True,
        )
    }

    records: list[dict[str, Any]] = []

    for horizon in FORECAST_HORIZONS:
        target_month = (
            origin
            + pd.offsets.MonthBegin(horizon)
        )

        future_rows: list[dict[str, Any]] = []

        for latest in latest_rows.itertuples(
            index=False
        ):
            latest_series = pd.Series(
                latest._asdict()
            )

            product_id = str(
                latest_series["product_id"]
            )

            future_rows.append(
                _future_row(
                    latest_series,
                    histories[product_id],
                    target_month,
                    base_month,
                )
            )

        future = pd.DataFrame(future_rows)
        future = _apply_categories(
            future,
            levels,
        )

        p10, p50, p90, _ = _predict_quantiles(
            models,
            future,
        )

        for index, row in enumerate(
            future.itertuples(index=False)
        ):
            product_id = str(row.product_id)

            histories[product_id].append(
                float(p50[index])
            )

            if product_id not in selected_ids:
                continue

            records.append(
                {
                    "origin_month": origin,
                    "target_month": target_month,
                    "product_id": product_id,
                    "source_family": LIGHTGBM_FAMILY,
                    "horizon": horizon,
                    "p10": float(p10[index]),
                    "p50": float(p50[index]),
                    "p90": float(p90[index]),
                }
            )

    return records


def build_rolling_predictions(
    features: pd.DataFrame,
    selection: pd.DataFrame,
) -> tuple[
    pd.DataFrame,
    tuple[pd.Timestamp, ...],
    tuple[pd.Timestamp, ...],
]:
    frame = features.copy()

    frame["product_id"] = (
        frame["product_id"].astype(str)
    )

    frame["month_start"] = pd.to_datetime(
        frame["month_start"]
    )

    frame["units_sold"] = pd.to_numeric(
        frame["units_sold"],
        errors="coerce",
    ).fillna(0.0)

    frame["is_cold_start"] = (
        frame["is_cold_start"].astype(bool)
    )

    months = tuple(
        pd.Timestamp(month)
        for month in sorted(
            frame["month_start"].unique()
        )
    )

    max_month = max(months)

    eligible_origins = tuple(
        month
        for month in months
        if (
            month
            + pd.offsets.MonthBegin(
                max(FORECAST_HORIZONS)
            )
        )
        <= max_month
    )

    if len(eligible_origins) < ROLLING_ORIGIN_COUNT:
        raise ValueError(
            "Insufficient eligible rolling origins."
        )

    origins = eligible_origins[
        -ROLLING_ORIGIN_COUNT:
    ]

    calibration_origins = origins[
        :CALIBRATION_ORIGIN_COUNT
    ]

    holdout_origins = origins[
        CALIBRATION_ORIGIN_COUNT:
    ]

    if (
        len(holdout_origins)
        != HOLDOUT_ORIGIN_COUNT
    ):
        raise ValueError(
            "Unexpected holdout origin count."
        )

    actual_lookup = {
        (
            str(row.product_id),
            pd.Timestamp(row.month_start),
        ): float(row.units_sold)
        for row in frame.itertuples(index=False)
    }

    records: list[dict[str, Any]] = []

    for origin in origins:
        records.extend(
            _baseline_predictions(
                frame,
                selection,
                origin,
            )
        )

        records.extend(
            _lightgbm_predictions(
                frame,
                selection,
                origin,
            )
        )

    predictions = pd.DataFrame(records)

    predictions["actual"] = [
        actual_lookup[
            (
                str(row.product_id),
                pd.Timestamp(row.target_month),
            )
        ]
        for row in predictions.itertuples(index=False)
    ]

    duplicate_count = int(
        predictions.duplicated(
            [
                "origin_month",
                "product_id",
                "horizon",
            ]
        ).sum()
    )

    if duplicate_count:
        raise ValueError(
            "Duplicate rolling prediction rows: "
            f"{duplicate_count}"
        )

    expected_rows = (
        ROLLING_ORIGIN_COUNT
        * int(selection["product_id"].nunique())
        * len(FORECAST_HORIZONS)
    )

    if len(predictions) != expected_rows:
        raise ValueError(
            "Unexpected rolling prediction count: "
            f"{len(predictions)} != {expected_rows}"
        )

    return (
        predictions,
        calibration_origins,
        holdout_origins,
    )


def _load_context(
    connection: Connection[Any],
) -> tuple[
    UUID,
    str,
    Any,
    str,
    UUID,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    hybrid_run = query_frame(
        connection,
        """
        SELECT
            id,
            feature_run_id,
            training_cutoff,
            feature_version,
            dataset_fingerprint
        FROM ml.model_runs
        WHERE model_family = 'hybrid_champion'
          AND status = 'completed'
        ORDER BY finished_at DESC
        LIMIT 1
        """,
    )

    if hybrid_run.empty:
        raise RuntimeError(
            "No completed hybrid champion exists."
        )

    row = hybrid_run.iloc[0]

    parent_hybrid_run_id = row["id"]
    feature_run_id = row["feature_run_id"]

    selection = query_frame(
        connection,
        """
        SELECT
            product_id,
            selected_model,
            is_cold_start,
            CASE
                WHEN selected_model =
                     'lightgbm_quantile'
                THEN 'global_lightgbm_quantile'
                ELSE 'baseline_ensemble'
            END AS source_family
        FROM ml.forecasts
        WHERE model_run_id = %s
          AND horizon = 1
        ORDER BY product_id
        """,
        (parent_hybrid_run_id,),
    )

    features = query_frame(
        connection,
        """
        SELECT
            product_id,
            month_start,
            category,
            supplier_id,
            purchase_price,
            sales_price,
            lead_time_days,
            minimum_order_quantity,
            units_sold,
            lag_1,
            lag_2,
            lag_3,
            lag_6,
            lag_12,
            rolling_mean_3,
            rolling_mean_6,
            rolling_mean_12,
            rolling_std_3,
            rolling_std_6,
            is_cold_start
        FROM ml.product_monthly_features
        WHERE feature_run_id = %s
        ORDER BY product_id, month_start
        """,
        (feature_run_id,),
    )

    parent_forecasts = query_frame(
        connection,
        """
        SELECT
            product_id,
            forecast_month,
            horizon,
            forecast_p10,
            forecast_p50,
            forecast_p90,
            selected_model,
            is_cold_start,
            backtest_wape,
            backtest_bias,
            confidence_score,
            CASE
                WHEN selected_model =
                     'lightgbm_quantile'
                THEN 'global_lightgbm_quantile'
                ELSE 'baseline_ensemble'
            END AS source_family
        FROM ml.forecasts
        WHERE model_run_id = %s
        ORDER BY product_id, forecast_month
        """,
        (parent_hybrid_run_id,),
    )

    return (
        feature_run_id,
        str(row["feature_version"]),
        row["training_cutoff"],
        str(row["dataset_fingerprint"]),
        parent_hybrid_run_id,
        selection,
        features,
        parent_forecasts,
    )


def run_interval_calibration(
    connection: Connection[Any],
    *,
    code_commit: str | None,
) -> CalibrationSummary:
    (
        feature_run_id,
        feature_version,
        training_cutoff,
        fingerprint,
        parent_hybrid_run_id,
        selection,
        features,
        parent_forecasts,
    ) = _load_context(connection)

    duplicate = query_frame(
        connection,
        """
        SELECT id
        FROM ml.model_runs
        WHERE feature_run_id = %s
          AND model_family = %s
          AND code_commit IS NOT DISTINCT FROM %s
          AND status = 'completed'
        LIMIT 1
        """,
        (
            feature_run_id,
            MODEL_FAMILY,
            code_commit,
        ),
    )

    if not duplicate.empty:
        raise RuntimeError(
            "A completed calibration run already exists "
            "for this feature run and commit."
        )

    (
        rolling_predictions,
        calibration_origins,
        holdout_origins,
    ) = build_rolling_predictions(
        features,
        selection,
    )

    calibration = rolling_predictions[
        rolling_predictions["origin_month"].isin(
            calibration_origins
        )
    ].copy()

    holdout_raw = rolling_predictions[
        rolling_predictions["origin_month"].isin(
            holdout_origins
        )
    ].copy()

    qhat_by_cell: dict[
        tuple[str, int],
        float,
    ] = {}

    calibration_rows: list[dict[str, Any]] = []

    for (
        source_family,
        horizon,
    ), cell in calibration.groupby(
        ["source_family", "horizon"],
        observed=True,
        sort=True,
    ):
        scores = np.maximum(
            cell["p10"].to_numpy(dtype=float)
            - cell["actual"].to_numpy(dtype=float),
            cell["actual"].to_numpy(dtype=float)
            - cell["p90"].to_numpy(dtype=float),
        )

        qhat = finite_sample_qhat(scores)

        key = (
            str(source_family),
            int(horizon),
        )

        qhat_by_cell[key] = qhat

        calibrated_cell = apply_qhat(
            cell,
            {key: qhat},
        )

        calibration_rows.append(
            {
                "source_family": str(
                    source_family
                ),
                "horizon": int(horizon),
                "qhat": qhat,
                "sample_size": int(len(cell)),
                "origin_count": int(
                    cell["origin_month"].nunique()
                ),
                "coverage": float(
                    calibrated_cell["covered"].mean()
                ),
            }
        )

    holdout = apply_qhat(
        holdout_raw,
        qhat_by_cell,
    )

    (
        inventory_risk_ready,
        overall_holdout_coverage,
        minimum_horizon_coverage,
        minimum_cell_coverage,
    ) = readiness_decision(holdout)

    calibration_table = pd.DataFrame(
        calibration_rows
    )

    horizon_metrics = (
        holdout.groupby(
            "horizon",
            observed=True,
            sort=True,
        )
        .agg(
            coverage=("covered", "mean"),
            mean_width=("interval_width", "mean"),
            mean_interval_score=(
                "interval_score",
                "mean",
            ),
            sample_size=("covered", "size"),
            origin_count=(
                "origin_month",
                "nunique",
            ),
        )
        .reset_index()
    )

    cell_metrics = (
        holdout.groupby(
            ["source_family", "horizon"],
            observed=True,
            sort=True,
        )
        .agg(
            coverage=("covered", "mean"),
            mean_width=("interval_width", "mean"),
            mean_interval_score=(
                "interval_score",
                "mean",
            ),
            sample_size=("covered", "size"),
            origin_count=(
                "origin_month",
                "nunique",
            ),
        )
        .reset_index()
    )

    metric_records: list[dict[str, Any]] = []

    for row in calibration_table.itertuples(
        index=False
    ):
        for metric_name, metric_value in (
            ("calibration_qhat", row.qhat),
            (
                "calibration_coverage",
                row.coverage,
            ),
        ):
            metric_records.append(
                {
                    "model_name": row.source_family,
                    "horizon": row.horizon,
                    "metric_name": metric_name,
                    "metric_value": float(
                        metric_value
                    ),
                    "sample_size": int(
                        row.sample_size
                    ),
                    "fold_count": int(
                        row.origin_count
                    ),
                }
            )

    for row in cell_metrics.itertuples(
        index=False
    ):
        for metric_name, metric_value in (
            ("holdout_coverage", row.coverage),
            (
                "holdout_mean_width",
                row.mean_width,
            ),
            (
                "holdout_mean_interval_score",
                row.mean_interval_score,
            ),
        ):
            metric_records.append(
                {
                    "model_name": row.source_family,
                    "horizon": int(row.horizon),
                    "metric_name": metric_name,
                    "metric_value": float(
                        metric_value
                    ),
                    "sample_size": int(
                        row.sample_size
                    ),
                    "fold_count": int(
                        row.origin_count
                    ),
                }
            )

    for row in horizon_metrics.itertuples(
        index=False
    ):
        for metric_name, metric_value in (
            ("holdout_coverage", row.coverage),
            (
                "holdout_mean_width",
                row.mean_width,
            ),
            (
                "holdout_mean_interval_score",
                row.mean_interval_score,
            ),
        ):
            metric_records.append(
                {
                    "model_name": MODEL_FAMILY,
                    "horizon": int(row.horizon),
                    "metric_name": metric_name,
                    "metric_value": float(
                        metric_value
                    ),
                    "sample_size": int(
                        row.sample_size
                    ),
                    "fold_count": int(
                        row.origin_count
                    ),
                }
            )

    metric_records.extend(
        [
            {
                "model_name": MODEL_FAMILY,
                "horizon": 0,
                "metric_name": (
                    "holdout_coverage_overall"
                ),
                "metric_value": (
                    overall_holdout_coverage
                ),
                "sample_size": int(len(holdout)),
                "fold_count": int(
                    len(holdout_origins)
                ),
            },
            {
                "model_name": MODEL_FAMILY,
                "horizon": 0,
                "metric_name": (
                    "minimum_horizon_coverage"
                ),
                "metric_value": (
                    minimum_horizon_coverage
                ),
                "sample_size": int(len(holdout)),
                "fold_count": int(
                    len(holdout_origins)
                ),
            },
            {
                "model_name": MODEL_FAMILY,
                "horizon": 0,
                "metric_name": (
                    "minimum_cell_coverage"
                ),
                "metric_value": (
                    minimum_cell_coverage
                ),
                "sample_size": int(len(holdout)),
                "fold_count": int(
                    len(holdout_origins)
                ),
            },
            {
                "model_name": MODEL_FAMILY,
                "horizon": 0,
                "metric_name": (
                    "inventory_risk_ready"
                ),
                "metric_value": (
                    1.0
                    if inventory_risk_ready
                    else 0.0
                ),
                "sample_size": int(len(holdout)),
                "fold_count": int(
                    len(holdout_origins)
                ),
            },
        ]
    )

    holdout_coverage_by_cell = {
        f"{row.source_family}:h{int(row.horizon)}": (
            float(row.coverage)
        )
        for row in cell_metrics.itertuples(
            index=False
        )
    }

    qhat_json = {
        f"{source_family}:h{horizon}": qhat
        for (
            source_family,
            horizon,
        ), qhat in sorted(qhat_by_cell.items())
    }

    model_run_id = uuid4()

    parameters = {
        "parent_hybrid_run_id": str(
            parent_hybrid_run_id
        ),
        "target_coverage": TARGET_COVERAGE,
        "alpha": ALPHA,
        "method": (
            "rolling_origin_horizon_source_cqr"
        ),
        "rolling_origin_count": (
            ROLLING_ORIGIN_COUNT
        ),
        "calibration_origin_count": (
            CALIBRATION_ORIGIN_COUNT
        ),
        "holdout_origin_count": (
            HOLDOUT_ORIGIN_COUNT
        ),
        "calibration_origins": [
            origin.date().isoformat()
            for origin in calibration_origins
        ],
        "holdout_origins": [
            origin.date().isoformat()
            for origin in holdout_origins
        ],
        "qhat_by_source_horizon": qhat_json,
        "holdout_coverage_by_source_horizon": (
            holdout_coverage_by_cell
        ),
        "readiness_gates": {
            "overall": OVERALL_COVERAGE_GATE,
            "horizon": HORIZON_COVERAGE_GATE,
            "cell": CELL_COVERAGE_GATE,
        },
        "overall_holdout_coverage": (
            overall_holdout_coverage
        ),
        "minimum_horizon_coverage": (
            minimum_horizon_coverage
        ),
        "minimum_cell_coverage": (
            minimum_cell_coverage
        ),
        "point_forecast_ready": True,
        "inventory_risk_ready": (
            inventory_risk_ready
        ),
        "interval_calibration_required": (
            not inventory_risk_ready
        ),
        "provisional_champion": True,
        "automatic_inventory_actions": False,
        "time_series_guarantee_note": (
            "Empirical rolling-origin holdout gate; "
            "classical exchangeability guarantee is "
            "not claimed."
        ),
    }

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO ml.model_runs (
                id,
                feature_run_id,
                status,
                model_family,
                model_version,
                training_cutoff,
                forecast_horizon_months,
                feature_version,
                code_commit,
                dataset_fingerprint,
                parameters
            )
            VALUES (
                %s,
                %s,
                'running',
                %s,
                %s,
                %s,
                3,
                %s,
                %s,
                %s,
                %s
            )
            """,
            (
                model_run_id,
                feature_run_id,
                MODEL_FAMILY,
                MODEL_VERSION,
                training_cutoff,
                feature_version,
                code_commit,
                fingerprint,
                Jsonb(parameters),
            ),
        )

    connection.commit()

    try:
        metric_rows = [
            (
                uuid4(),
                model_run_id,
                None,
                record["model_name"],
                record["horizon"],
                record["metric_name"],
                record["metric_value"],
                record["sample_size"],
                record["fold_count"],
            )
            for record in metric_records
        ]

        execute_many(
            connection,
            """
            INSERT INTO ml.model_metrics (
                id,
                model_run_id,
                product_id,
                model_name,
                horizon,
                metric_name,
                metric_value,
                sample_size,
                fold_count
            )
            VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s
            )
            """,
            metric_rows,
        )

        forecast_rows = []

        for row in parent_forecasts.itertuples(
            index=False
        ):
            key = (
                str(row.source_family),
                int(row.horizon),
            )

            qhat = qhat_by_cell[key]

            calibrated_p10 = max(
                0.0,
                float(row.forecast_p10) - qhat,
            )

            calibrated_p50 = float(
                row.forecast_p50
            )

            calibrated_p90 = max(
                float(row.forecast_p90) + qhat,
                calibrated_p50,
            )

            cell_key = (
                f"{key[0]}:h{key[1]}"
            )

            cell_coverage = (
                holdout_coverage_by_cell[cell_key]
            )

            calibrated_confidence = min(
                float(row.confidence_score),
                cell_coverage,
            )

            forecast_rows.append(
                (
                    model_run_id,
                    str(row.product_id),
                    row.forecast_month,
                    int(row.horizon),
                    calibrated_p10,
                    calibrated_p50,
                    calibrated_p90,
                    str(row.selected_model),
                    bool(row.is_cold_start),
                    (
                        float(row.backtest_wape)
                        if pd.notna(row.backtest_wape)
                        else None
                    ),
                    (
                        float(row.backtest_bias)
                        if pd.notna(row.backtest_bias)
                        else None
                    ),
                    calibrated_confidence,
                )
            )

        execute_many(
            connection,
            """
            INSERT INTO ml.forecasts (
                model_run_id,
                product_id,
                forecast_month,
                horizon,
                forecast_p10,
                forecast_p50,
                forecast_p90,
                selected_model,
                is_cold_start,
                backtest_wape,
                backtest_bias,
                confidence_score
            )
            VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s
            )
            """,
            forecast_rows,
        )

        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ml.model_runs
                SET status = 'completed',
                    finished_at = now()
                WHERE id = %s
                """,
                (model_run_id,),
            )

        connection.commit()

    except Exception as exc:
        connection.rollback()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ml.model_runs
                SET status = 'failed',
                    finished_at = now(),
                    error_message = %s
                WHERE id = %s
                """,
                (
                    str(exc)[:4000],
                    model_run_id,
                ),
            )

        connection.commit()
        raise

    return CalibrationSummary(
        model_run_id=model_run_id,
        feature_run_id=feature_run_id,
        parent_hybrid_run_id=(
            parent_hybrid_run_id
        ),
        forecast_count=len(forecast_rows),
        metric_count=len(metric_records),
        calibration_prediction_count=int(
            len(calibration)
        ),
        holdout_prediction_count=int(
            len(holdout)
        ),
        overall_holdout_coverage=(
            overall_holdout_coverage
        ),
        minimum_horizon_coverage=(
            minimum_horizon_coverage
        ),
        minimum_cell_coverage=(
            minimum_cell_coverage
        ),
        inventory_risk_ready=(
            inventory_risk_ready
        ),
        calibration_origins=tuple(
            origin.date().isoformat()
            for origin in calibration_origins
        ),
        holdout_origins=tuple(
            origin.date().isoformat()
            for origin in holdout_origins
        ),
    )
