# Google Drive XLSX Automation Runbook

## Účel

Platforma každú hodinu kontroluje jeden vyhradený Google Drive priečinok.
Nový XLSX spracuje cez existujúci validačný, importný a analytický pipeline.
Google Drive integrácia je read-only.

## Ručné spustenie

    systemctl start korporate-ai-google-drive.service

## Stav timera

    systemctl status korporate-ai-google-drive.timer
    systemctl list-timers --all korporate-ai-google-drive.timer

## Logy

    journalctl -u korporate-ai-google-drive.service -n 200 --no-pager

## Audit

    SELECT id, status, attempt_number, source_filename,
           error_stage, error_message, started_at, finished_at
    FROM audit.pipeline_runs
    ORDER BY started_at DESC;

## Stavy

- completed: nový XLSX bol spracovaný,
- skipped_duplicate: rovnaký SHA-256 už existuje,
- rejected: obsahová validačná chyba,
- failed: technická chyba,
- no_file: nie je nová spracovateľná verzia.

## Vypnutie

    systemctl disable --now korporate-ai-google-drive.timer

## Emailový alert

Pri nenulovom návratovom kóde pipeline wrapper odošle jeden email.

SMTP nastavenie:

- host: smtp.m1.websupport.sk
- port: 587
- režim: STARTTLS
- odosielateľ: robot@statslab.eu
- príjemca: miro.adamek@gmail.com

SMTP konfigurácia a heslo sú uložené mimo Gitu:

- secrets/smtp_alert.json
- secrets/smtp_alert_password

Ručný test emailu:

    BODY_FILE="$(mktemp)"
    printf '%s\n' "Korporate AI SMTP test" > "$BODY_FILE"

    python3 scripts/send-email-alert.py \
      --config secrets/smtp_alert.json \
      --password-file secrets/smtp_alert_password \
      --subject "Korporate AI – test SMTP alertu" \
      --body-file "$BODY_FILE"

    rm -f "$BODY_FILE"
