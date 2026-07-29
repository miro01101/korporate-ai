from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from ml_pipeline.interval_calibration import (
    AGGREGATE_METRIC_HORIZON,
    apply_qhat,
    finite_sample_qhat,
    readiness_decision,
)


class IntervalCalibrationTests(unittest.TestCase):
    def test_aggregate_metric_horizon_respects_schema(
        self,
    ) -> None:
        self.assertGreaterEqual(
            AGGREGATE_METRIC_HORIZON,
            1,
        )

    def test_qhat_is_nonnegative(self) -> None:
        scores = np.asarray(
            [-2.0, -1.0, 0.0, 1.0, 3.0]
        )

        qhat = finite_sample_qhat(
            scores,
            alpha=0.20,
        )

        self.assertGreaterEqual(qhat, 0.0)

    def test_apply_qhat_preserves_order(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "source_family": (
                        "baseline_ensemble"
                    ),
                    "horizon": 1,
                    "actual": 12.0,
                    "p10": 8.0,
                    "p50": 10.0,
                    "p90": 11.0,
                }
            ]
        )

        calibrated = apply_qhat(
            frame,
            {
                (
                    "baseline_ensemble",
                    1,
                ): 2.0
            },
        )

        row = calibrated.iloc[0]

        self.assertEqual(
            row["calibrated_p10"],
            6.0,
        )

        self.assertEqual(
            row["calibrated_p50"],
            10.0,
        )

        self.assertEqual(
            row["calibrated_p90"],
            13.0,
        )

        self.assertTrue(bool(row["covered"]))

    def test_readiness_gate_passes(self) -> None:
        rows = []

        for source in (
            "baseline_ensemble",
            "global_lightgbm_quantile",
        ):
            for horizon in (1, 2, 3):
                for index in range(100):
                    rows.append(
                        {
                            "source_family": source,
                            "horizon": horizon,
                            "covered": index < 82,
                        }
                    )

        ready, overall, minimum_horizon, minimum_cell = (
            readiness_decision(
                pd.DataFrame(rows)
            )
        )

        self.assertTrue(ready)
        self.assertGreaterEqual(overall, 0.78)
        self.assertGreaterEqual(
            minimum_horizon,
            0.75,
        )
        self.assertGreaterEqual(
            minimum_cell,
            0.70,
        )

    def test_readiness_gate_blocks_bad_cell(
        self,
    ) -> None:
        rows = []

        for source in (
            "baseline_ensemble",
            "global_lightgbm_quantile",
        ):
            for horizon in (1, 2, 3):
                coverage_count = (
                    60
                    if (
                        source
                        == "global_lightgbm_quantile"
                        and horizon == 3
                    )
                    else 90
                )

                for index in range(100):
                    rows.append(
                        {
                            "source_family": source,
                            "horizon": horizon,
                            "covered": (
                                index < coverage_count
                            ),
                        }
                    )

        ready, _, _, minimum_cell = (
            readiness_decision(
                pd.DataFrame(rows)
            )
        )

        self.assertFalse(ready)
        self.assertLess(minimum_cell, 0.70)


if __name__ == "__main__":
    unittest.main()
