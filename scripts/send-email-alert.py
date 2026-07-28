#!/usr/bin/env python3
# Send an operational alert through authenticated SMTP.

from __future__ import annotations

import argparse
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
import json
from pathlib import Path
import smtplib
import ssl
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--password-file", type=Path, required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body-file", type=Path, required=True)
    return parser.parse_args()


def read_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"SMTP config neexistuje: {path}")

    config = json.loads(path.read_text(encoding="utf-8"))

    required = {
        "host",
        "port",
        "username",
        "from_email",
        "to_emails",
        "mode",
    }

    missing = sorted(required - config.keys())
    if missing:
        raise RuntimeError(
            "SMTP config nemá polia: " + ", ".join(missing)
        )

    if config["mode"] not in {"ssl", "starttls"}:
        raise RuntimeError(
            "SMTP mode musí byť ssl alebo starttls."
        )

    recipients = config["to_emails"]
    if not isinstance(recipients, list) or not recipients:
        raise RuntimeError("to_emails musí byť neprázdny zoznam.")

    return config


def read_password(path: Path) -> str:
    if not path.is_file():
        raise RuntimeError(
            f"SMTP password file neexistuje: {path}"
        )

    password = path.read_text(encoding="utf-8").rstrip("\r\n")
    if not password:
        raise RuntimeError("SMTP password file je prázdny.")

    return password


def build_message(
    config: dict[str, Any],
    subject: str,
    body: str,
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = str(config["from_email"])
    message["To"] = ", ".join(config["to_emails"])
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(
        domain=str(config["from_email"]).split("@", 1)[-1]
    )
    message.set_content(body)
    return message


def send_alert(
    config: dict[str, Any],
    password: str,
    subject: str,
    body: str,
) -> None:
    message = build_message(config, subject, body)
    context = ssl.create_default_context()

    if config["mode"] == "ssl":
        with smtplib.SMTP_SSL(
            host=str(config["host"]),
            port=int(config["port"]),
            timeout=30,
            context=context,
        ) as smtp:
            smtp.login(str(config["username"]), password)
            smtp.send_message(message)
        return

    with smtplib.SMTP(
        host=str(config["host"]),
        port=int(config["port"]),
        timeout=30,
    ) as smtp:
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.ehlo()
        smtp.login(str(config["username"]), password)
        smtp.send_message(message)


def main() -> int:
    args = parse_args()
    config = read_config(args.config)
    password = read_password(args.password_file)

    if not args.body_file.is_file():
        raise RuntimeError(
            f"Body file neexistuje: {args.body_file}"
        )

    body = args.body_file.read_text(
        encoding="utf-8",
        errors="replace",
    )

    send_alert(
        config=config,
        password=password,
        subject=args.subject,
        body=body,
    )

    print("EMAIL_ALERT_SENT=ANO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
