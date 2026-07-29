from __future__ import annotations

import unittest

import pandas as pd

from ml_pipeline.features import (
    build_product_monthly_features,
)


class FeatureEngineeringTests(unittest.TestCase):
    def source_frames(
        self,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        products = pd.DataFrame(
            [
                {
                    "product_id": "P1",
                    "product_name": "Product 1",
                    "category": "A",
                    "supplier_id": 1,
                    "purchase_price": 5.0,
                    "sales_price": 8.0,
                    "minimum_order_quantity": 10,
                    "lead_time_days": 7,
                },
                {
                    "product_id": "P2",
                    "product_name": "Product 2",
                    "category": "B",
                    "supplier_id": 2,
                    "purchase_price": 10.0,
                    "sales_price": 15.0,
                    "minimum_order_quantity": 5,
                    "lead_time_days": 14,
                },
            ]
        )

        sales = pd.DataFrame(
            [
                {
                    "month_start": "2025-01-01",
                    "product_id": "P1",
                    "units_sold": 10,
                    "order_count": 2,
                    "customer_count": 2,
                    "revenue": 80.0,
                    "gross_profit": 30.0,
                },
                {
                    "month_start": "2025-03-01",
                    "product_id": "P1",
                    "units_sold": 20,
                    "order_count": 3,
                    "customer_count": 3,
                    "revenue": 160.0,
                    "gross_profit": 60.0,
                },
                {
                    "month_start": "2025-04-01",
                    "product_id": "P2",
                    "units_sold": 5,
                    "order_count": 1,
                    "customer_count": 1,
                    "revenue": 75.0,
                    "gross_profit": 25.0,
                },
            ]
        )

        inventory_rows = []

        for month in pd.date_range(
            "2025-01-01",
            "2025-04-01",
            freq="MS",
        ):
            for product_id in ("P1", "P2"):
                inventory_rows.append(
                    {
                        "month_start": month,
                        "product_id": product_id,
                        "stock_actual": 100,
                        "stock_reserved": 10,
                        "stock_available": 90,
                        "min_stock": 20,
                        "max_stock": 200,
                    }
                )

        inventory = pd.DataFrame(inventory_rows)

        return products, sales, inventory

    def test_panel_is_complete_and_zero_filled(self) -> None:
        products, sales, inventory = self.source_frames()

        features = build_product_monthly_features(
            products,
            sales,
            inventory,
        )

        self.assertEqual(len(features), 8)

        p1_february = features[
            (features["product_id"] == "P1")
            & (
                features["month_start"]
                == pd.Timestamp("2025-02-01")
            )
        ].iloc[0]

        self.assertEqual(
            int(p1_february["units_sold"]),
            0,
        )

    def test_lags_do_not_use_current_target(self) -> None:
        products, sales, inventory = self.source_frames()

        features = build_product_monthly_features(
            products,
            sales,
            inventory,
        )

        p1_march = features[
            (features["product_id"] == "P1")
            & (
                features["month_start"]
                == pd.Timestamp("2025-03-01")
            )
        ].iloc[0]

        self.assertEqual(float(p1_march["lag_1"]), 0.0)
        self.assertEqual(float(p1_march["lag_2"]), 10.0)
        self.assertEqual(
            float(p1_march["rolling_mean_3"]),
            5.0,
        )

    def test_cold_start_and_segments_exist(self) -> None:
        products, sales, inventory = self.source_frames()

        features = build_product_monthly_features(
            products,
            sales,
            inventory,
        )

        self.assertTrue(
            set(features["abc_class"])
            <= {"A", "B", "C"}
        )

        self.assertTrue(
            set(features["xyz_class"])
            <= {"X", "Y", "Z"}
        )

        p2_april = features[
            (features["product_id"] == "P2")
            & (
                features["month_start"]
                == pd.Timestamp("2025-04-01")
            )
        ].iloc[0]

        self.assertTrue(bool(p2_april["is_cold_start"]))
        self.assertEqual(p2_april["xyz_class"], "Z")


if __name__ == "__main__":
    unittest.main()
