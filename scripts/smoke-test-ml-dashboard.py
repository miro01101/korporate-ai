#!/usr/bin/env python3
"""Dynamic smoke test for the Streamlit ML dashboard data contract."""

from __future__ import annotations

import json
from typing import Any
from urllib.request import urlopen


API_BASE_URL = "http://127.0.0.1:18000"
DASHBOARD_BASE_URL = "http://127.0.0.1:18501"

EXPECTED_ML_PATHS = {
    "/api/v1/ml/status",
    "/api/v1/ml/model-runs",
    "/api/v1/ml/forecast",
    "/api/v1/ml/inventory-risk",
    "/api/v1/ml/recommendations",
    "/api/v1/ml/products/{product_id}",
    "/api/v1/ml/overview",
}


def fetch_json(path: str) -> Any:
    with urlopen(API_BASE_URL + path, timeout=20) as response:
        return json.load(response)


def check(name: str, actual: Any, expected: Any) -> None:
    print(f"CHECK={name} ACTUAL={actual} EXPECTED={expected}")
    if actual != expected:
        raise AssertionError(
            f"{name}: {actual!r} != {expected!r}"
        )


with urlopen(
    DASHBOARD_BASE_URL + "/_stcore/health",
    timeout=15,
) as response:
    dashboard_health = response.read().decode("utf-8").strip()

check("dashboard_health", dashboard_health, "ok")

ready = fetch_json("/health/ready")
status = fetch_json("/api/v1/ml/status")
recommendations = fetch_json(
    "/api/v1/ml/recommendations?limit=500"
)
risks = fetch_json(
    "/api/v1/ml/inventory-risk?limit=500"
)
forecasts = fetch_json(
    "/api/v1/ml/forecast?limit=1000"
)
openapi = fetch_json("/openapi.json")

ml_paths = {
    path
    for path in openapi.get("paths", {})
    if path.startswith("/api/v1/ml/")
}

check("api_status", ready["status"], "ready")
check("api_version", ready["version"], "0.5.0")
check("ml_status", status["status"], "ready")
check("platform_version", status["platform_version"], "0.5.0")
check("forecast_payload_count", forecasts["count"], status["forecast_rows"])
check("risk_payload_count", risks["count"], status["inventory_risk_rows"])
check(
    "recommendation_payload_count",
    recommendations["count"],
    status["recommendation_rows"],
)
check(
    "pending_recommendations",
    sum(
        item["status"] == "pending"
        for item in recommendations["items"]
    ),
    status["pending_recommendations"],
)
check("ml_openapi_paths", ml_paths, EXPECTED_ML_PATHS)

print("ML_DASHBOARD_SMOKE_OK=ANO")
