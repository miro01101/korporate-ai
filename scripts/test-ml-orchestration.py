#!/usr/bin/env python3
"""Regression tests for Google Drive to ML orchestration helpers."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parent.parent
HELPER_PATH = REPOSITORY / "scripts/finalize-pipeline-ml.py"
RUNNER_PATH = REPOSITORY / "scripts/run-ml-after-import.sh"
WRAPPER_PATH = REPOSITORY / "scripts/run-google-drive-pipeline.sh"

SPEC = importlib.util.spec_from_file_location(
    "finalize_pipeline_ml",
    HELPER_PATH,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Cannot load finalize-pipeline-ml.py")

MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MLOrchestrationTests(unittest.TestCase):
    def test_valid_completed_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.json"
            path.write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "stages": {"validate": {}},
                    }
                ),
                encoding="utf-8",
            )
            payload = MODULE.load_summary(path)
            self.assertEqual(payload["status"], "completed")

    def test_invalid_summary_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.json"
            path.write_text(
                json.dumps(
                    {
                        "status": "running",
                        "stages": {},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeError):
                MODULE.load_summary(path)

    def test_stage_order_is_complete(self) -> None:
        source = RUNNER_PATH.read_text(encoding="utf-8")
        positions = [
            source.index(stage)
            for stage in (
                "validate",
                "build-features",
                "train-demand",
                "train-lightgbm",
                "select-hybrid",
                "calibrate-intervals",
                "build-inventory-risk",
                "build-recommendations",
            )
        ]
        self.assertEqual(positions, sorted(positions))

    def test_wrapper_defers_completion(self) -> None:
        source = WRAPPER_PATH.read_text(encoding="utf-8")
        self.assertIn("--defer-completion", source)
        self.assertIn("ready_for_ml", source)
        self.assertIn("finalize-pipeline-ml.py", source)

    def test_no_automatic_ordering_command(self) -> None:
        source = RUNNER_PATH.read_text(encoding="utf-8").lower()
        forbidden = (
            "create-purchase-order",
            "place-order",
            "execute-order",
            "automatic-order",
        )
        for token in forbidden:
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
