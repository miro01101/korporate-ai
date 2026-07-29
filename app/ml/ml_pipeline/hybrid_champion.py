from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import numpy as np
import pandas as pd
from psycopg import Connection
from psycopg.types.json import Jsonb

from ml_pipeline.db import execute_many, query_frame


MODEL_FAMILY = "hybrid_champion"
MODEL_VERSION = "inventory-hybrid-v1"
DEFAULT_SELECTION_MARGIN = 0.03


@dataclass(frozen=True)
class HybridOutputs:
    selection: pd.DataFrame
    forecasts: tuple[dict[str, Any], ...]
    metrics: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class HybridSummary:
    model_run_id: UUID
    feature_run_id: UUID
    baseline_run_id: UUID
    challenger_run_id: UUID
    product_count: int
    baseline_product_count: int
    lightgbm_product_count: int
    cold_start_product_count: int
    forecast_count: int
    metric_count: int
    baseline_median_wape: float
    hybrid_median_wape: float
    selection_margin: float


def select_product_models(
    baseline: pd.DataFrame,
    challenger: pd.DataFrame,
    *,
    selection_margin: float = DEFAULT_SELECTION_MARGIN,
) -> pd.DataFrame:
    required_baseline = {
        "product_id",
        "baseline_selected_model",
        "is_cold_start",
        "baseline_wape",
        "baseline_bias",
    }

    required_challenger = {
        "product_id",
        "challenger_wape",
        "challenger_bias",
    }

    missing_baseline = (
        required_baseline - set(baseline.columns)
    )

    missing_challenger = (
        required_challenger - set(challenger.columns)
    )

    if missing_baseline:
        raise ValueError(
            "Missing baseline columns: "
            + ", ".join(sorted(missing_baseline))
        )

    if missing_challenger:
        raise ValueError(
            "Missing challenger columns: "
            + ", ".join(sorted(missing_challenger))
        )

    if not 0.0 <= selection_margin < 1.0:
        raise ValueError(
            "selection_margin must be in [0, 1)."
        )

    baseline_frame = baseline.copy()
    challenger_frame = challenger.copy()

    baseline_frame["product_id"] = (
        baseline_frame["product_id"].astype(str)
    )

    challenger_frame["product_id"] = (
        challenger_frame["product_id"].astype(str)
    )

    for column in (
        "baseline_wape",
        "baseline_bias",
    ):
        baseline_frame[column] = pd.to_numeric(
            baseline_frame[column],
            errors="coerce",
        )

    for column in (
        "challenger_wape",
        "challenger_bias",
    ):
        challenger_frame[column] = pd.to_numeric(
            challenger_frame[column],
            errors="coerce",
        )

    baseline_frame["is_cold_start"] = (
        baseline_frame["is_cold_start"].astype(bool)
    )

    merged = baseline_frame.merge(
        challenger_frame,
        on="product_id",
        how="left",
        validate="one_to_one",
    )

    comparable = (
        ~merged["is_cold_start"]
        & merged["baseline_wape"].notna()
        & merged["challenger_wape"].notna()
    )

    challenger_wins = (
        comparable
        & (
            merged["challenger_wape"]
            <= merged["baseline_wape"]
            * (1.0 - selection_margin)
        )
    )

    merged["source_family"] = np.where(
        challenger_wins,
        "global_lightgbm_quantile",
        "baseline_ensemble",
    )

    merged["selected_model"] = np.where(
        challenger_wins,
        "lightgbm_quantile",
        merged["baseline_selected_model"],
    )

    merged["selected_wape"] = np.where(
        challenger_wins,
        merged["challenger_wape"],
        merged["baseline_wape"],
    )

    merged["selected_bias"] = np.where(
        challenger_wins,
        merged["challenger_bias"],
        merged["baseline_bias"],
    )

    merged["selection_reason"] = np.select(
        [
            merged["is_cold_start"],
            challenger_wins,
            comparable,
        ],
        [
            "cold_start_baseline",
            "lightgbm_wins_margin",
            "baseline_not_beaten",
        ],
        default="baseline_missing_comparison",
    )

    merged["challenger_selected"] = challenger_wins

    return merged.sort_values(
        "product_id",
        kind="stable",
    ).reset_index(drop=True)


