from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from ml_pipeline.lightgbm_challenger import (
    build_challenger_outputs,
    prepare_model_frame,
)


class LightGBMChallengerTests(unittest.TestCase):
    def feature_frame(self) -> pd.DataFrame:
        months = pd.date_range(
            "2023-01-01",
            periods=36,
            freq="MS",
        )

        rows: list[dict[str, object]] = []

        for product_number in range(3):
            product_id = f"P{product_number + 1}"

            values = np.asarray(
                [
                    max(
                        0.0,
                        10.0
                        + 3.0 * product_number
                        + 2.0
                        * np.sin(
                            2.0
                            * np.pi
                            * month.month
                            / 12.0
                        )
                        + (
                            5.0
                            if (
                                product_number == 2
                                and index % 4 == 0
                            )
                            else 0.0
                        ),
                    )
                    for index, month
                    in enumerate(months)
                ],
                dtype=float,
            )

            series = pd.Series(values)

            for index, month in enumerate(months):
                shifted = series.iloc[:index]

                def lag(position: int) -> float:
                    if index < position:
                        return float("nan")

                    return float(
                        series.iloc[index - position]
                    )

                def rolling_mean(
                    window: int,
                ) -> float:
                    if shifted.empty:
                        return float("nan")

                    return float(
                        shifted.iloc[-window:].mean()
                    )

                def rolling_std(
                    window: int,
                ) -> float:
                    if len(shifted) < 2:
                        return float("nan")

                    return float(
                        shifted.iloc[-window:].std(
                            ddof=0
                        )
                    )

                rows.append(
                    {
                        "product_id": product_id,
                        "month_start": month,
                        "category": (
                            "A"
                            if product_number < 2
                            else "B"
                        ),
                        "supplier_id": (
                            product_number + 1
                        ),
                        "purchase_price": (
                            5.0 + product_number
                        ),
                        "sales_price": (
                            8.0 + product_number
                        ),
                        "lead_time_days": 14,
                        "minimum_order_quantity": 5,
                        "units_sold": values[index],
                        "lag_1": lag(1),
                        "lag_2": lag(2),
                        "lag_3": lag(3),
                        "lag_6": lag(6),
                        "lag_12": lag(12),
                        "rolling_mean_3": (
                            rolling_mean(3)
                        ),
                        "rolling_mean_6": (
                            rolling_mean(6)
                        ),
                        "rolling_mean_12": (
                            rolling_mean(12)
                        ),
                        "rolling_std_3": (
                            rolling_std(3)
                        ),
                        "rolling_std_6": (
                            rolling_std(6)
                        ),
                        "is_cold_start": False,
                    }
                )

        return pd.DataFrame(rows)

    def test_prepared_frame_has_safe_columns(
        self,
    ) -> None:
        frame, levels = prepare_model_frame(
            self.feature_frame()
        )

        self.assertEqual(
            frame["product_id"].nunique(),
            3,
        )

        self.assertEqual(
            len(levels["product_id"]),
            3,
        )

        self.assertIn("month_sin", frame.columns)
        self.assertIn("month_cos", frame.columns)
        self.assertIn("time_index", frame.columns)

        self.assertNotIn(
            "zero_ratio_12",
            frame.columns,
        )

    def test_challenger_outputs_are_valid(
        self,
    ) -> None:
        outputs = build_challenger_outputs(
            self.feature_frame(),
            backtest_months=3,
            model_parameters={
                "n_estimators": 20,
                "learning_rate": 0.10,
                "num_leaves": 7,
                "max_depth": 3,
                "min_child_samples": 5,
                "n_jobs": 1,
            },
        )

        self.assertEqual(
            len(outputs.forecasts),
            3 * 3,
        )

        self.assertEqual(
            len(outputs.metrics),
            3 * 8 + 8,
        )

        self.assertEqual(
            outputs.backtest_row_count,
            3 * 3,
        )

        for forecast in outputs.forecasts:
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

        self.assertEqual(
            set(outputs.models),
            {"p10", "p50", "p90"},
        )


if __name__ == "__main__":
    unittest.main()
