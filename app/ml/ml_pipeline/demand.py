from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import numpy as np
import pandas as pd
from psycopg import Connection
from psycopg.types.json import Jsonb

from ml_pipeline.config import MODEL_VERSION
from ml_pipeline.db import execute_many, query_frame
from ml_pipeline.forecasting import (
    MODEL_NAMES,
    forecast_quantiles_from_backtest,
    select_baseline_model,
)


FORECAST_HORIZON_MONTHS = 3
MINIMUM_TRAINING_MONTHS = 24
MAXIMUM_BACKTEST_MONTHS = 12


@dataclass(frozen=True)
class DemandOutputs:
    metrics: tuple[dict[str, Any], ...]
    forecasts: tuple[dict[str, Any], ...]
    selected_model_counts: dict[str, int]
    median_selected_wape: float | None


@dataclass(frozen=True)
class DemandTrainingSummary:
    model_run_id: UUID
    feature_run_id: UUID
    product_count: int
    forecast_count: int
    metric_count: int
    training_cutoff: str
    selected_model_counts: dict[str, int]
    median_selected_wape: float | None


def _confidence_score(
    wape: float | None,
    bias: float | None,
) -> float:
    if wape is None or bias is None:
        return 0.35

    if not np.isfinite(wape) or not np.isfinite(bias):
        return 0.20

    score = 1.0 / (
        1.0
        + max(float(wape), 0.0)
        + abs(float(bias))
    )

    return float(np.clip(score, 0.05, 0.99))


def _cold_start_points(
    features: pd.DataFrame,
) -> dict[str, float]:
    latest_month = features["month_start"].max()

    recent_start = (
        latest_month
        - pd.DateOffset(months=5)
    )

    recent = features[
        features["month_start"] >= recent_start
    ]

    recent_means = (
        recent.groupby(
            "product_id",
            as_index=False,
            observed=True,
        )["units_sold"]
        .mean()
        .rename(
            columns={
                "units_sold": "recent_mean_demand"
            }
        )
    )

    latest = (
        features[
            features["month_start"] == latest_month
        ][
            [
                "product_id",
                "category",
                "is_cold_start",
            ]
        ]
        .drop_duplicates("product_id")
        .merge(
            recent_means,
            on="product_id",
            how="left",
            validate="one_to_one",
        )
    )

    latest["recent_mean_demand"] = (
        pd.to_numeric(
            latest["recent_mean_demand"],
            errors="coerce",
        ).fillna(0.0)
    )

    established = latest[
        ~latest["is_cold_start"].astype(bool)
    ]

    global_fallback = (
        float(
            established["recent_mean_demand"].median()
        )
        if not established.empty
        else 0.0
    )

    points: dict[str, float] = {}

    for row in latest.itertuples(index=False):
        category_peers = established[
            established["category"] == row.category
        ]

        if not category_peers.empty:
            point = float(
                category_peers[
                    "recent_mean_demand"
                ].median()
            )
        else:
            point = global_fallback

        points[str(row.product_id)] = max(
            point,
            0.0,
        )

    return points


def _validate_feature_frame(
    features: pd.DataFrame,
) -> None:
    required = {
        "product_id",
        "month_start",
        "category",
        "units_sold",
        "is_cold_start",
    }

    missing = required - set(features.columns)

    if missing:
        raise ValueError(
            "Demand features are missing columns: "
            + ", ".join(sorted(missing))
        )

    if features.empty:
        raise ValueError(
            "Demand feature dataset is empty."
        )

    duplicate_count = int(
        features.duplicated(
            ["product_id", "month_start"]
        ).sum()
    )

    if duplicate_count:
        raise ValueError(
            "Duplicate product-month rows detected: "
            f"{duplicate_count}"
        )

    month_counts = features.groupby(
        "product_id",
        observed=True,
    )["month_start"].nunique()

    insufficient = month_counts[
        month_counts <= MINIMUM_TRAINING_MONTHS
    ]

    if not insufficient.empty:
        raise ValueError(
            "Products with insufficient history: "
            f"{len(insufficient)}"
        )