def build_hybrid_outputs(
    baseline_products: pd.DataFrame,
    challenger_metrics: pd.DataFrame,
    baseline_forecasts: pd.DataFrame,
    challenger_forecasts: pd.DataFrame,
    *,
    selection_margin: float = DEFAULT_SELECTION_MARGIN,
) -> HybridOutputs:
    selection = select_product_models(
        baseline_products,
        challenger_metrics,
        selection_margin=selection_margin,
    )

    baseline_ids = set(
        selection.loc[
            ~selection["challenger_selected"],
            "product_id",
        ].astype(str)
    )

    challenger_ids = set(
        selection.loc[
            selection["challenger_selected"],
            "product_id",
        ].astype(str)
    )

    baseline_frame = baseline_forecasts.copy()
    challenger_frame = challenger_forecasts.copy()

    baseline_frame["product_id"] = (
        baseline_frame["product_id"].astype(str)
    )

    challenger_frame["product_id"] = (
        challenger_frame["product_id"].astype(str)
    )

    selected_forecasts = pd.concat(
        [
            baseline_frame[
                baseline_frame["product_id"].isin(
                    baseline_ids
                )
            ],
            challenger_frame[
                challenger_frame["product_id"].isin(
                    challenger_ids
                )
            ],
        ],
        ignore_index=True,
    )

    selected_forecasts = selected_forecasts.sort_values(
        ["product_id", "forecast_month"],
        kind="stable",
    ).reset_index(drop=True)

    if len(selected_forecasts) != 240:
        raise ValueError(
            "Hybrid forecast must contain 240 rows; "
            f"found {len(selected_forecasts)}."
        )

    if (
        selected_forecasts["product_id"].nunique()
        != 80
    ):
        raise ValueError(
            "Hybrid forecast must contain 80 products."
        )

    if selected_forecasts.duplicated(
        ["product_id", "forecast_month"]
    ).any():
        raise ValueError(
            "Duplicate hybrid product-month forecasts."
        )

    forecast_columns = (
        "product_id",
        "forecast_month",
        "horizon",
        "forecast_p10",
        "forecast_p50",
        "forecast_p90",
        "selected_model",
        "is_cold_start",
        "backtest_wape",
        "backtest_bias",
        "confidence_score",
    )

    forecasts = tuple(
        dict(zip(
            forecast_columns,
            values,
            strict=True,
        ))
        for values in selected_forecasts[
            list(forecast_columns)
        ].itertuples(index=False, name=None)
    )

    metrics: list[dict[str, Any]] = []

    comparable_selection = selection.dropna(
        subset=["selected_wape"]
    )

    for row in comparable_selection.itertuples(
        index=False
    ):
        for metric_name, metric_value in (
            ("wape", row.selected_wape),
            ("bias", row.selected_bias),
        ):
            if pd.isna(metric_value):
                continue

            metrics.append(
                {
                    "product_id": str(row.product_id),
                    "model_name": str(
                        row.selected_model
                    ),
                    "horizon": 1,
                    "metric_name": metric_name,
                    "metric_value": float(
                        metric_value
                    ),
                    "sample_size": 12,
                    "fold_count": 12,
                }
            )

    selected_wape = comparable_selection[
        "selected_wape"
    ].astype(float)

    product_count = int(len(selection))

    lightgbm_count = int(
        selection["challenger_selected"].sum()
    )

    baseline_count = (
        product_count - lightgbm_count
    )

    cold_start_count = int(
        selection["is_cold_start"].sum()
    )

    aggregate_metrics = (
        (
            "median_product_wape",
            float(selected_wape.median()),
            len(selected_wape),
        ),
        (
            "mean_product_wape",
            float(selected_wape.mean()),
            len(selected_wape),
        ),
        (
            "lightgbm_product_share",
            lightgbm_count / product_count,
            product_count,
        ),
        (
            "baseline_product_share",
            baseline_count / product_count,
            product_count,
        ),
        (
            "cold_start_product_share",
            cold_start_count / product_count,
            product_count,
        ),
    )

    for (
        metric_name,
        metric_value,
        sample_size,
    ) in aggregate_metrics:
        metrics.append(
            {
                "product_id": None,
                "model_name": MODEL_FAMILY,
                "horizon": 1,
                "metric_name": metric_name,
                "metric_value": float(metric_value),
                "sample_size": int(sample_size),
                "fold_count": 12,
            }
        )

    return HybridOutputs(
        selection=selection,
        forecasts=forecasts,
        metrics=tuple(metrics),
    )


