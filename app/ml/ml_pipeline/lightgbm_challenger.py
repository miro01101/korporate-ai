from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
from typing import Any
from uuid import UUID, uuid4

import joblib
from lightgbm import LGBMRegressor
import numpy as np
import pandas as pd
from psycopg import Connection
from psycopg.types.json import Jsonb
from sklearn.metrics import mean_pinball_loss

from ml_pipeline.db import execute_many, query_frame


MODEL_FAMILY = "global_lightgbm_quantile"
MODEL_VERSION = "inventory-lightgbm-v1"

FORECAST_HORIZON_MONTHS = 3
BACKTEST_MONTHS = 12
PROMOTION_MARGIN = 0.03

QUANTILES = {
    "p10": 0.10,
    "p50": 0.50,
    "p90": 0.90,
}

CATEGORICAL_COLUMNS = (
    "product_id",
    "category",
    "supplier_id",
)

NUMERIC_COLUMNS = (
    "purchase_price",
    "sales_price",
    "margin_ratio",
    "lead_time_days",
    "minimum_order_quantity",
    "lag_1",
    "lag_2",
    "lag_3",
    "lag_6",
    "lag_12",
    "rolling_mean_3",
    "rolling_mean_6",
    "rolling_mean_12",
    "rolling_std_3",
    "rolling_std_6",
    "month_sin",
    "month_cos",
    "time_index",
)

MODEL_COLUMNS = (
    *CATEGORICAL_COLUMNS,
    *NUMERIC_COLUMNS,
)

DEFAULT_MODEL_PARAMETERS: dict[str, Any] = {
    "boosting_type": "gbdt",
    "n_estimators": 220,
    "learning_rate": 0.035,
    "num_leaves": 15,
    "max_depth": 5,
    "min_child_samples": 30,
    "subsample": 0.90,
    "subsample_freq": 1,
    "colsample_bytree": 0.90,
    "reg_alpha": 0.10,
    "reg_lambda": 1.00,
    "random_state": 20260729,
    "n_jobs": 2,
    "deterministic": True,
    "force_col_wise": True,
    "verbosity": -1,
}


@dataclass
class ChallengerOutputs:
    metrics: tuple[dict[str, Any], ...]
    forecasts: tuple[dict[str, Any], ...]
    models: dict[str, LGBMRegressor]
    category_levels: dict[str, list[str]]
    training_row_count: int
    backtest_row_count: int
    raw_crossing_rate: float


@dataclass(frozen=True)
class ChallengerSummary:
    model_run_id: UUID
    feature_run_id: UUID
    baseline_run_id: UUID
    product_count: int
    forecast_count: int
    metric_count: int
    training_cutoff: str
    training_row_count: int
    backtest_row_count: int
    raw_crossing_rate: float
    baseline_median_wape: float | None
    challenger_median_wape: float | None
    comparable_product_count: int
    improved_product_count: int
    promotion_recommended: bool
    artifact_path: str
    artifact_sha256: str


def _safe_wape(
    actual: np.ndarray,
    predicted: np.ndarray,
) -> float:
    denominator = float(np.sum(np.abs(actual)))
    errors = np.abs(predicted - actual)

    if denominator > 0:
        return float(np.sum(errors) / denominator)

    return float(np.mean(errors))


def _safe_bias(
    actual: np.ndarray,
    predicted: np.ndarray,
) -> float:
    denominator = float(np.sum(np.abs(actual)))
    errors = predicted - actual

    if denominator > 0:
        return float(np.sum(errors) / denominator)

    return float(np.mean(errors))


def _confidence_score(
    wape: float,
    bias: float,
    coverage: float,
    horizon: int,
) -> float:
    calibration_penalty = abs(coverage - 0.80)

    base = 1.0 / (
        1.0
        + max(wape, 0.0)
        + abs(bias)
        + calibration_penalty
    )

    score = base - 0.05 * (horizon - 1)

    return float(np.clip(score, 0.05, 0.99))


def _category_levels(
    frame: pd.DataFrame,
) -> dict[str, list[str]]:
    return {
        column: sorted(
            frame[column]
            .astype("string")
            .fillna("UNKNOWN")
            .astype(str)
            .unique()
            .tolist()
        )
        for column in CATEGORICAL_COLUMNS
    }


