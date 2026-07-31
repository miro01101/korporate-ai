#!/usr/bin/env python3
"""Small regression tests for the Google Drive pipeline helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from unittest import mock
import unittest

import requests


MODULE_PATH = Path("/app/scripts/google-drive-pipeline.py")
SPEC = importlib.util.spec_from_file_location(
    "google_drive_pipeline",
    MODULE_PATH,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Cannot load google-drive-pipeline.py")

MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class GoogleDrivePipelineTests(unittest.TestCase):
    def test_folder_query(self) -> None:
        query = MODULE.folder_query("abc-DEF_123")
        self.assertIn("'abc-DEF_123' in parents", query)
        self.assertIn("trashed = false", query)

    def test_invalid_folder_id(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.folder_query("bad folder id")

    def test_safe_filename(self) -> None:
        self.assertEqual(
            MODULE.safe_filename("../Denný report č. 1.xlsx"),
            "Denn_report_._1.xlsx",
        )

    def test_timestamp(self) -> None:
        parsed = MODULE.parse_timestamp("2026-07-28T17:00:00Z")
        self.assertEqual(parsed.utcoffset().total_seconds(), 0)

    def test_validation_failure_is_rejected(self) -> None:
        status = MODULE.import_failure_status(
            "PIPELINE_STEP_FAILED=validate-workbook-values.py EXIT_CODE=1",
            None,
        )
        self.assertEqual(status, "rejected")

    def test_technical_failure_is_failed(self) -> None:
        self.assertEqual(
            MODULE.import_failure_status("connection refused", None),
            "failed",
        )

    def test_defer_completion_argument(self) -> None:
        argv = [
            "google-drive-pipeline.py",
            "--service-account-file",
            "/tmp/service.json",
            "--folder-id-file",
            "/tmp/folder-id",
            "--import-root",
            "/tmp/imports",
            "--db-password-file",
            "/tmp/db-password",
            "--defer-completion",
        ]
        with mock.patch.object(sys, "argv", argv):
            args = MODULE.parse_args()
        self.assertTrue(args.defer_completion)

    @mock.patch.object(MODULE.time, "sleep")
    def test_transport_error_is_retried(
        self,
        sleep_mock: mock.Mock,
    ) -> None:
        response = mock.Mock()
        response.status_code = 200

        session = mock.Mock()
        session.request.side_effect = [
            requests.ConnectionError("dns failure"),
            response,
        ]

        actual = MODULE.request_with_retry(
            session,
            "GET",
            "https://example.test",
        )

        self.assertIs(actual, response)
        self.assertEqual(session.request.call_count, 2)
        sleep_mock.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