def build_demand_outputs(
    features: pd.DataFrame,
) -> DemandOutputs:
    frame = features.copy()

    frame["product_id"] = (
        frame["product_id"].astype(str)
    )

    frame["category"] = (
        frame["category"].astype(str)
    )

    frame["month_start"] = pd.to_datetime(
        frame["month_start"]
    )

    frame["units_sold"] = pd.to_numeric(
        frame["units_sold"],
        errors="coerce",
    ).fillna(0.0)

    frame["units_sold"] = frame[
        "units_sold"
    ].clip(lower=0.0)

    frame["is_cold_start"] = frame[
        "is_cold_start"
    ].astype(bool)

    frame = frame.sort_values(
        ["product_id", "month_start"],
        kind="stable",
    ).reset_index(drop=True)

    _validate_feature_frame(frame)

    cutoff = frame["month_start"].max()
    cold_start_points = _cold_start_points(frame)

    metric_records: list[dict[str, Any]] = []
    forecast_records: list[dict[str, Any]] = []

    aggregate_metrics: dict[
        tuple[str, str],
        list[tuple[float, int]],
    ] = defaultdict(list)

    selected_counts: Counter[str] = Counter()
    selected_wapes: list[float] = []

    for product_id, product_frame in frame.groupby(
        "product_id",
        sort=True,
        observed=True,
    ):
        product_frame = product_frame.sort_values(
            "month_start",
            kind="stable",
        )

        history = product_frame[
            "units_sold"
        ].to_numpy(dtype=float)

        latest = product_frame.iloc[-1]
        is_cold_start = bool(
            latest["is_cold_start"]
        )

        if is_cold_start:
            selected_model = "category_cold_start"
            point = cold_start_points.get(
                str(product_id),
                0.0,
            )

            selected_wape = None
            selected_bias = None
            recursive_point = point
        else:
            selection = select_baseline_model(
                history,
                minimum_training_months=(
                    MINIMUM_TRAINING_MONTHS
                ),
                maximum_test_months=(
                    MAXIMUM_BACKTEST_MONTHS
                ),
            )

            selected_model = (
                selection.selected_model
            )

            selected_metric = next(
                metric
                for metric in selection.metrics
                if metric.model_name
                == selected_model
            )

            selected_wape = float(
                selected_metric.wape
            )

            selected_bias = float(
                selected_metric.bias
            )

            selected_wapes.append(
                selected_wape
            )

            for metric in selection.metrics:
                for metric_name, metric_value in (
                    ("mae", metric.mae),
                    ("wape", metric.wape),
                    ("bias", metric.bias),
                ):
                    metric_value = float(
                        metric_value
                    )

                    sample_size = int(
                        metric.sample_size
                    )

                    metric_records.append(
                        {
                            "product_id": str(
                                product_id
                            ),
                            "model_name": (
                                metric.model_name
                            ),
                            "horizon": 1,
                            "metric_name": (
                                metric_name
                            ),
                            "metric_value": (
                                metric_value
                            ),
                            "sample_size": (
                                sample_size
                            ),
                            "fold_count": (
                                sample_size
                            ),
                        }
                    )

                    aggregate_metrics[
                        (
                            metric.model_name,
                            metric_name,
                        )
                    ].append(
                        (
                            metric_value,
                            sample_size,
                        )
                    )

            recursive_point = None

        selected_counts[selected_model] += 1

        recursive_history = history.copy()

        for horizon in range(
            1,
            FORECAST_HORIZON_MONTHS + 1,
        ):
            if is_cold_start:
                p50 = float(recursive_point or 0.0)
                p10 = max(p50 * 0.50, 0.0)
                p90 = max(p50 * 1.50, p50)
            else:
                p10, p50, p90 = (
                    forecast_quantiles_from_backtest(
                        recursive_history,
                        selected_model,
                    )
                )

                recursive_history = np.append(
                    recursive_history,
                    p50,
                )

            base_confidence = _confidence_score(
                selected_wape,
                selected_bias,
            )

            confidence = float(
                np.clip(
                    base_confidence
                    - 0.05 * (horizon - 1),
                    0.05,
                    0.99,
                )
            )

            forecast_month = (
                cutoff
                + pd.offsets.MonthBegin(horizon)
            )

            forecast_records.append(
                {
                    "product_id": str(
                        product_id
                    ),
                    "forecast_month": (
                        forecast_month.date()
                    ),
                    "horizon": horizon,
                    "forecast_p10": float(p10),
                    "forecast_p50": float(p50),
                    "forecast_p90": float(p90),
                    "selected_model": (
                        selected_model
                    ),
                    "is_cold_start": (
                        is_cold_start
                    ),
                    "backtest_wape": (
                        selected_wape
                    ),
                    "backtest_bias": (
                        selected_bias
                    ),
                    "confidence_score": (
                        confidence
                    ),
                }
            )

    for (
        model_name,
        metric_name,
    ), values in sorted(
        aggregate_metrics.items()
    ):
        weights = np.asarray(
            [sample_size for _, sample_size in values],
            dtype=float,
        )

        metric_values = np.asarray(
            [value for value, _ in values],
            dtype=float,
        )

        total_samples = int(weights.sum())

        aggregate_value = float(
            np.average(
                metric_values,
                weights=weights,
            )
        )

        metric_records.append(
            {
                "product_id": None,
                "model_name": model_name,
                "horizon": 1,
                "metric_name": metric_name,
                "metric_value": aggregate_value,
                "sample_size": total_samples,
                "fold_count": total_samples,
            }
        )

    median_wape = (
        float(np.median(selected_wapes))
        if selected_wapes
        else None
    )

    return DemandOutputs(
        metrics=tuple(metric_records),
        forecasts=tuple(forecast_records),
        selected_model_counts=dict(
            sorted(selected_counts.items())
        ),
        median_selected_wape=median_wape,
    )


