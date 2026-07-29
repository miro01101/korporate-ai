from __future__ import annotations

import unittest

import pandas as pd

from ml_pipeline.hybrid_champion import (
    select_product_models,
)


class HybridSelectionTests(unittest.TestCase):
    def baseline(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "product_id": "P1",
                    "baseline_selected_model": "naive",
                    "is_cold_start": False,
                    "baseline_wape": 1.00,
                    "baseline_bias": 0.10,
                },
                {
                    "product_id": "P2",
                    "baseline_selected_model": (
                        "moving_average_3"
                    ),
                    "is_cold_start": False,
                    "baseline_wape": 1.00,
                    "baseline_bias": 0.05,
                },
                {
                    "product_id": "P3",
                    "baseline_selected_model": (
                        "croston_sba"
                    ),
                    "is_cold_start": False,
                    "baseline_wape": 0.50,
                    "baseline_bias": -0.10,
                },
                {
                    "product_id": "P4",
                    "baseline_selected_model": (
                        "category_cold_start"
                    ),
                    "is_cold_start": True,
                    "baseline_wape": None,
                    "baseline_bias": None,
                },
            ]
        )

    def challenger(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "product_id": "P1",
                    "challenger_wape": 0.90,
                    "challenger_bias": 0.02,
                },
                {
                    "product_id": "P2",
                    "challenger_wape": 0.98,
                    "challenger_bias": 0.01,
                },
                {
                    "product_id": "P3",
                    "challenger_wape": 0.70,
                    "challenger_bias": -0.20,
                },
                {
                    "product_id": "P4",
                    "challenger_wape": 1.10,
                    "challenger_bias": 0.30,
                },
            ]
        )

    def test_only_clear_challenger_win_is_selected(
        self,
    ) -> None:
        selection = select_product_models(
            self.baseline(),
            self.challenger(),
            selection_margin=0.03,
        )

        source = dict(zip(
            selection["product_id"],
            selection["source_family"],
            strict=True,
        ))

        self.assertEqual(
            source["P1"],
            "global_lightgbm_quantile",
        )

        self.assertEqual(
            source["P2"],
            "baseline_ensemble",
        )

        self.assertEqual(
            source["P3"],
            "baseline_ensemble",
        )

        self.assertEqual(
            source["P4"],
            "baseline_ensemble",
        )

    def test_cold_start_never_uses_challenger(
        self,
    ) -> None:
        selection = select_product_models(
            self.baseline(),
            self.challenger(),
        )

        cold = selection[
            selection["product_id"] == "P4"
        ].iloc[0]

        self.assertFalse(
            bool(cold["challenger_selected"])
        )

        self.assertEqual(
            cold["selection_reason"],
            "cold_start_baseline",
        )

    def test_tie_within_margin_keeps_baseline(
        self,
    ) -> None:
        selection = select_product_models(
            self.baseline(),
            self.challenger(),
            selection_margin=0.03,
        )

        tied = selection[
            selection["product_id"] == "P2"
        ].iloc[0]

        self.assertEqual(
            tied["source_family"],
            "baseline_ensemble",
        )

        self.assertEqual(
            tied["selected_model"],
            "moving_average_3",
        )


if __name__ == "__main__":
    unittest.main()