def _load_context(
    connection: Connection[Any],
) -> tuple[
    UUID,
    str,
    Any,
    str,
    UUID,
    UUID,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    features = query_frame(
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

    if features.empty:
        raise RuntimeError(
            "No completed feature run exists."
        )

    feature_row = features.iloc[0]
    feature_run_id = feature_row["id"]

    runs = query_frame(
        connection,
        """
        SELECT
            id,
            model_family
        FROM ml.model_runs
        WHERE feature_run_id = %s
          AND status = 'completed'
          AND model_family IN (
              'baseline_ensemble',
              'global_lightgbm_quantile'
          )
        ORDER BY finished_at DESC
        """,
        (feature_run_id,),
    )

    baseline_rows = runs[
        runs["model_family"]
        == "baseline_ensemble"
    ]

    challenger_rows = runs[
        runs["model_family"]
        == "global_lightgbm_quantile"
    ]

    if baseline_rows.empty:
        raise RuntimeError(
            "No completed baseline run exists."
        )

    if challenger_rows.empty:
        raise RuntimeError(
            "No completed LightGBM run exists."
        )

    baseline_run_id = baseline_rows.iloc[0]["id"]
    challenger_run_id = (
        challenger_rows.iloc[0]["id"]
    )

    baseline_products = query_frame(
        connection,
        """
        SELECT
            product_id,
            selected_model AS baseline_selected_model,
            is_cold_start,
            backtest_wape AS baseline_wape,
            backtest_bias AS baseline_bias
        FROM ml.forecasts
        WHERE model_run_id = %s
          AND horizon = 1
        ORDER BY product_id
        """,
        (baseline_run_id,),
    )

    challenger_metrics = query_frame(
        connection,
        """
        SELECT
            product_id,
            max(metric_value) FILTER (
                WHERE metric_name = 'wape'
            ) AS challenger_wape,
            max(metric_value) FILTER (
                WHERE metric_name = 'bias'
            ) AS challenger_bias
        FROM ml.model_metrics
        WHERE model_run_id = %s
          AND product_id IS NOT NULL
        GROUP BY product_id
        ORDER BY product_id
        """,
        (challenger_run_id,),
    )

    forecast_query = """
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
            confidence_score
        FROM ml.forecasts
        WHERE model_run_id = %s
        ORDER BY product_id, forecast_month
    """

    baseline_forecasts = query_frame(
        connection,
        forecast_query,
        (baseline_run_id,),
    )

    challenger_forecasts = query_frame(
        connection,
        forecast_query,
        (challenger_run_id,),
    )

    return (
        feature_run_id,
        str(feature_row["feature_version"]),
        feature_row["source_max_month"],
        str(feature_row["dataset_fingerprint"]),
        baseline_run_id,
        challenger_run_id,
        baseline_products,
        challenger_metrics,
        baseline_forecasts,
        challenger_forecasts,
    )


def run_hybrid_selection(
    connection: Connection[Any],
    *,
    code_commit: str | None,
    selection_margin: float = DEFAULT_SELECTION_MARGIN,
) -> HybridSummary:
    (
        feature_run_id,
        feature_version,
        training_cutoff,
        fingerprint,
        baseline_run_id,
        challenger_run_id,
        baseline_products,
        challenger_metrics,
        baseline_forecasts,
        challenger_forecasts,
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
            "A completed hybrid run already exists "
            "for this feature run and commit."
        )

    outputs = build_hybrid_outputs(
        baseline_products,
        challenger_metrics,
        baseline_forecasts,
        challenger_forecasts,
        selection_margin=selection_margin,
    )

    selection = outputs.selection

    lightgbm_products = (
        selection.loc[
            selection["challenger_selected"],
            "product_id",
        ]
        .astype(str)
        .tolist()
    )

    baseline_products_selected = (
        selection.loc[
            ~selection["challenger_selected"],
            "product_id",
        ]
        .astype(str)
        .tolist()
    )

    baseline_median_wape = float(
        selection["baseline_wape"]
        .dropna()
        .astype(float)
        .median()
    )

    hybrid_median_wape = float(
        selection["selected_wape"]
        .dropna()
        .astype(float)
        .median()
    )

    model_run_id = uuid4()

    parameters = {
        "selection_margin": selection_margin,
        "baseline_run_id": str(baseline_run_id),
        "challenger_run_id": str(
            challenger_run_id
        ),
        "lightgbm_products": lightgbm_products,
        "baseline_products": (
            baseline_products_selected
        ),
        "selection_counts": {
            "lightgbm": len(lightgbm_products),
            "baseline": len(
                baseline_products_selected
            ),
            "cold_start": int(
                selection["is_cold_start"].sum()
            ),
        },
        "point_forecast_ready": True,
        "inventory_risk_ready": False,
        "interval_calibration_required": True,
        "provisional_champion": True,
        "selection_bias_warning": (
            "Model selection and reported WAPE use "
            "the same rolling backtest period. "
            "Future monitoring is required."
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

    return HybridSummary(
        model_run_id=model_run_id,
        feature_run_id=feature_run_id,
        baseline_run_id=baseline_run_id,
        challenger_run_id=challenger_run_id,
        product_count=int(len(selection)),
        baseline_product_count=int(
            (~selection["challenger_selected"]).sum()
        ),
        lightgbm_product_count=int(
            selection["challenger_selected"].sum()
        ),
        cold_start_product_count=int(
            selection["is_cold_start"].sum()
        ),
        forecast_count=len(outputs.forecasts),
        metric_count=len(outputs.metrics),
        baseline_median_wape=baseline_median_wape,
        hybrid_median_wape=hybrid_median_wape,
        selection_margin=selection_margin,
    )
