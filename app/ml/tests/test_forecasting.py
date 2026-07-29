from __future__ import annotations

import unittest

import numpy as np

from ml_pipeline.forecasting import (
    MODEL_NAMES,
    croston_sba_forecast,
    forecast_candidate,
    forecast_quantiles_from_backtest,
    select_baseline_model,
    tsb_forecast,
)


class ForecastingTests(unittest.TestCase):
    def test_intermittent_forecasts_are_nonnegative(self) -> None:
        history = np.array(
            [
                0, 0, 10, 0, 0, 0,
                8, 0, 0, 12, 0, 0,
            ],
            dtype=float,
        )

        self.assertGreaterEqual(
            croston_sba_forecast(history),
            0.0,
        )

        self.assertGreaterEqual(
            tsb_forecast(history),
            0.0,
        )

    def test_all_candidates_return_finite_values(self) -> None:
        history = np.array(
            [10, 12, 11, 13, 12, 14] * 5,
            dtype=float,
        )

        for model_name in MODEL_NAMES:
            forecast = forecast_candidate(
                model_name,
                history,
            )

            self.assertTrue(np.isfinite(forecast))
            self.assertGreaterEqual(forecast, 0.0)

    def test_selector_returns_registered_model(self) -> None:
        history = np.array(
            [10, 12, 11, 13, 12, 14] * 6,
            dtype=float,
        )

        selection = select_baseline_model(history)

        self.assertIn(
            selection.selected_model,
            MODEL_NAMES,
        )

        self.assertEqual(
            len(selection.metrics),
            len(MODEL_NAMES),
        )

    def test_quantiles_are_monotonic(self) -> None:
        history = np.array(
            [10, 12, 11, 13, 12, 14] * 6,
            dtype=float,
        )

        selection = select_baseline_model(history)

        p10, p50, p90 = (
            forecast_quantiles_from_backtest(
                history,
                selection.selected_model,
            )
        )

        self.assertGreaterEqual(p10, 0.0)
        self.assertGreaterEqual(p50, p10)
        self.assertGreaterEqual(p90, p50)


if __name__ == "__main__":
    unittest.main()
