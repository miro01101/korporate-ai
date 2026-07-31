#!/usr/bin/env python3
"""Dynamic production smoke test for read-only ML API endpoints."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


BASE_URL = "http://127.0.0.1:18000"
EXPECTED_PATHS = {
    "/api/v1/ml/status",
    "/api/v1/ml/model-runs",
    "/api/v1/ml/forecast",
    "/api/v1/ml/inventory-risk",
    "/api/v1/ml/recommendations",
    "/api/v1/ml/products/{product_id}",
}


def request_json(path: str) -> dict[str, Any]:
    with urlopen(BASE_URL + path, timeout=20) as response:
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
    missing = EXPECTED_PATHS - set(openapi["paths"])
    assert_equal("missing_ml_paths", sorted(missing), [])
    for path in EXPECTED_PATHS:
        assert_equal(
            f"methods_{path}",
            sorted(openapi["paths"][path]),
            ["get"],
        )

    status = request_json("/api/v1/ml/status")
    run_id = status["latest_model_run_id"]

    assert_equal("ml_status", status["status"], "ready")
    assert_equal("ml_api_version", status["api_version"], "0.5.0")
    assert_equal("platform_version", status["platform_version"], "0.5.0")
    assert_equal("transaction_read_only", status["transaction_read_only"], True)

    model_runs = request_json("/api/v1/ml/model-runs?limit=100")
    if model_runs["count"] < 1:
        raise AssertionError("No model runs returned.")
    assert_equal(
        "latest_run_present",
        any(item["id"] == run_id for item in model_runs["items"]),
        True,
    )

    forecasts = request_json("/api/v1/ml/forecast?limit=1000")
    risks = request_json("/api/v1/ml/inventory-risk?limit=500")
    recommendations = request_json(
        "/api/v1/ml/recommendations?limit=500"
    )

    assert_equal("forecast_count", forecasts["count"], status["forecast_rows"])
    assert_equal("forecast_run", forecasts["model_run_id"], run_id)
    assert_equal("risk_count", risks["count"], status["inventory_risk_rows"])
    assert_equal("risk_run", risks["model_run_id"], run_id)
    assert_equal(
        "recommendation_count",
        recommendations["count"],
        status["recommendation_rows"],
    )
    assert_equal("recommendation_run", recommendations["model_run_id"], run_id)

    pending = sum(
        item["status"] == "pending"
        for item in recommendations["items"]
    )
    assert_equal(
        "recommendation_pending_count",
        pending,
        status["pending_recommendations"],
    )

    product_ids = [
        item["product_id"]
        for item in recommendations["items"]
    ] or [
        item["product_id"]
        for item in risks["items"]
    ]
    if not product_ids:
        raise AssertionError("No product available for detail smoke.")

    product_id = product_ids[0]
    product = request_json(f"/api/v1/ml/products/{product_id}")
    assert_equal("product_id", product["product"]["product_id"], product_id)
    assert_equal("product_run", product["model_run_id"], run_id)
    if not product["forecasts"]:
        raise AssertionError("Product detail has no forecasts.")

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
