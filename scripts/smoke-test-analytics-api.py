"""Smoke-test analytics API endpoints."""

from __future__ import annotations

import argparse
import json
import sys
from urllib.parse import urlencode
from urllib.request import urlopen


EXPECTED_REVENUE = 1961603.78
EXPECTED_GROSS_PROFIT = 431770.06


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:18000",
    )
    return parser.parse_args()


def get_json(
    base_url: str,
    path: str,
    parameters: dict[str, object] | None = None,
):
    url = f"{base_url.rstrip('/')}{path}"
    if parameters:
        url = f"{url}?{urlencode(parameters)}"

    with urlopen(url, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError(
                f"{path} returned HTTP {response.status}"
            )
        return json.load(response)


def assert_equal(name: str, actual, expected) -> None:
    print(f"CHECK={name} ACTUAL={actual} EXPECTED={expected}")
    if actual != expected:
        raise RuntimeError(
            f"{name}: {actual!r} != {expected!r}"
        )


def assert_at_least(name: str, actual, minimum) -> None:
    print(f"CHECK={name} ACTUAL={actual} MINIMUM={minimum}")
    if actual < minimum:
        raise RuntimeError(
            f"{name}: {actual!r} < minimum {minimum!r}"
        )


def main() -> int:
    args = parse_args()

    try:
        status = get_json(
            args.base_url,
            "/api/v1/analytics/status",
        )
        assert_equal("analytics_status", status["status"], "ready")
        assert_at_least(
            "sales_monthly_rows",
            status["table_counts"]["sales_monthly"],
            66,
        )
        assert_at_least(
            "management_rows",
            status["table_counts"]["management_kpis_monthly"],
            67,
        )

        summary = get_json(
            args.base_url,
            "/api/v1/analytics/summary",
        )
        assert_at_least(
            "latest_sales_month",
            summary["selected_month"],
            "2026-06-01",
        )

        monthly = get_json(
            args.base_url,
            "/api/v1/analytics/monthly",
        )
        assert_at_least("monthly_rows", monthly["count"], 67)

        revenue = round(
            sum(
                float(row["revenue"] or 0)
                for row in monthly["items"]
            ),
            2,
        )
        gross_profit = round(
            sum(
                float(row["gross_profit"] or 0)
                for row in monthly["items"]
            ),
            2,
        )

        assert_at_least(
            "management_revenue",
            revenue,
            EXPECTED_REVENUE,
        )
        assert_at_least(
            "management_gross_profit",
            gross_profit,
            EXPECTED_GROSS_PROFIT,
        )

        products = get_json(
            args.base_url,
            "/api/v1/analytics/sales/products",
            {
                "date_from": "2025-01-01",
                "date_to": "2026-06-30",
                "limit": 10,
            },
        )
        assert_equal("top_product_rows", products["count"], 10)

        inventory = get_json(
            args.base_url,
            "/api/v1/analytics/inventory",
        )
        assert_at_least("inventory_rows", inventory["count"], 67)

        suppliers = get_json(
            args.base_url,
            "/api/v1/analytics/procurement/suppliers",
        )
        assert_equal("supplier_rows", suppliers["count"], 8)

        expeditions = get_json(
            args.base_url,
            "/api/v1/analytics/expeditions",
        )
        assert_at_least("expedition_rows", expeditions["count"], 67)

        vehicles = get_json(
            args.base_url,
            "/api/v1/analytics/vehicles",
        )
        assert_equal("vehicle_rows", vehicles["count"], 6)

    except Exception as exc:
        print(
            f"ANALYTICS_API_SMOKE_ERROR={exc}",
            file=sys.stderr,
        )
        return 1

    print("ANALYTICS_API_SMOKE_OK=ANO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
