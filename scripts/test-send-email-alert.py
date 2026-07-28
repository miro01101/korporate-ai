#!/usr/bin/env python3
# Unit tests for the SMTP alert helper.

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest import mock
import unittest


MODULE_PATH = Path(__file__).resolve().with_name(
    "send-email-alert.py"
)

SPEC = importlib.util.spec_from_file_location(
    "send_email_alert",
    MODULE_PATH,
)

if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Helper sa nedá načítať.")

MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SendEmailAlertTests(unittest.TestCase):
    @mock.patch.object(MODULE.smtplib, "SMTP")
    def test_starttls_login_and_send(
        self,
        smtp_mock: mock.Mock,
    ) -> None:
        smtp = smtp_mock.return_value.__enter__.return_value

        config = {
            "host": "smtp.example.test",
            "port": 587,
            "username": "robot@example.test",
            "from_email": "robot@example.test",
            "to_emails": ["recipient@example.test"],
            "mode": "starttls",
        }

        MODULE.send_alert(
            config=config,
            password="secret",
            subject="Test",
            body="Test body",
        )

        smtp_mock.assert_called_once_with(
            host="smtp.example.test",
            port=587,
            timeout=30,
        )
        self.assertEqual(smtp.ehlo.call_count, 2)
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with(
            "robot@example.test",
            "secret",
        )
        smtp.send_message.assert_called_once()

    @mock.patch.object(MODULE.smtplib, "SMTP_SSL")
    def test_ssl_mode_is_still_supported(
        self,
        smtp_ssl_mock: mock.Mock,
    ) -> None:
        smtp = smtp_ssl_mock.return_value.__enter__.return_value

        config = {
            "host": "smtp.example.test",
            "port": 465,
            "username": "robot@example.test",
            "from_email": "robot@example.test",
            "to_emails": ["recipient@example.test"],
            "mode": "ssl",
        }

        MODULE.send_alert(
            config=config,
            password="secret",
            subject="Test",
            body="Test body",
        )

        smtp_ssl_mock.assert_called_once()
        smtp.login.assert_called_once_with(
            "robot@example.test",
            "secret",
        )
        smtp.send_message.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
