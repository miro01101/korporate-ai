from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from ml_pipeline.demand import (
    FORECAST_HORIZON_MONTHS,
    build_demand_outputs,
)
from ml_pipeline.temporal import (
    filter_features_to_training_cutoff,
)


class DemandOutputTests(unittest.TestCase):
    def feature_frame(self) -> pd.DataFrame:
        months = pd.date_range(
            "2023-01-01",
            periods=36,
            freq="MS",
        )

        rows: list[dict[str, object]] = []

        stable = [
            10.0 + float(index % 3)
            for index in range(len(months))
        ]

        intermittent = [
            8.0 if index % 4 == 0 else 0.0
            for index in range(len(months))
        ]

        cold = [
            0.0
            for _ in range(len(months))
        ]

        cold[-1] = 4.0

        for product_id, category, values, cold_start in (
            ("P1", "A", stable, False),
            ("P2", "B", intermittent, False),
            ("P3", "A", cold, True),
        ):
            for month, units in zip(
                months,
                values,
                strict=True,
            ):
                rows.append(
                    {
                        "product_id": product_id,
                        "month_start": month,
                        "category": category,
                        "units_sold": units,
                        "is_cold_start": cold_start,
                    }
                )

        return pd.DataFrame(rows)

    def test_forecast_count_and_horizons(self) -> None:
        output = build_demand_outputs(
            self.feature_frame()
        )

        self.assertEqual(
            len(output.forecasts),
            3 * FORECAST_HORIZON_MONTHS,
        )

        forecasts = pd.DataFrame(output.forecasts)

        for _, group in forecasts.groupby(
            "product_id"
        ):
            self.assertEqual(
                list(group["horizon"]),
                [1, 2, 3],
            )

    def test_forecast_quantiles_are_valid(self) -> None:
        output = build_demand_outputs(
            self.feature_frame()
        )

        for forecast in output.forecasts:
            self.assertGreaterEqual(
                forecast["forecast_p10"],
                0.0,
            )

            self.assertGreaterEqual(
                forecast["forecast_p50"],
                forecast["forecast_p10"],
            )

            self.assertGreaterEqual(
                forecast["forecast_p90"],
                forecast["forecast_p50"],
            )

            self.assertGreaterEqual(
                forecast["confidence_score"],
                0.0,
            )

            self.assertLessEqual(
                forecast["confidence_score"],
                1.0,
            )

    def test_cold_start_uses_category_model(self) -> None:
        output = build_demand_outputs(
            self.feature_frame()
        )

        cold_forecasts = [
            forecast
            for forecast in output.forecasts
            if forecast["product_id"] == "P3"
        ]

        self.assertEqual(len(cold_forecasts), 3)

        self.assertTrue(
            all(
                forecast["selected_model"]
                == "category_cold_start"
                for forecast in cold_forecasts
            )
        )

        self.assertTrue(
            all(
                forecast["is_cold_start"]
                for forecast in cold_forecasts
            )
        )

    def test_inventory_only_month_is_not_a_demand_target(self) -> None:
        features = self.feature_frame()
        inventory_only = features[
            features["month_start"]
            == features["month_start"].max()
        ].copy()
        inventory_only["month_start"] = pd.Timestamp(
            "2026-01-01"
        )
        inventory_only["units_sold"] = 0.0

        panel = pd.concat(
            [features, inventory_only],
            ignore_index=True,
        )

        training = filter_features_to_training_cutoff(
            panel,
            pd.Timestamp("2025-12-01"),
        )
        output = build_demand_outputs(training)
        forecast_months = sorted(
            {row["forecast_month"] for row in output.forecasts}
        )

        self.assertEqual(
            forecast_months,
            [
                pd.Timestamp("2026-01-01").date(),
                pd.Timestamp("2026-02-01").date(),
                pd.Timestamp("2026-03-01").date(),
            ],
        )

    def test_metrics_include_aggregate_rows(self) -> None:
        output = build_demand_outputs(
            self.feature_frame()
        )

        aggregate_metrics = [
            metric
            for metric in output.metrics
            if metric["product_id"] is None
        ]

        self.assertGreaterEqual(
            len(aggregate_metrics),
            18,
        )

        self.assertTrue(
            np.isfinite(
                output.median_selected_wape
            )
        )


if __name__ == "__main__":
    unittest.main()
