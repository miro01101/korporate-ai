from __future__ import annotations

import unittest

import pandas as pd

from ml_pipeline.quality import validate_source_frames


class QualityTests(unittest.TestCase):
    def valid_frames(self) -> dict[str, pd.DataFrame]:
        return {
            "products": pd.DataFrame(
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
                    }
                ]
            ),
            "sales": pd.DataFrame(
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
                        "month_start": "2025-02-01",
                        "product_id": "P1",
                        "units_sold": 0,
                        "order_count": 0,
                        "customer_count": 0,
                        "revenue": 0.0,
                        "gross_profit": 0.0,
                    },
                ]
            ),
            "inventory": pd.DataFrame(
                [
                    {
                        "month_start": "2025-01-01",
                        "product_id": "P1",
                        "stock_actual": 100,
                        "stock_reserved": 10,
                        "stock_available": 90,
                        "min_stock": 20,
                        "max_stock": 200,
                    }
                ]
            ),
            "purchases": pd.DataFrame(
                [
                    {
                        "order_date": "2025-01-01",
                        "delivery_date": "2025-01-07",
                        "product_id": "P1",
                        "ordered_quantity": 100,
                        "delivered_quantity": 90,
                        "purchase_price": 5.0,
                    }
                ]
            ),
        }

    def test_valid_frames_have_no_critical_issues(self) -> None:
        issues = validate_source_frames(
            self.valid_frames()
        )

        critical = [
            issue
            for issue in issues
            if issue.severity == "critical"
        ]

        self.assertEqual(critical, [])

    def test_duplicate_sales_grain_is_critical(self) -> None:
        frames = self.valid_frames()

        frames["sales"] = pd.concat(
            [
                frames["sales"],
                frames["sales"].iloc[[0]],
            ],
            ignore_index=True,
        )

        issues = validate_source_frames(frames)

        codes = {
            issue.check_code
            for issue in issues
            if issue.severity == "critical"
        }

        self.assertIn(
            "SALES_PRODUCT_MONTH_UNIQUE",
            codes,
        )


if __name__ == "__main__":
    unittest.main()
