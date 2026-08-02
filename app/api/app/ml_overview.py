"""Read-only aggregate endpoint for the ML dashboard overview."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.ml_api import fetch_all_readonly, fetch_one_readonly


router = APIRouter(prefix="/api/v1/ml", tags=["ml"])


def _number(value: Any) -> float | None:
    return None if value is None else float(value)


@router.get("/overview")
def ml_overview() -> dict[str, Any]:
    context = fetch_one_readonly(
        """
        WITH calibrated AS (
            SELECT *
            FROM ml.model_runs
            WHERE status = 'completed'
              AND model_family = 'hybrid_calibrated'
              AND coalesce(
                    (parameters ->> 'inventory_risk_ready')::boolean,
                    false
                  ) = true
            ORDER BY finished_at DESC, id DESC
            LIMIT 1
        ),
        hybrid AS (
            SELECT mr.*
            FROM ml.model_runs mr, calibrated c
            WHERE mr.id = CAST(
                c.parameters ->> 'parent_hybrid_run_id' AS uuid
            )
        ),
        baseline AS (
            SELECT mr.*
            FROM ml.model_runs mr, hybrid h
            WHERE mr.id = CAST(
                h.parameters ->> 'baseline_run_id' AS uuid
            )
        ),
        challenger AS (
            SELECT mr.*
            FROM ml.model_runs mr, hybrid h
            WHERE mr.id = CAST(
                h.parameters ->> 'challenger_run_id' AS uuid
            )
        ),
        feature AS (
            SELECT fr.*
            FROM ml.feature_runs fr, calibrated c
            WHERE fr.id = c.feature_run_id
        )
        SELECT
            c.id AS calibrated_run_id,
            c.training_cutoff,
            c.dataset_fingerprint,
            c.code_commit AS calibrated_code_commit,
            c.started_at AS calibrated_started_at,
            c.finished_at AS calibrated_finished_at,
            c.parameters AS calibrated_parameters,

            h.id AS hybrid_run_id,
            h.model_version AS hybrid_model_version,
            h.code_commit AS hybrid_code_commit,
            h.parameters AS hybrid_parameters,

            b.id AS baseline_run_id,
            b.model_version AS baseline_model_version,
            b.code_commit AS baseline_code_commit,

            ch.id AS lightgbm_run_id,
            ch.model_version AS lightgbm_model_version,
            ch.code_commit AS lightgbm_code_commit,

            f.id AS feature_run_id,
            f.status AS feature_status,
            f.feature_version,
            f.source_min_month,
            f.source_max_month,
            f.product_count AS feature_product_count,
            f.row_count AS feature_row_count,
            f.dataset_fingerprint AS feature_dataset_fingerprint,
            f.git_commit AS feature_git_commit,
            f.metadata AS feature_metadata,
            f.started_at AS feature_started_at,
            f.finished_at AS feature_finished_at,

            dq.id AS data_quality_run_id,
            dq.status AS data_quality_status,
            dq.quality_version,
            dq.source_min_date AS quality_source_min_date,
            dq.source_max_date AS quality_source_max_date,
            dq.issue_count,
            dq.critical_count,
            dq.warning_count,
            dq.dataset_fingerprint AS quality_dataset_fingerprint,
            dq.started_at AS quality_started_at,
            dq.finished_at AS quality_finished_at,

            (
                SELECT min(forecast_month)
                FROM ml.forecasts
                WHERE model_run_id = c.id
            ) AS forecast_min_month,
            (
                SELECT max(forecast_month)
                FROM ml.forecasts
                WHERE model_run_id = c.id
            ) AS forecast_max_month
        FROM calibrated c
        JOIN hybrid h ON true
        JOIN baseline b ON true
        JOIN challenger ch ON true
        JOIN feature f ON true
        LEFT JOIN LATERAL (
            SELECT d.*
            FROM ml.data_quality_runs d
            WHERE d.status = 'completed'
              AND d.dataset_fingerprint = c.dataset_fingerprint
            ORDER BY d.finished_at DESC NULLS LAST, d.id DESC
            LIMIT 1
        ) dq ON true
        """
    )
    if context is None:
        raise HTTPException(503, "No complete ML lineage is available.")

    ids = {
        "baseline": context["baseline_run_id"],
        "lightgbm": context["lightgbm_run_id"],
        "hybrid": context["hybrid_run_id"],
    }

    quality_rows = fetch_all_readonly(
        """
        SELECT
            model_run_id,
            percentile_cont(0.5)
            WITHIN GROUP (
                ORDER BY backtest_wape
            ) AS median_wape
        FROM ml.forecasts
        WHERE model_run_id IN (
            CAST(:baseline AS uuid),
            CAST(:lightgbm AS uuid),
            CAST(:hybrid AS uuid)
        )
          AND backtest_wape IS NOT NULL
        GROUP BY model_run_id
        """,
        ids,
    )
    wape = {
        str(row["model_run_id"]): _number(row["median_wape"])
        for row in quality_rows
    }

    issues: list[dict[str, Any]] = []
    dq_id = context.get("data_quality_run_id")
    if dq_id:
        issues = fetch_all_readonly(
            """
            SELECT
                severity,
                check_code,
                entity_type,
                entity_id,
                period,
                column_name,
                observed_value,
                expected_rule,
                message,
                created_at
            FROM ml.data_quality_issues
            WHERE run_id = CAST(:run_id AS uuid)
            ORDER BY
                CASE severity
                    WHEN 'critical' THEN 1
                    WHEN 'warning' THEN 2
                    ELSE 3
                END,
                check_code,
                created_at
            """,
            {"run_id": dq_id},
        )

    feature_meta = context.get("feature_metadata")
    if not isinstance(feature_meta, dict):
        feature_meta = {}

    calibrated = context.get("calibrated_parameters")
    if not isinstance(calibrated, dict):
        calibrated = {}

    hybrid = context.get("hybrid_parameters")
    if not isinstance(hybrid, dict):
        hybrid = {}

    selection = hybrid.get("selection_counts")
    if not isinstance(selection, dict):
        selection = {}

    def model_payload(name: str, version_key: str) -> dict[str, Any]:
        run_id = context[f"{name}_run_id"]
        return {
            "run_id": run_id,
            "model_version": context[version_key],
            "median_wape": wape.get(str(run_id)),
        }

    return {
        "status": "ready",
        "latest_model_run_id": context["calibrated_run_id"],
        "training_cutoff": context["training_cutoff"],
        "forecast_period": {
            "from": context["forecast_min_month"],
            "to": context["forecast_max_month"],
        },
        "model_quality": {
            "baseline": model_payload(
                "baseline", "baseline_model_version"
            ),
            "lightgbm": model_payload(
                "lightgbm", "lightgbm_model_version"
            ),
            "hybrid": model_payload(
                "hybrid", "hybrid_model_version"
            ),
        },
        "coverage": {
            "overall_holdout": _number(
                calibrated.get("overall_holdout_coverage")
            ),
            "minimum_horizon": _number(
                calibrated.get("minimum_horizon_coverage")
            ),
            "minimum_cell": _number(
                calibrated.get("minimum_cell_coverage")
            ),
        },
        "selection_counts": {
            "baseline": int(selection.get("baseline", 0)),
            "lightgbm": int(selection.get("lightgbm", 0)),
            "cold_start": int(selection.get("cold_start", 0)),
        },
        "data_quality": {
            "run_id": dq_id,
            "status": context.get("data_quality_status"),
            "quality_version": context.get("quality_version"),
            "source_min_date": context.get("quality_source_min_date"),
            "source_max_date": context.get("quality_source_max_date"),
            "issue_count": int(context.get("issue_count") or 0),
            "critical_count": int(context.get("critical_count") or 0),
            "warning_count": int(context.get("warning_count") or 0),
            "dataset_fingerprint": context.get(
                "quality_dataset_fingerprint"
            ),
            "started_at": context.get("quality_started_at"),
            "finished_at": context.get("quality_finished_at"),
            "issues": issues,
        },
        "feature_run": {
            "id": context["feature_run_id"],
            "status": context["feature_status"],
            "feature_version": context["feature_version"],
            "source_min_month": context["source_min_month"],
            "source_max_month": context["source_max_month"],
            "sales_source_max_month": feature_meta.get(
                "sales_source_max_month"
            ),
            "inventory_source_max_month": feature_meta.get(
                "inventory_source_max_month"
            ),
            "panel_max_month": feature_meta.get("panel_max_month"),
            "product_count": int(context["feature_product_count"]),
            "row_count": int(context["feature_row_count"]),
            "dataset_fingerprint": context[
                "feature_dataset_fingerprint"
            ],
            "git_commit": context["feature_git_commit"],
            "started_at": context["feature_started_at"],
            "finished_at": context["feature_finished_at"],
        },
        "lineage": {
            "data_quality_run_id": dq_id,
            "feature_run_id": context["feature_run_id"],
            "baseline_run_id": context["baseline_run_id"],
            "lightgbm_run_id": context["lightgbm_run_id"],
            "hybrid_run_id": context["hybrid_run_id"],
            "calibrated_run_id": context["calibrated_run_id"],
            "dataset_fingerprint": context["dataset_fingerprint"],
            "feature_git_commit": context["feature_git_commit"],
            "baseline_code_commit": context[
                "baseline_code_commit"
            ],
            "lightgbm_code_commit": context[
                "lightgbm_code_commit"
            ],
            "hybrid_code_commit": context["hybrid_code_commit"],
            "calibrated_code_commit": context[
                "calibrated_code_commit"
            ],
            "calibrated_started_at": context[
                "calibrated_started_at"
            ],
            "calibrated_finished_at": context[
                "calibrated_finished_at"
            ],
        },
    }
