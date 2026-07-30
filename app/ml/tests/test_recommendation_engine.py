from __future__ import annotations

from datetime import date
import unittest
from uuid import UUID

import pandas as pd

from ml_pipeline.recommendation_engine import (
    EXPEDITE,
    HOLD,
    PURCHASE,
    REDUCE_DEFER,
    REVIEW,
    build_recommendations,
    classify_recommendation,
    deterministic_recommendation_id,
    recommendation_priority,
)


class RecommendationEngineTests(unittest.TestCase):
    def row(self, **overrides: object) -> pd.Series:
        values: dict[str, object] = {
            "model_run_id": UUID("2425d5eb-371f-48d1-9d60-a65bcf614d74"),
            "product_id": "P-001",
            "as_of_date": date(2025, 12, 1),
            "stock_available": 10,
            "incoming_quantity": 0,
            "reorder_point": 20.0,
            "stockout_probability_30d": 0.60,
            "stockout_probability_60d": 0.70,
            "stockout_probability_90d": 0.80,
            "overstock_probability_90d": 0.00,
            "recommended_order_quantity": 12,
            "recommended_order_date": date(2025, 12, 1),
            "minimum_order_quantity": 4,
            "max_stock": 100,
            "abc_class": "A",
            "xyz_class": "Y",
            "selected_model": "moving_average_3",
            "is_cold_start": False,
            "confidence_score": 0.60,
            "earliest_incoming": None,
        }
        values.update(overrides)
        return pd.Series(values)

    def test_expedite_precedes_purchase(self) -> None:
        row = self.row(incoming_quantity=5)
        self.assertEqual(classify_recommendation(row), EXPEDITE)

    def test_purchase_when_reorder_quantity_is_positive(self) -> None:
        self.assertEqual(classify_recommendation(self.row()), PURCHASE)

    def test_reduce_defer_when_overstock_is_high(self) -> None:
        row = self.row(
            recommended_order_quantity=0,
            recommended_order_date=None,
            overstock_probability_90d=0.60,
        )
        self.assertEqual(classify_recommendation(row), REDUCE_DEFER)

    def test_review_for_cold_start_without_other_action(self) -> None:
        row = self.row(
            recommended_order_quantity=0,
            recommended_order_date=None,
            stockout_probability_30d=0.10,
            overstock_probability_90d=0.00,
            is_cold_start=True,
        )
        self.assertEqual(classify_recommendation(row), REVIEW)

    def test_hold_when_no_action_is_required(self) -> None:
        row = self.row(
            recommended_order_quantity=0,
            recommended_order_date=None,
            stockout_probability_30d=0.10,
            overstock_probability_90d=0.00,
        )
        self.assertEqual(classify_recommendation(row), HOLD)

    def test_deterministic_id_is_stable(self) -> None:
        model_run_id = UUID("2425d5eb-371f-48d1-9d60-a65bcf614d74")
        first = deterministic_recommendation_id(
            model_run_id,
            "P-001",
        )
        second = deterministic_recommendation_id(
            model_run_id,
            "P-001",
        )
        other = deterministic_recommendation_id(
            model_run_id,
            "P-002",
        )
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)

    def test_priority_bounds(self) -> None:
        row = self.row(
            stockout_probability_30d=0.95,
            stockout_probability_60d=0.99,
            stockout_probability_90d=1.00,
        )
        priority = recommendation_priority(PURCHASE, row)
        self.assertGreaterEqual(priority, 50)
        self.assertLessEqual(priority, 99)

    def test_build_requires_exact_product_count(self) -> None:
        frame = pd.DataFrame([self.row()])
        with self.assertRaisesRegex(
            ValueError,
            "Expected 80 products",
        ):
            build_recommendations(frame)


if __name__ == "__main__":
    unittest.main()