def _apply_categories(
    frame: pd.DataFrame,
    levels: dict[str, list[str]],
) -> pd.DataFrame:
    output = frame.copy()

    for column in CATEGORICAL_COLUMNS:
        values = (
            output[column]
            .astype("string")
            .fillna("UNKNOWN")
            .astype(str)
        )

        output[column] = pd.Categorical(
            values,
            categories=levels[column],
        )

    return output


def prepare_model_frame(
    features: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    frame = features.copy()

    required = {
        "product_id",
        "month_start",
        "category",
        "supplier_id",
        "purchase_price",
        "sales_price",
        "lead_time_days",
        "minimum_order_quantity",
        "units_sold",
        "lag_1",
        "lag_2",
        "lag_3",
        "lag_6",
        "lag_12",
        "rolling_mean_3",
        "rolling_mean_6",
        "rolling_mean_12",
        "rolling_std_3",
        "rolling_std_6",
        "is_cold_start",
    }

    missing = required - set(frame.columns)

    if missing:
        raise ValueError(
            "Missing challenger columns: "
            + ", ".join(sorted(missing))
        )

    if frame.empty:
        raise ValueError("Feature frame is empty.")

    duplicate_count = int(
        frame.duplicated(
            ["product_id", "month_start"]
        ).sum()
    )

    if duplicate_count:
        raise ValueError(
            f"Duplicate product-month rows: {duplicate_count}"
        )

    frame["month_start"] = pd.to_datetime(
        frame["month_start"]
    )

    frame["product_id"] = (
        frame["product_id"].astype(str)
    )

    frame["category"] = (
        frame["category"].astype(str)
    )

    frame["supplier_id"] = (
        frame["supplier_id"].astype(str)
    )

    numeric_source_columns = (
        "purchase_price",
        "sales_price",
        "lead_time_days",
        "minimum_order_quantity",
        "units_sold",
        "lag_1",
        "lag_2",
        "lag_3",
        "lag_6",
        "lag_12",
        "rolling_mean_3",
        "rolling_mean_6",
        "rolling_mean_12",
        "rolling_std_3",
        "rolling_std_6",
    )

    for column in numeric_source_columns:
        frame[column] = pd.to_numeric(
            frame[column],
            errors="coerce",
        )

    frame["units_sold"] = (
        frame["units_sold"]
        .fillna(0.0)
        .clip(lower=0.0)
    )

    frame["margin_ratio"] = np.where(
        frame["purchase_price"] > 0,
        frame["sales_price"]
        / frame["purchase_price"],
        1.0,
    )

    month_number = frame["month_start"].dt.month

    frame["month_sin"] = np.sin(
        2.0 * np.pi * month_number / 12.0
    )

    frame["month_cos"] = np.cos(
        2.0 * np.pi * month_number / 12.0
    )

    base_month = frame["month_start"].min()

    frame["time_index"] = (
        (
            frame["month_start"].dt.year
            - base_month.year
        )
        * 12
        + (
            frame["month_start"].dt.month
            - base_month.month
        )
    ).astype(float)

    frame["is_cold_start"] = (
        frame["is_cold_start"].astype(bool)
    )

    frame = frame.sort_values(
        ["month_start", "product_id"],
        kind="stable",
    ).reset_index(drop=True)

    levels = _category_levels(frame)
    frame = _apply_categories(frame, levels)

    return frame, levels


def _model_parameters(
    override: dict[str, Any] | None,
) -> dict[str, Any]:
    parameters = dict(DEFAULT_MODEL_PARAMETERS)

    if override:
        parameters.update(override)

    return parameters


def fit_quantile_models(
    training_frame: pd.DataFrame,
    *,
    model_parameters: dict[str, Any] | None = None,
) -> dict[str, LGBMRegressor]:
    valid = training_frame.dropna(
        subset=["lag_12"]
    ).copy()

    if valid.empty:
        raise ValueError(
            "No rows remain after the lag-12 guard."
        )

    X_train = valid[list(MODEL_COLUMNS)]
    y_train = valid["units_sold"].to_numpy(
        dtype=float
    )

    parameters = _model_parameters(
        model_parameters
    )

    models: dict[str, LGBMRegressor] = {}

    for label, alpha in QUANTILES.items():
        model = LGBMRegressor(
            objective="quantile",
            alpha=alpha,
            **parameters,
        )

        model.fit(
            X_train,
            y_train,
            categorical_feature=list(
                CATEGORICAL_COLUMNS
            ),
        )

        models[label] = model

    return models


def _predict_quantiles(
    models: dict[str, LGBMRegressor],
    frame: pd.DataFrame,
) -> tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
]:
    X = frame[list(MODEL_COLUMNS)]

    raw_p10 = np.asarray(
        models["p10"].predict(X),
        dtype=float,
    )

    raw_p50 = np.asarray(
        models["p50"].predict(X),
        dtype=float,
    )

    raw_p90 = np.asarray(
        models["p90"].predict(X),
        dtype=float,
    )

    raw_crossing = (
        (raw_p10 > raw_p50)
        | (raw_p50 > raw_p90)
    )

    p10 = np.minimum.reduce(
        [raw_p10, raw_p50, raw_p90]
    )

    p90 = np.maximum.reduce(
        [raw_p10, raw_p50, raw_p90]
    )

    p50 = np.clip(raw_p50, p10, p90)

    return (
        np.clip(p10, 0.0, None),
        np.clip(p50, 0.0, None),
        np.clip(p90, 0.0, None),
        raw_crossing,
    )


