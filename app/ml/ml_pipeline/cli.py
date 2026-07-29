from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from pathlib import Path
import importlib.metadata
import json
import math
import os
import subprocess
from typing import Any
from uuid import UUID, uuid4

import numpy as np
import pandas as pd
from psycopg.types.json import Jsonb

from ml_pipeline.config import (
    DatabaseConfig,
    FEATURE_VERSION,
    QUALITY_VERSION,
)
from ml_pipeline.db import (
    database_connection,
    execute_many,
    load_source_frames,
)
from ml_pipeline.features import (
    build_product_monthly_features,
    dataset_fingerprint,
)
from ml_pipeline.quality import (
    QualityIssue,
    validate_source_frames,
)
from ml_pipeline.demand import run_demand_training
from ml_pipeline.lightgbm_challenger import (
    run_lightgbm_training,
)
from ml_pipeline.hybrid_champion import (
    run_hybrid_selection,
)


def git_commit() -> str | None:
    configured = os.getenv("GIT_COMMIT", "").strip()

    if configured:
        return configured

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()

    if isinstance(value, np.generic):
        return value.item()

    if pd.isna(value):
        return None

    return value


def _python_value(value: Any) -> Any:
    if isinstance(value, np.generic):
        value = value.item()

    if isinstance(value, pd.Timestamp):
        return value.date()

    if pd.isna(value):
        return None

    return value


def self_check() -> int:
    packages = (
        "numpy",
        "pandas",
        "scikit-learn",
        "lightgbm",
        "psycopg",
        "joblib",
    )

    print("ML_SELF_CHECK=START")

    for package in packages:
        version = importlib.metadata.version(package)
        print(f"package={package} version={version}")

    print(f"feature_version={FEATURE_VERSION}")
    print(f"quality_version={QUALITY_VERSION}")
    print("ML_SELF_CHECK=PASS")

    return 0


def _write_quality_run(
    frames: dict[str, pd.DataFrame],
    issues: list[QualityIssue],
) -> UUID:
    config = DatabaseConfig.from_environment()
    run_id = uuid4()
    fingerprint = dataset_fingerprint(frames.values())

    sales = frames["sales"]

    min_date = (
        pd.to_datetime(sales["month_start"]).min().date()
        if not sales.empty
        else None
    )

    max_date = (
        pd.to_datetime(sales["month_start"]).max().date()
        if not sales.empty
        else None
    )

    critical_count = sum(
        issue.severity == "critical"
        for issue in issues
    )

    warning_count = sum(
        issue.severity == "warning"
        for issue in issues
    )

    with database_connection(config) as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ml.data_quality_runs (
                        id,
                        status,
                        quality_version,
                        source_min_date,
                        source_max_date,
                        dataset_fingerprint,
                        metadata
                    )
                    VALUES (
                        %s,
                        'running',
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    )
                    """,
                    (
                        run_id,
                        QUALITY_VERSION,
                        min_date,
                        max_date,
                        fingerprint,
                        Jsonb(
                            {
                                "frame_rows": {
                                    name: int(len(frame))
                                    for name, frame
                                    in frames.items()
                                }
                            }
                        ),
                    ),
                )

            issue_rows = []

            for issue in issues:
                issue_rows.append(
                    (
                        uuid4(),
                        run_id,
                        issue.severity,
                        issue.check_code,
                        issue.entity_type,
                        issue.entity_id,
                        issue.period,
                        issue.column_name,
                        Jsonb(
                            {
                                key: _json_safe(value)
                                for key, value
                                in issue.observed_value.items()
                            }
                        ),
                        issue.expected_rule,
                        issue.message,
                    )
                )

            if issue_rows:
                execute_many(
                    connection,
                    """
                    INSERT INTO ml.data_quality_issues (
                        id,
                        run_id,
                        severity,
                        check_code,
                        entity_type,
                        entity_id,
                        period,
                        column_name,
                        observed_value,
                        expected_rule,
                        message
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    """,
                    issue_rows,
                )

            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE ml.data_quality_runs
                    SET status = 'completed',
                        finished_at = now(),
                        issue_count = %s,
                        critical_count = %s,
                        warning_count = %s
                    WHERE id = %s
                    """,
                    (
                        len(issues),
                        critical_count,
                        warning_count,
                        run_id,
                    ),
                )

            connection.commit()
        except Exception:
            connection.rollback()
            raise

    return run_id


