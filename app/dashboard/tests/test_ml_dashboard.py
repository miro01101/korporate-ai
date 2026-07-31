from pathlib import Path
import unittest

from ml_dashboard import (
    ML_ENDPOINTS,
    action_quantity,
    build_forecast_chart_rows,
    filter_recommendations,
    filter_risk_rows,
    probability_percent,
    product_ids_from_payloads,
    recommendation_label,
    recommendation_type_summary,
    render_ml_dashboard,
)


class MLDashboardTests(unittest.TestCase):
    def test_probability_percent(self) -> None:
        self.assertEqual(
            probability_percent(0.5234),
            "52.3 %",
        )
        self.assertEqual(
            probability_percent(None),
            "—",
        )

    def test_recommendation_label(self) -> None:
        self.assertEqual(
            recommendation_label("PURCHASE"),
            "Objednať",
        )
        self.assertEqual(
            recommendation_label("CUSTOM"),
            "CUSTOM",
        )

    def test_product_ids_are_unique_and_sorted(self) -> None:
        self.assertEqual(
            product_ids_from_payloads(
                [
                    {"product_id": "B"},
                    {"product_id": "A"},
                ],
                [
                    {"product_id": "A"},
                    {"product_id": None},
                ],
            ),
            ["A", "B"],
        )

    def test_forecast_chart_has_three_quantiles(self) -> None:
        rows = build_forecast_chart_rows(
            [
                {
                    "forecast_month": "2026-01-01",
                    "forecast_p10": 1,
                    "forecast_p50": 2,
                    "forecast_p90": 3,
                }
            ]
        )

        self.assertEqual(len(rows), 3)
        self.assertEqual(
            {row["quantile"] for row in rows},
            {"P10", "P50", "P90"},
        )

    def test_risk_filter(self) -> None:
        rows = filter_risk_rows(
            [
                {
                    "product_id": "A",
                    "stockout_probability_30d": 0.8,
                    "recommended_order_quantity": 5,
                },
                {
                    "product_id": "B",
                    "stockout_probability_30d": 0.2,
                    "recommended_order_quantity": 0,
                },
            ],
            minimum_probability=0.5,
            recommended_only=True,
        )

        self.assertEqual(
            [row["product_id"] for row in rows],
            ["A"],
        )

    def test_recommendation_filter(self) -> None:
        rows = filter_recommendations(
            [
                {
                    "product_id": "A",
                    "recommendation_type": "PURCHASE",
                    "priority": 90,
                    "recommended_quantity": 5,
                },
                {
                    "product_id": "B",
                    "recommendation_type": "HOLD",
                    "priority": 10,
                    "recommended_quantity": 0,
                },
            ],
            selected_types=["PURCHASE"],
            minimum_priority=50,
        )

        self.assertEqual(
            [row["product_id"] for row in rows],
            ["A"],
        )

    def test_recommendation_summary_has_all_types(self) -> None:
        summary = recommendation_type_summary(
            [
                {
                    "recommendation_type": "PURCHASE",
                },
                {
                    "recommendation_type": "PURCHASE",
                },
                {
                    "recommendation_type": "HOLD",
                },
            ]
        )

        values = {
            row["type"]: row["count"]
            for row in summary
        }

        self.assertEqual(values["PURCHASE"], 2)
        self.assertEqual(values["HOLD"], 1)
        self.assertEqual(values["EXPEDITE"], 0)

    def test_action_quantity_excludes_non_actions(self) -> None:
        self.assertEqual(
            action_quantity(
                [
                    {
                        "recommendation_type": "PURCHASE",
                        "recommended_quantity": 10,
                    },
                    {
                        "recommendation_type": "EXPEDITE",
                        "recommended_quantity": 3,
                    },
                    {
                        "recommendation_type": "HOLD",
                        "recommended_quantity": 9,
                    },
                ]
            ),
            13,
        )

    def test_six_read_only_endpoints_are_declared(self) -> None:
        self.assertEqual(len(ML_ENDPOINTS), 6)
        self.assertTrue(
            all(
                endpoint.startswith("/api/v1/ml/")
                for endpoint in ML_ENDPOINTS
            )
        )

    def test_module_has_no_direct_database_or_write_client(self) -> None:
        source = Path(
            "/app/ml_dashboard.py"
        ).read_text(encoding="utf-8")

        forbidden = (
            "sqlalchemy",
            "psycopg",
            "requests.post",
            "httpx.post",
            "api_post",
            "api_put",
            "api_patch",
            "api_delete",
        )

        for token in forbidden:
            self.assertNotIn(token, source)

        self.assertTrue(callable(render_ml_dashboard))


if __name__ == "__main__":
    unittest.main()