def _metric_records(
    predictions: pd.DataFrame,
) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []

    group_keys: list[str | None] = (
        sorted(
            predictions["product_id"]
            .astype(str)
            .unique()
            .tolist()
        )
        + [None]
    )

    for product_id in group_keys:
        if product_id is None:
            group = predictions
        else:
            group = predictions[
                predictions["product_id"]
                == product_id
            ]

        actual = group["actual"].to_numpy(
            dtype=float
        )

        p10 = group["p10"].to_numpy(
            dtype=float
        )

        p50 = group["p50"].to_numpy(
            dtype=float
        )

        p90 = group["p90"].to_numpy(
            dtype=float
        )

        mae = float(
            np.mean(np.abs(p50 - actual))
        )

        wape = _safe_wape(actual, p50)
        bias = _safe_bias(actual, p50)

        pinball_p10 = float(
            mean_pinball_loss(
                actual,
                p10,
                alpha=0.10,
            )
        )

        pinball_p50 = float(
            mean_pinball_loss(
                actual,
                p50,
                alpha=0.50,
            )
        )

        pinball_p90 = float(
            mean_pinball_loss(
                actual,
                p90,
                alpha=0.90,
            )
        )

        coverage = float(
            np.mean(
                (actual >= p10)
                & (actual <= p90)
            )
        )

        crossing_rate = float(
            group["raw_crossing"].mean()
        )

        sample_size = int(len(group))
        fold_count = int(
            group["month_start"].nunique()
        )

        for metric_name, metric_value in (
            ("mae", mae),
            ("wape", wape),
            ("bias", bias),
            ("pinball_p10", pinball_p10),
            ("pinball_p50", pinball_p50),
            ("pinball_p90", pinball_p90),
            ("coverage_80", coverage),
            (
                "raw_quantile_crossing_rate",
                crossing_rate,
            ),
        ):
            records.append(
                {
                    "product_id": product_id,
                    "model_name": (
                        "lightgbm_quantile"
                    ),
                    "horizon": 1,
                    "metric_name": metric_name,
                    "metric_value": metric_value,
                    "sample_size": sample_size,
                    "fold_count": fold_count,
                }
            )

    return tuple(records)


def _future_row(
    latest: pd.Series,
    history: list[float],
    forecast_month: pd.Timestamp,
    base_month: pd.Timestamp,
) -> dict[str, Any]:
    values = np.asarray(history, dtype=float)

    def lag(position: int) -> float:
        if len(values) < position:
            return 0.0

        return float(values[-position])

    def rolling_mean(window: int) -> float:
        if len(values) == 0:
            return 0.0

        return float(
            np.mean(values[-window:])
        )

    def rolling_std(window: int) -> float:
        if len(values) < 2:
            return 0.0

        return float(
            np.std(
                values[-window:],
                ddof=0,
            )
        )

    month_number = forecast_month.month

    purchase_price = float(
        latest["purchase_price"]
    )

    sales_price = float(
        latest["sales_price"]
    )

    margin_ratio = (
        sales_price / purchase_price
        if purchase_price > 0
        else 1.0
    )

    time_index = (
        (forecast_month.year - base_month.year)
        * 12
        + (
            forecast_month.month
            - base_month.month
        )
    )

    return {
        "product_id": str(latest["product_id"]),
        "category": str(latest["category"]),
        "supplier_id": str(
            latest["supplier_id"]
        ),
        "purchase_price": purchase_price,
        "sales_price": sales_price,
        "margin_ratio": margin_ratio,
        "lead_time_days": float(
            latest["lead_time_days"]
        ),
        "minimum_order_quantity": float(
            latest["minimum_order_quantity"]
        ),
        "lag_1": lag(1),
        "lag_2": lag(2),
        "lag_3": lag(3),
        "lag_6": lag(6),
        "lag_12": lag(12),
        "rolling_mean_3": rolling_mean(3),
        "rolling_mean_6": rolling_mean(6),
        "rolling_mean_12": rolling_mean(12),
        "rolling_std_3": rolling_std(3),
        "rolling_std_6": rolling_std(6),
        "month_sin": math.sin(
            2.0 * math.pi * month_number / 12.0
        ),
        "month_cos": math.cos(
            2.0 * math.pi * month_number / 12.0
        ),
        "time_index": float(time_index),
        "forecast_month": forecast_month,
        "is_cold_start": bool(
            latest["is_cold_start"]
        ),
    }