def _latest_feature_context(
    connection: Connection[Any],
) -> tuple[UUID, str, Any, str, pd.DataFrame]:
    context = query_frame(
        connection,
        """
        SELECT
            id,
            feature_version,
            source_max_month,
            dataset_fingerprint
        FROM ml.feature_runs
        WHERE status = 'completed'
        ORDER BY finished_at DESC
        LIMIT 1
        """,
    )

    if context.empty:
        raise RuntimeError(
            "No completed feature run is available."
        )

    row = context.iloc[0]

    feature_run_id = row["id"]
    feature_version = str(
        row["feature_version"]
    )

    training_cutoff = row[
        "source_max_month"
    ]

    fingerprint = str(
        row["dataset_fingerprint"]
    )

    features = query_frame(
        connection,
        """
        SELECT
            product_id,
            month_start,
            category,
            units_sold,
            is_cold_start
        FROM ml.product_monthly_features
        WHERE feature_run_id = %s
        ORDER BY product_id, month_start
        """,
        (feature_run_id,),
    )

    return (
        feature_run_id,
        feature_version,
        training_cutoff,
        fingerprint,
        features,
    )


def run_demand_training(
    connection: Connection[Any],
    *,
    code_commit: str | None,
) -> DemandTrainingSummary:
    (
        feature_run_id,
        feature_version,
        training_cutoff,
        fingerprint,
        features,
    ) = _latest_feature_context(connection)

    model_run_id = uuid4()

    parameters = {
        "candidate_models": list(MODEL_NAMES),
        "cold_start_model": (
            "category_peer_median_last_6_months"
        ),
        "minimum_training_months": (
            MINIMUM_TRAINING_MONTHS
        ),
        "maximum_backtest_months": (
            MAXIMUM_BACKTEST_MONTHS
        ),
        "forecast_horizon_months": (
            FORECAST_HORIZON_MONTHS
        ),
        "interval_method": (
            "rolling_backtest_residual_quantiles"
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
                'baseline_ensemble',
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s
            )
            """,
            (
                model_run_id,
                feature_run_id,
                MODEL_VERSION,
                training_cutoff,
                FORECAST_HORIZON_MONTHS,
                feature_version,
                code_commit,
                fingerprint,
                Jsonb(parameters),
            ),
        )

    connection.commit()

    try:
        outputs = build_demand_outputs(features)

        metric_rows = [
            (
                uuid4(),
                model_run_id,
                metric["product_id"],
                metric["model_name"],
                metric["horizon"],
                metric["metric_name"],
                metric["metric_value"],
                metric["sample_size"],
                metric["fold_count"],
            )
            for metric in outputs.metrics
        ]

        forecast_rows = [
            (
                model_run_id,
                forecast["product_id"],
                forecast["forecast_month"],
                forecast["horizon"],
                forecast["forecast_p10"],
                forecast["forecast_p50"],
                forecast["forecast_p90"],
                forecast["selected_model"],
                forecast["is_cold_start"],
                forecast["backtest_wape"],
                forecast["backtest_bias"],
                forecast["confidence_score"],
            )
            for forecast in outputs.forecasts
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

    return DemandTrainingSummary(
        model_run_id=model_run_id,
        feature_run_id=feature_run_id,
        product_count=int(
            features["product_id"].nunique()
        ),
        forecast_count=len(outputs.forecasts),
        metric_count=len(outputs.metrics),
        training_cutoff=str(training_cutoff),
        selected_model_counts=(
            outputs.selected_model_counts
        ),
        median_selected_wape=(
            outputs.median_selected_wape
        ),
    )
