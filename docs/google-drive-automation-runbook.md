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


## ML orchestration

Po úspešnom importe a transakčnom mart refreshi ostane konkrétny
`audit.pipeline_runs` riadok dočasne v stave `running`. Hostiteľský wrapper
následne pod samostatným ML lockom vykoná:

1. `validate`,
2. `build-features`,
3. `train-demand`,
4. `train-lightgbm`,
5. `select-hybrid`,
6. `calibrate-intervals`,
7. `build-inventory-risk`,
8. `build-recommendations`.

Až po úspechu všetkých krokov a dynamických API/dashboard smoke testov sa
pipeline run zmení na `completed`. Pri chybe sa zmení na `failed`, uloží
`error_stage`, ML summary do `metadata.ml` a wrapper odošle jeden email.

ML vrstva nevytvára nákupné objednávky. Recommendations zostávajú
`pending` a vyžadujú ľudské schválenie.

Ručný read-only preflight:

    scripts/run-ml-after-import.sh --preflight-only

Manuálny ML príkaz používa rovnaký non-blocking lock:

    scripts/run-ml-pipeline.sh self-check