def build_challenger_outputs(
    features: pd.DataFrame,
    *,
    backtest_months: int = BACKTEST_MONTHS,
    model_parameters: dict[str, Any] | None = None,
) -> ChallengerOutputs:
    frame, levels = prepare_model_frame(
        features
    )

    unique_months = sorted(
        frame["month_start"].unique()
    )

    if len(unique_months) <= backtest_months + 12:
        raise ValueError(
            "Insufficient months for challenger backtest."
        )

    test_months = unique_months[
        -backtest_months:
    ]

    prediction_frames: list[pd.DataFrame] = []

    for test_month in test_months:
        training = frame[
            frame["month_start"] < test_month
        ].copy()

        test = frame[
            frame["month_start"] == test_month
        ].copy()

        if test.empty:
            raise ValueError(
                f"Missing test month: {test_month}"
            )

        models = fit_quantile_models(
            training,
            model_parameters=model_parameters,
        )

        p10, p50, p90, crossing = (
            _predict_quantiles(models, test)
        )

        prediction_frames.append(
            pd.DataFrame(
                {
                    "product_id": (
                        test["product_id"]
                        .astype(str)
                        .to_numpy()
                    ),
                    "month_start": (
                        test["month_start"]
                        .to_numpy()
                    ),
                    "actual": (
                        test["units_sold"]
                        .to_numpy(dtype=float)
                    ),
                    "p10": p10,
                    "p50": p50,
                    "p90": p90,
                    "raw_crossing": crossing,
                }
            )
        )

    backtest_predictions = pd.concat(
        prediction_frames,
        ignore_index=True,
    )

    metrics = _metric_records(
        backtest_predictions
    )

    final_models = fit_quantile_models(
        frame,
        model_parameters=model_parameters,
    )

    cutoff = frame["month_start"].max()
    base_month = frame["month_start"].min()

    latest_rows = (
        frame.sort_values(
            ["product_id", "month_start"],
            kind="stable",
        )
        .groupby(
            "product_id",
            observed=True,
            sort=True,
        )
        .tail(1)
        .copy()
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
        in frame.groupby(
            "product_id",
            observed=True,
            sort=True,
        )
    }

    metric_lookup: dict[
        tuple[str, str],
        float,
    ] = {
        (
            str(metric["product_id"]),
            str(metric["metric_name"]),
        ): float(metric["metric_value"])
        for metric in metrics
        if metric["product_id"] is not None
    }

    forecast_records: list[dict[str, Any]] = []

    for horizon in range(
        1,
        FORECAST_HORIZON_MONTHS + 1,
    ):
        forecast_month = (
            cutoff
            + pd.offsets.MonthBegin(horizon)
        )

        future_rows = []

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
                    forecast_month,
                    base_month,
                )
            )

        future = pd.DataFrame(future_rows)
        future = _apply_categories(
            future,
            levels,
        )

        p10, p50, p90, _ = _predict_quantiles(
            final_models,
            future,
        )

        for position, row in enumerate(
            future.itertuples(index=False)
        ):
            product_id = str(row.product_id)

            wape = metric_lookup[
                (product_id, "wape")
            ]

            bias = metric_lookup[
                (product_id, "bias")
            ]

            coverage = metric_lookup[
                (product_id, "coverage_80")
            ]

            confidence = _confidence_score(
                wape,
                bias,
                coverage,
                horizon,
            )

            forecast_records.append(
                {
                    "product_id": product_id,
                    "forecast_month": (
                        forecast_month.date()
                    ),
                    "horizon": horizon,
                    "forecast_p10": float(
                        p10[position]
                    ),
                    "forecast_p50": float(
                        p50[position]
                    ),
                    "forecast_p90": float(
                        p90[position]
                    ),
                    "selected_model": (
                        "lightgbm_quantile"
                    ),
                    "is_cold_start": bool(
                        row.is_cold_start
                    ),
                    "backtest_wape": wape,
                    "backtest_bias": bias,
                    "confidence_score": confidence,
                }
            )

            histories[product_id].append(
                float(p50[position])
            )

    return ChallengerOutputs(
        metrics=metrics,
        forecasts=tuple(forecast_records),
        models=final_models,
        category_levels=levels,
        training_row_count=int(
            frame["lag_12"].notna().sum()
        ),
        backtest_row_count=int(
            len(backtest_predictions)
        ),
        raw_crossing_rate=float(
            backtest_predictions[
                "raw_crossing"
            ].mean()
        ),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(
            lambda: file.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def write_artifacts(
    *,
    model_run_id: UUID,
    outputs: ChallengerOutputs,
    artifact_root: Path,
    parameters: dict[str, Any],
) -> tuple[str, str]:
    run_directory = artifact_root / str(
        model_run_id
    )

    run_directory.mkdir(
        parents=True,
        exist_ok=False,
    )

    model_files: dict[str, str] = {}

    try:
        for label, model in outputs.models.items():
            path = run_directory / (
                f"lightgbm_{label}.joblib"
            )

            temporary = path.with_suffix(
                ".joblib.tmp"
            )

            joblib.dump(model, temporary)
            temporary.replace(path)

            model_files[path.name] = (
                _sha256_file(path)
            )

        bundle_digest = hashlib.sha256()

        for name, checksum in sorted(
            model_files.items()
        ):
            bundle_digest.update(
                f"{name}:{checksum}\n".encode(
                    "utf-8"
                )
            )

        bundle_sha256 = (
            bundle_digest.hexdigest()
        )

        manifest = {
            "model_run_id": str(model_run_id),
            "model_family": MODEL_FAMILY,
            "model_version": MODEL_VERSION,
            "model_columns": list(
                MODEL_COLUMNS
            ),
            "categorical_columns": list(
                CATEGORICAL_COLUMNS
            ),
            "category_levels": (
                outputs.category_levels
            ),
            "parameters": parameters,
            "model_files": model_files,
            "bundle_sha256": bundle_sha256,
        }

        manifest_path = (
            run_directory / "manifest.json"
        )

        temporary_manifest = (
            run_directory / "manifest.json.tmp"
        )

        temporary_manifest.write_text(
            json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        temporary_manifest.replace(
            manifest_path
        )

    except Exception:
        shutil.rmtree(
            run_directory,
            ignore_errors=True,
        )

        raise

    return (
        str(run_directory),
        bundle_sha256,
    )


def _latest_feature_context(
    connection: Connection[Any],
) -> tuple[
    UUID,
    str,
    date,
    str,
    pd.DataFrame,
]:
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
            "No completed feature run exists."
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

    return (
        feature_run_id,
        feature_version,
        training_cutoff,
        fingerprint,
        features,
    )


def _baseline_context(
    connection: Connection[Any],
    feature_run_id: UUID,
) -> tuple[UUID, pd.DataFrame]:
    baseline = query_frame(
        connection,
        """
        SELECT id
        FROM ml.model_runs
        WHERE feature_run_id = %s
          AND model_family = 'baseline_ensemble'
          AND status = 'completed'
        ORDER BY finished_at DESC
        LIMIT 1
        """,
        (feature_run_id,),
    )

    if baseline.empty:
        raise RuntimeError(
            "No completed baseline run exists."
        )

    baseline_run_id = baseline.iloc[0]["id"]

    baseline_metrics = query_frame(
        connection,
        """
        SELECT
            product_id,
            max(backtest_wape) AS baseline_wape
        FROM ml.forecasts
        WHERE model_run_id = %s
          AND horizon = 1
        GROUP BY product_id
        ORDER BY product_id
        """,
        (baseline_run_id,),
    )

    return baseline_run_id, baseline_metrics


def run_lightgbm_training(
    connection: Connection[Any],
    *,
    code_commit: str | None,
    artifact_root: Path,
) -> ChallengerSummary:
    (
        feature_run_id,
        feature_version,
        training_cutoff,
        fingerprint,
        features,
    ) = _latest_feature_context(connection)

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
            "A completed challenger already exists "
            "for this feature run and commit."
        )

    (
        baseline_run_id,
        baseline_metrics,
    ) = _baseline_context(
        connection,
        feature_run_id,
    )

    model_run_id = uuid4()

    parameters = {
        "quantiles": QUANTILES,
        "model_parameters": (
            DEFAULT_MODEL_PARAMETERS
        ),
        "backtest_months": BACKTEST_MONTHS,
        "forecast_horizon_months": (
            FORECAST_HORIZON_MONTHS
        ),
        "promotion_margin": (
            PROMOTION_MARGIN
        ),
        "baseline_run_id": str(
            baseline_run_id
        ),
        "safe_feature_columns": list(
            MODEL_COLUMNS
        ),
        "excluded_current_target_features": [
            "zero_ratio_12",
            "demand_cv_12",
            "months_since_last_sale",
            "abc_class",
            "xyz_class",
        ],
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
                MODEL_FAMILY,
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

    artifact_path: str | None = None

    try:
        outputs = build_challenger_outputs(
            features
        )

        (
            artifact_path,
            artifact_sha256,
        ) = write_artifacts(
            model_run_id=model_run_id,
            outputs=outputs,
            artifact_root=artifact_root,
            parameters=parameters,
        )

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

        challenger_wape = pd.DataFrame(
            [
                {
                    "product_id": str(
                        metric["product_id"]
                    ),
                    "challenger_wape": float(
                        metric["metric_value"]
                    ),
                }
                for metric in outputs.metrics
                if metric["product_id"]
                is not None
                and metric["metric_name"]
                == "wape"
            ]
        )

        comparison = baseline_metrics.copy()

        comparison["product_id"] = (
            comparison["product_id"]
            .astype(str)
        )

        comparison["baseline_wape"] = (
            pd.to_numeric(
                comparison["baseline_wape"],
                errors="coerce",
            )
        )

        comparison = comparison.merge(
            challenger_wape,
            on="product_id",
            how="inner",
            validate="one_to_one",
        )

        comparison = comparison.dropna(
            subset=[
                "baseline_wape",
                "challenger_wape",
            ]
        )

        comparable_product_count = int(
            len(comparison)
        )

        improved_product_count = int(
            (
                comparison["challenger_wape"]
                <= comparison["baseline_wape"]
                * (1.0 - PROMOTION_MARGIN)
            ).sum()
        )

        baseline_median_wape = (
            float(
                comparison[
                    "baseline_wape"
                ].median()
            )
            if comparable_product_count
            else None
        )

        challenger_median_wape = (
            float(
                comparison[
                    "challenger_wape"
                ].median()
            )
            if comparable_product_count
            else None
        )

        required_wins = math.ceil(
            comparable_product_count / 2
        )

        promotion_recommended = bool(
            comparable_product_count > 0
            and challenger_median_wape
            is not None
            and baseline_median_wape
            is not None
            and challenger_median_wape
            <= baseline_median_wape
            * (1.0 - PROMOTION_MARGIN)
            and improved_product_count
            >= required_wins
        )

        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ml.model_runs
                SET status = 'completed',
                    finished_at = now(),
                    artifact_path = %s,
                    artifact_sha256 = %s
                WHERE id = %s
                """,
                (
                    artifact_path,
                    artifact_sha256,
                    model_run_id,
                ),
            )

        connection.commit()

    except Exception as exc:
        connection.rollback()

        if artifact_path:
            shutil.rmtree(
                artifact_path,
                ignore_errors=True,
            )

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

    return ChallengerSummary(
        model_run_id=model_run_id,
        feature_run_id=feature_run_id,
        baseline_run_id=baseline_run_id,
        product_count=int(
            features["product_id"].nunique()
        ),
        forecast_count=len(
            outputs.forecasts
        ),
        metric_count=len(outputs.metrics),
        training_cutoff=str(
            training_cutoff
        ),
        training_row_count=(
            outputs.training_row_count
        ),
        backtest_row_count=(
            outputs.backtest_row_count
        ),
        raw_crossing_rate=(
            outputs.raw_crossing_rate
        ),
        baseline_median_wape=(
            baseline_median_wape
        ),
        challenger_median_wape=(
            challenger_median_wape
        ),
        comparable_product_count=(
            comparable_product_count
        ),
        improved_product_count=(
            improved_product_count
        ),
        promotion_recommended=(
            promotion_recommended
        ),
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
    )