def validate_command() -> int:
    config = DatabaseConfig.from_environment()

    with database_connection(config) as connection:
        frames = load_source_frames(connection)

    issues = validate_source_frames(frames)
    run_id = _write_quality_run(frames, issues)

    critical_count = sum(
        issue.severity == "critical"
        for issue in issues
    )

    warning_count = sum(
        issue.severity == "warning"
        for issue in issues
    )

    print(f"quality_run_id={run_id}")
    print(f"quality_issue_count={len(issues)}")
    print(f"quality_critical_count={critical_count}")
    print(f"quality_warning_count={warning_count}")

    for issue in issues:
        print(
            json.dumps(
                issue.as_dict(),
                default=str,
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    if critical_count:
        print("ML_DATA_QUALITY=FAIL")
        return 2

    print("ML_DATA_QUALITY=PASS")
    return 0


FEATURE_COLUMNS = (
    "product_id",
    "month_start",
    "product_name",
    "category",
    "supplier_id",
    "units_sold",
    "revenue",
    "gross_profit",
    "order_count",
    "customer_count",
    "zero_demand",
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
    "zero_ratio_12",
    "demand_cv_12",
    "months_since_last_sale",
    "stock_actual",
    "stock_reserved",
    "stock_available",
    "min_stock",
    "max_stock",
    "purchase_price",
    "sales_price",
    "lead_time_days",
    "minimum_order_quantity",
    "abc_class",
    "xyz_class",
    "is_cold_start",
)


def build_features_command() -> int:
    config = DatabaseConfig.from_environment()

    with database_connection(config) as connection:
        frames = load_source_frames(connection)

    issues = validate_source_frames(frames)

    critical_issues = [
        issue
        for issue in issues
        if issue.severity == "critical"
    ]

    if critical_issues:
        print(
            "ML_FEATURE_BUILD=BLOCKED_BY_DATA_QUALITY"
        )
        print(
            f"critical_issue_count={len(critical_issues)}"
        )
        return 2

    features = build_product_monthly_features(
        frames["products"],
        frames["sales"],
        frames["inventory"],
    )

    fingerprint = dataset_fingerprint(
        frames.values()
    )

    feature_run_id = uuid4()
    source_min_month = (
        features["month_start"].min().date()
    )
    source_max_month = (
        features["month_start"].max().date()
    )

    product_count = int(
        features["product_id"].nunique()
    )

    row_count = int(len(features))

    with database_connection(config) as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ml.feature_runs (
                        id,
                        status,
                        feature_version,
                        source_min_month,
                        source_max_month,
                        product_count,
                        row_count,
                        dataset_fingerprint,
                        git_commit,
                        metadata
                    )
                    VALUES (
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
                        feature_run_id,
                        FEATURE_VERSION,
                        source_min_month,
                        source_max_month,
                        product_count,
                        row_count,
                        fingerprint,
                        git_commit(),
                        Jsonb(
                            {
                                "grain": "product_month",
                                "zero_filled_panel": True,
                            }
                        ),
                    ),
                )

            rows = []

            for record in features[
                list(FEATURE_COLUMNS)
            ].itertuples(index=False, name=None):
                rows.append(
                    (
                        feature_run_id,
                        *(
                            _python_value(value)
                            for value in record
                        ),
                    )
                )

            placeholders = ", ".join(
                ["%s"] * (len(FEATURE_COLUMNS) + 1)
            )

            execute_many(
                connection,
                f"""
                INSERT INTO ml.product_monthly_features (
                    feature_run_id,
                    {", ".join(FEATURE_COLUMNS)}
                )
                VALUES ({placeholders})
                """,
                rows,
            )

            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE ml.feature_runs
                    SET status = 'completed',
                        finished_at = now()
                    WHERE id = %s
                    """,
                    (feature_run_id,),
                )

            connection.commit()
        except Exception as exc:
            connection.rollback()

            with database_connection(config) as error_connection:
                with error_connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE ml.feature_runs
                        SET status = 'failed',
                            finished_at = now(),
                            error_message = %s
                        WHERE id = %s
                        """,
                        (
                            str(exc)[:4000],
                            feature_run_id,
                        ),
                    )

                error_connection.commit()

            raise

    print(f"feature_run_id={feature_run_id}")
    print(f"feature_version={FEATURE_VERSION}")
    print(f"feature_product_count={product_count}")
    print(f"feature_row_count={row_count}")
    print(f"feature_source_min_month={source_min_month}")
    print(f"feature_source_max_month={source_max_month}")
    print(f"dataset_fingerprint={fingerprint}")
    print("ML_FEATURE_BUILD=PASS")

    return 0


