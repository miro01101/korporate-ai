#!/usr/bin/env python3
"""Unit tests for the full-workbook merge helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path("/app/scripts/merge-and-promote-staging.py")
SPEC = importlib.util.spec_from_file_location("full_merge", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Cannot load merge module")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FullWorkbookMergeTests(unittest.TestCase):
    def test_single_supplier_keeps_source_id(self) -> None:
        self.assertEqual(
            MODULE.normalized_purchase_order_id("PO-1", "Supplier A", 1),
            "PO-1",
        )

    def test_multi_supplier_id_is_deterministic(self) -> None:
        first = MODULE.normalized_purchase_order_id(
            "PO-1", " Supplier A ", 2
        )
        second = MODULE.normalized_purchase_order_id(
            "PO-1", "supplier a", 2
        )
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("PO-1--"))
        self.assertEqual(len(first.rsplit("--", 1)[1]), 12)

    def test_different_suppliers_receive_different_ids(self) -> None:
        self.assertNotEqual(
            MODULE.normalized_purchase_order_id("PO-1", "Supplier A", 2),
            MODULE.normalized_purchase_order_id("PO-1", "Supplier B", 2),
        )

    def test_append_only_core_rules_are_excluded(self) -> None:
        codes = {rule[0] for rule in MODULE.BASE_VALIDATIONS}
        self.assertTrue(MODULE.EXCLUDED_BASE_RULES.isdisjoint(codes))

    def test_no_delete_from_core_in_source(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8").lower()
        self.assertNotIn("delete from core.", source)

    def test_merge_mode_is_declared(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn('"merge_mode": "full_workbook_v1"', source)
        self.assertIn('"automatic_deletes": 0', source)

    def test_supplier_count_uses_grouped_cte(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("count(DISTINCT supplier) OVER", source)
        self.assertIn("WITH supplier_counts AS (", source)
        self.assertIn(
            "count(DISTINCT supplier)::integer AS supplier_count",
            source,
        )

    def test_validation_queries_use_tuple_rows(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "from psycopg.rows import dict_row, tuple_row",
            source,
        )
        self.assertIn("row_factory=tuple_row", source)
        self.assertIn("expected exactly 6.", source)

    def test_validation_row_shape_guard(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "returned 4 validation columns; expected exactly 6",
        ):
            MODULE.add_issue(
                None,
                batch_id=None,
                severity="ERROR",
                rule_code="TEST-E001",
                sheet_name="fixture",
                row=(1, "key", "column", "value"),
            )

    def test_existing_sales_line_conflict_is_warning(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn('"MERGE-E011"', source)
        self.assertIn('"MERGE-W011"', source)
        self.assertIn(
            'severity = "WARNING" if is_warning else "ERROR"',
            source,
        )
        self.assertIn("warning_rule_counts", source)

    def test_existing_sales_line_is_not_updated(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn(
            "UPDATE core.sales_order_lines",
            source,
        )
        self.assertIn(
            "LEFT JOIN core.sales_order_lines c",
            source,
        )
        self.assertIn("AND c.id IS NULL", source)

    def test_all_positional_fetches_use_tuple_rows(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("cursor.fetchone()[0]", source)
        self.assertNotIn(
            "inserted, updated, unchanged = cursor.fetchone()",
            source,
        )
        self.assertGreaterEqual(
            source.count("row_factory=tuple_row"),
            3,
        )
        self.assertIn(
            "Scalar query must return exactly one row with one column.",
            source,
        )
        self.assertIn(
            "Merge classification must return exactly",
            source,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
