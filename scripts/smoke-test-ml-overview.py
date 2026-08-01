#!/usr/bin/env python3
from __future__ import annotations

import json
from typing import Any
from urllib.request import urlopen

API = "http://127.0.0.1:18000"
DASHBOARD = "http://127.0.0.1:18501"


def get(path: str) -> dict[str, Any]:
    with urlopen(API + path, timeout=20) as response:
        return json.load(response)


def check(name: str, actual: Any, expected: Any) -> None:
    print(f"CHECK={name} ACTUAL={actual} EXPECTED={expected}")
    if actual != expected:
        raise AssertionError(f"{name}: {actual!r} != {expected!r}")


def require(name: str, condition: bool, detail: Any) -> None:
    print(f"CHECK={name} ACTUAL={detail} EXPECTED=PASS")
    if not condition:
        raise AssertionError(f"{name}: {detail!r}")


status = get("/api/v1/ml/status")
overview = get("/api/v1/ml/overview")
openapi = get("/openapi.json")

with urlopen(DASHBOARD + "/_stcore/health", timeout=20) as response:
    dashboard_health = response.read().decode().strip()

check("dashboard_health", dashboard_health, "ok")
check("overview_route", "/api/v1/ml/overview" in openapi["paths"], True)
check(
    "latest_run",
    overview["latest_model_run_id"],
    status["latest_model_run_id"],
)
check("training_cutoff", overview["training_cutoff"], "2026-06-01")
check("forecast_from", overview["forecast_period"]["from"], "2026-07-01")
check("forecast_to", overview["forecast_period"]["to"], "2026-09-01")

for model in ("baseline", "lightgbm", "hybrid"):
    value = overview["model_quality"][model]["median_wape"]
    require(
        f"{model}_wape",
        isinstance(value, (int, float)) and value >= 0,
        value,
    )

selection = overview["selection_counts"]
check(
    "selected_products",
    selection["baseline"] + selection["lightgbm"],
    80,
)

coverage = overview["coverage"]
require("overall_coverage", coverage["overall_holdout"] >= 0.78, coverage)
require("horizon_coverage", coverage["minimum_horizon"] >= 0.75, coverage)
require("cell_coverage", coverage["minimum_cell"] >= 0.70, coverage)

dq = overview["data_quality"]
check("critical_count", dq["critical_count"], 0)
check("issue_payload_count", len(dq["issues"]), dq["issue_count"])

feature = overview["feature_run"]
check("feature_products", feature["product_count"], 80)
check("feature_rows", feature["row_count"], 5360)
check("sales_cutoff", feature["sales_source_max_month"], "2026-06-01")
check(
    "inventory_month",
    feature["inventory_source_max_month"],
    "2026-07-01",
)

for key in (
    "data_quality_run_id",
    "feature_run_id",
    "baseline_run_id",
    "lightgbm_run_id",
    "hybrid_run_id",
    "calibrated_run_id",
):
    require(
        f"lineage_{key}",
        bool(overview["lineage"].get(key)),
        overview["lineage"].get(key),
    )

print("ML_OVERVIEW_API_SMOKE=PASS")
print("ML_OVERVIEW_DASHBOARD_SMOKE=PASS")
