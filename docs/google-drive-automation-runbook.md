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
