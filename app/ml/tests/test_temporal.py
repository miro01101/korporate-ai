from __future__ import annotations

from datetime import date
import unittest

import pandas as pd

from ml_pipeline.temporal import (
    expected_forecast_months,
    feature_temporal_metadata,
    filter_features_to_training_cutoff,
    training_cutoff_from_metadata,
    validate_forecast_window,
)


class TemporalContractTests(unittest.TestCase):
    def test_inventory_can_extend_panel_beyond_sales_cutoff(self) -> None:
        sales = pd.DataFrame(
            {"month_start": ["2026-05-01", "2026-06-01"]}
        )
        inventory = pd.DataFrame(
            {"month_start": ["2026-06-01", "2026-07-01"]}
        )
        features = pd.DataFrame(
            {
                "product_id": ["P1", "P1", "P1"],
                "month_start": [
                    "2026-05-01",
                    "2026-06-01",
                    "2026-07-01",
                ],
            }
        )

        metadata = feature_temporal_metadata(
            sales,
            inventory,
            features,
        )

        self.assertEqual(
            metadata["sales_source_max_month"],
            "2026-06-01",
        )
        self.assertEqual(
            metadata["inventory_source_max_month"],
            "2026-07-01",
        )
        self.assertEqual(
            metadata["panel_max_month"],
            "2026-07-01",
        )
        self.assertEqual(
            training_cutoff_from_metadata(metadata),
            date(2026, 6, 1),
        )

    def test_training_filter_excludes_inventory_only_month(self) -> None:
        features = pd.DataFrame(
            [
                {"product_id": product, "month_start": month}
                for product in ("P1", "P2")
                for month in (
                    "2026-05-01",
                    "2026-06-01",
                    "2026-07-01",
                )
            ]
        )

        filtered = filter_features_to_training_cutoff(
            features,
            date(2026, 6, 1),
        )

        self.assertEqual(
            filtered["month_start"].max(),
            pd.Timestamp("2026-06-01"),
        )
        self.assertEqual(len(filtered), 4)

    def test_forecast_window_starts_after_sales_cutoff(self) -> None:
        self.assertEqual(
            expected_forecast_months(
                date(2026, 6, 1),
                (1, 2, 3),
            ),
            (
                date(2026, 7, 1),
                date(2026, 8, 1),
                date(2026, 9, 1),
            ),
        )

        forecasts = pd.DataFrame(
            [
                {
                    "product_id": product,
                    "horizon": horizon,
                    "forecast_month": month,
                }
                for product in ("P1", "P2")
                for horizon, month in zip(
                    (1, 2, 3),
                    (
                        date(2026, 7, 1),
                        date(2026, 8, 1),
                        date(2026, 9, 1),
                    ),
                    strict=True,
                )
            ]
        )

        validate_forecast_window(
            forecasts,
            training_cutoff=date(2026, 6, 1),
            horizons=(1, 2, 3),
            label="test forecasts",
        )

        forecasts.loc[forecasts["horizon"] == 1, "forecast_month"] = date(
            2026,
            8,
            1,
        )

        with self.assertRaisesRegex(
            ValueError,
            "does not immediately follow",
        ):
            validate_forecast_window(
                forecasts,
                training_cutoff=date(2026, 6, 1),
                horizons=(1, 2, 3),
                label="test forecasts",
            )


if __name__ == "__main__":
    unittest.main()
