from __future__ import annotations

from datetime import date
import inspect
import unittest
from uuid import uuid4

import pandas as pd

from ml_pipeline.inventory_risk import (
    build_inventory_risk_frame,
    quantile_cdf_proxy,
    round_up_to_moq,
    run_inventory_risk,
)


class InventoryRiskTests(unittest.TestCase):
    def test_psycopg_sql_modulo_is_escaped(
        self,
    ) -> None:
        source = inspect.getsource(
            run_inventory_risk
        )

        self.assertEqual(
            source.count("%% products.minimum_order_quantity"),
            1,
        )
        self.assertNotIn(
            "\n                           % products.minimum_order_quantity",
            source,
        )

    def _frames(
        self,
        *,
        stock_available: int = 3,
        max_stock: int = 20,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        products = pd.DataFrame(
            [
                {
                    "product_id": "P1",
                    "lead_time_days": 10,
                    "minimum_order_quantity": 7,
                    "stock_available": stock_available,
                    "max_stock": max_stock,
                    "snapshot_date": date(2025, 12, 1),
                }
            ]
        )

        forecasts = pd.DataFrame(
            [
                {
                    "product_id": "P1",
                    "forecast_month": date(2026, 1, 1),
                    "horizon": 1,
                    "forecast_p10": 5.0,
                    "forecast_p50": 10.0,
                    "forecast_p90": 20.0,
                },
                {
                    "product_id": "P1",
                    "forecast_month": date(2026, 2, 1),
                    "horizon": 2,
                    "forecast_p10": 4.0,
                    "forecast_p50": 8.0,
                    "forecast_p90": 16.0,
                },
                {
                    "product_id": "P1",
                    "forecast_month": date(2026, 3, 1),
                    "horizon": 3,
                    "forecast_p10": 3.0,
                    "forecast_p50": 6.0,
                    "forecast_p90": 12.0,
                },
            ]
        )

        purchases = pd.DataFrame(
            [
                {
                    "product_id": "P1",
                    "delivery_date": date(2025, 12, 6),
                    "outstanding_quantity": 2,
                }
            ]
        )

        return products, forecasts, purchases

    def test_cdf_hits_quantile_anchors(self) -> None:
        self.assertAlmostEqual(
            quantile_cdf_proxy(10.0, 10.0, 20.0, 30.0),
            0.10,
        )
        self.assertAlmostEqual(
            quantile_cdf_proxy(20.0, 10.0, 20.0, 30.0),
            0.50,
        )
        self.assertAlmostEqual(
            quantile_cdf_proxy(30.0, 10.0, 20.0, 30.0),
            0.90,
        )

    def test_cdf_handles_equal_zero_quantiles(self) -> None:
        self.assertAlmostEqual(
            quantile_cdf_proxy(0.0, 0.0, 0.0, 0.0),
            0.90,
        )
        self.assertGreaterEqual(
            quantile_cdf_proxy(1.0, 0.0, 0.0, 0.0),
            0.90,
        )

    def test_moq_rounding(self) -> None:
        self.assertEqual(round_up_to_moq(0.0, 7), 0)
        self.assertEqual(round_up_to_moq(0.1, 7), 7)
        self.assertEqual(round_up_to_moq(15.0, 7), 21)

    def test_build_risk_uses_p50_p90_incoming_and_moq(self) -> None:
        products, forecasts, purchases = self._frames()

        risk = build_inventory_risk_frame(
            products,
            forecasts,
            purchases,
            model_run_id=uuid4(),
            as_of_date=date(2025, 12, 1),
            expected_product_count=1,
        )

        row = risk.iloc[0]

        self.assertEqual(row["incoming_quantity"], 2)
        self.assertEqual(row["expected_lead_time_demand"], 10.0)
        self.assertEqual(row["safety_stock"], 10.0)
        self.assertEqual(row["reorder_point"], 20.0)
        self.assertEqual(row["recommended_order_quantity"], 21)
        self.assertEqual(row["recommended_order_date"], date(2025, 12, 1))

        for column in (
            "stockout_probability_30d",
            "stockout_probability_60d",
            "stockout_probability_90d",
            "overstock_probability_90d",
        ):
            self.assertGreaterEqual(row[column], 0.0)
            self.assertLessEqual(row[column], 1.0)

    def test_high_stock_produces_no_order(self) -> None:
        products, forecasts, purchases = self._frames(
            stock_available=100,
            max_stock=20,
        )

        risk = build_inventory_risk_frame(
            products,
            forecasts,
            purchases.iloc[0:0],
            model_run_id=uuid4(),
            as_of_date=date(2025, 12, 1),
            expected_product_count=1,
        )

        row = risk.iloc[0]

        self.assertEqual(row["recommended_order_quantity"], 0)
        self.assertIsNone(row["recommended_order_date"])
        self.assertGreater(row["overstock_probability_90d"], 0.0)

    def test_lead_time_above_mvp_guard_is_rejected(self) -> None:
        products, forecasts, purchases = self._frames()
        products.loc[0, "lead_time_days"] = 31

        with self.assertRaisesRegex(
            ValueError,
            "supports lead time",
        ):
            build_inventory_risk_frame(
                products,
                forecasts,
                purchases,
                model_run_id=uuid4(),
                as_of_date=date(2025, 12, 1),
                expected_product_count=1,
            )


if __name__ == "__main__":
    unittest.main()