def train_demand_command() -> int:
    config = DatabaseConfig.from_environment()

    with database_connection(config) as connection:
        summary = run_demand_training(
            connection,
            code_commit=git_commit(),
        )

    print(f"model_run_id={summary.model_run_id}")
    print(f"feature_run_id={summary.feature_run_id}")
    print(
        f"training_cutoff={summary.training_cutoff}"
    )
    print(
        f"demand_product_count={summary.product_count}"
    )
    print(
        f"demand_forecast_count={summary.forecast_count}"
    )
    print(
        f"demand_metric_count={summary.metric_count}"
    )
    print(
        "selected_model_counts="
        + json.dumps(
            summary.selected_model_counts,
            sort_keys=True,
        )
    )
    print(
        "median_selected_wape="
        + (
            str(summary.median_selected_wape)
            if summary.median_selected_wape
            is not None
            else "null"
        )
    )
    print("ML_DEMAND_TRAINING=PASS")

    return 0


def train_lightgbm_command() -> int:
    config = DatabaseConfig.from_environment()

    artifact_root = Path(
        os.getenv(
            "ML_ARTIFACT_DIR",
            "/artifacts",
        )
    )

    with database_connection(config) as connection:
        summary = run_lightgbm_training(
            connection,
            code_commit=git_commit(),
            artifact_root=artifact_root,
        )

    print(f"model_run_id={summary.model_run_id}")
    print(
        f"feature_run_id={summary.feature_run_id}"
    )
    print(
        f"baseline_run_id={summary.baseline_run_id}"
    )
    print(
        f"training_cutoff={summary.training_cutoff}"
    )
    print(
        f"challenger_product_count="
        f"{summary.product_count}"
    )
    print(
        f"challenger_forecast_count="
        f"{summary.forecast_count}"
    )
    print(
        f"challenger_metric_count="
        f"{summary.metric_count}"
    )
    print(
        f"training_row_count="
        f"{summary.training_row_count}"
    )
    print(
        f"backtest_row_count="
        f"{summary.backtest_row_count}"
    )
    print(
        f"raw_quantile_crossing_rate="
        f"{summary.raw_crossing_rate}"
    )
    print(
        f"baseline_median_wape="
        f"{summary.baseline_median_wape}"
    )
    print(
        f"challenger_median_wape="
        f"{summary.challenger_median_wape}"
    )
    print(
        f"comparable_product_count="
        f"{summary.comparable_product_count}"
    )
    print(
        f"improved_product_count="
        f"{summary.improved_product_count}"
    )
    print(
        "promotion_recommended="
        + (
            "ANO"
            if summary.promotion_recommended
            else "NIE"
        )
    )
    print(
        f"artifact_path={summary.artifact_path}"
    )
    print(
        f"artifact_sha256="
        f"{summary.artifact_sha256}"
    )
    print("ML_LIGHTGBM_TRAINING=PASS")

    return 0


def select_hybrid_command() -> int:
    config = DatabaseConfig.from_environment()

    with database_connection(config) as connection:
        summary = run_hybrid_selection(
            connection,
            code_commit=git_commit(),
        )

    print(f"model_run_id={summary.model_run_id}")
    print(
        f"feature_run_id={summary.feature_run_id}"
    )
    print(
        f"baseline_run_id={summary.baseline_run_id}"
    )
    print(
        f"challenger_run_id="
        f"{summary.challenger_run_id}"
    )
    print(
        f"hybrid_product_count="
        f"{summary.product_count}"
    )
    print(
        f"baseline_product_count="
        f"{summary.baseline_product_count}"
    )
    print(
        f"lightgbm_product_count="
        f"{summary.lightgbm_product_count}"
    )
    print(
        f"cold_start_product_count="
        f"{summary.cold_start_product_count}"
    )
    print(
        f"hybrid_forecast_count="
        f"{summary.forecast_count}"
    )
    print(
        f"hybrid_metric_count="
        f"{summary.metric_count}"
    )
    print(
        f"baseline_median_wape="
        f"{summary.baseline_median_wape}"
    )
    print(
        f"hybrid_median_wape="
        f"{summary.hybrid_median_wape}"
    )
    print(
        f"selection_margin="
        f"{summary.selection_margin}"
    )
    print("ML_HYBRID_SELECTION=PASS")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Korporate AI predictive inventory pipeline."
        )
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    subparsers.add_parser("self-check")
    subparsers.add_parser("validate")
    subparsers.add_parser("build-features")
    subparsers.add_parser("train-demand")
    subparsers.add_parser("train-lightgbm")
    subparsers.add_parser("select-hybrid")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "self-check":
        return self_check()

    if args.command == "validate":
        return validate_command()

    if args.command == "build-features":
        return build_features_command()

    if args.command == "train-demand":
        return train_demand_command()

    if args.command == "train-lightgbm":
        return train_lightgbm_command()

    if args.command == "select-hybrid":
        return select_hybrid_command()

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
