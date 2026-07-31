#!/usr/bin/env python3
"""Production smoke test for read-only ML API endpoints."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE_URL = "http://127.0.0.1:18000"
EXPECTED_RUN_ID = "2425d5eb-371f-48d1-9d60-a65bcf614d74"
EXPECTED_PATHS = {
    "/api/v1/ml/status",
    "/api/v1/ml/model-runs",
    "/api/v1/ml/forecast",
    "/api/v1/ml/inventory-risk",
    "/api/v1/ml/recommendations",
    "/api/v1/ml/products/{product_id}",
}


def request_json(path: str) -> dict[str, Any]:
    with urlopen(BASE_URL + path, timeout=15) as response:
        if response.status != 200:
            raise AssertionError(
                f"GET {path} returned {response.status}."
            )
        return json.load(response)


def assert_equal(name: str, actual: Any, expected: Any) -> None:
    print(f"CHECK={name} ACTUAL={actual} EXPECTED={expected}")
    if actual != expected:
        raise AssertionError(
            f"{name}: expected {expected!r}, got {actual!r}."
        )


def main() -> int:
    ready = request_json("/health/ready")
    assert_equal("api_ready", ready["status"], "ready")
    assert_equal("api_version", ready["version"], "0.5.0")

    openapi = request_json("/openapi.json")
    assert_equal("openapi_api_version", openapi["info"]["version"], "0.5.0")
    assert_equal("openapi_path_count", len(openapi["paths"]), 18)
    missing = EXPECTED_PATHS - set(openapi["paths"])
    assert_equal("missing_ml_paths", sorted(missing), [])
    for path in EXPECTED_PATHS:
        assert_equal(
            f"methods_{path}",
            sorted(openapi["paths"][path]),
            ["get"],
        )

    status = request_json("/api/v1/ml/status")
    assert_equal("ml_status", status["status"], "ready")
    assert_equal("ml_api_version", status["api_version"], "0.5.0")
    assert_equal("platform_version", status["platform_version"], "0.5.0")
    assert_equal("latest_model_run_id", status["latest_model_run_id"], EXPECTED_RUN_ID)
    assert_equal("forecast_rows", status["forecast_rows"], 240)
    assert_equal("inventory_risk_rows", status["inventory_risk_rows"], 80)
    assert_equal("recommendation_rows", status["recommendation_rows"], 80)
    assert_equal("pending_recommendations", status["pending_recommendations"], 80)
    assert_equal("transaction_read_only", status["transaction_read_only"], True)

    model_runs = request_json("/api/v1/ml/model-runs?limit=100")
    assert_equal("model_run_count", model_runs["count"], 5)

    forecasts = request_json("/api/v1/ml/forecast?limit=500")
    assert_equal("forecast_count", forecasts["count"], 240)
    assert_equal("forecast_run", forecasts["model_run_id"], EXPECTED_RUN_ID)

    risks = request_json("/api/v1/ml/inventory-risk?limit=200")
    assert_equal("risk_count", risks["count"], 80)
    assert_equal("risk_run", risks["model_run_id"], EXPECTED_RUN_ID)

    recommendations = request_json(
        "/api/v1/ml/recommendations?limit=100"
    )
    assert_equal("recommendation_count", recommendations["count"], 80)
    pending = sum(
        item["status"] == "pending"
        for item in recommendations["items"]
    )
    assert_equal("recommendation_pending_count", pending, 80)

    product = request_json(
        "/api/v1/ml/products/KORP-LT-0033"
    )
    assert_equal("product_id", product["product"]["product_id"], "KORP-LT-0033")
    assert_equal("product_forecast_count", len(product["forecasts"]), 3)
    assert_equal(
        "product_recommendation_type",
        product["recommendation"]["recommendation_type"],
        "PURCHASE",
    )

    try:
        request_json("/api/v1/ml/products/DOES-NOT-EXIST")
    except HTTPError as error:
        assert_equal("unknown_product_status", error.code, 404)
    else:
        raise AssertionError("Unknown product did not return 404.")

    request = Request(
        BASE_URL + "/api/v1/ml/recommendations",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urlopen(request, timeout=15)
    except HTTPError as error:
        assert_equal("recommendation_post_status", error.code, 405)
    else:
        raise AssertionError("POST recommendation endpoint unexpectedly exists.")

    print("ML_API_SMOKE_OK=ANO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
