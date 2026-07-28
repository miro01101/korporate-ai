#!/usr/bin/env bash
set -uo pipefail

REPOSITORY_ROOT="$(
  cd "$(dirname "${BASH_SOURCE[0]}")/.."
  pwd
)"
cd "$REPOSITORY_ROOT" || exit 1

IMPORTER_IMAGE="${KORPORATE_IMPORTER_IMAGE:-korporate-ai-importer:0.4.0}"
IMPORT_ROOT="/srv/korporate-ai/imports/manual"

DB_PASSWORD_FILE="$REPOSITORY_ROOT/secrets/postgres_app_password"
SERVICE_ACCOUNT_FILE="$REPOSITORY_ROOT/secrets/google_drive_service_account.json"
FOLDER_ID_FILE="$REPOSITORY_ROOT/secrets/google_drive_folder_id"

SMTP_CONFIG="$REPOSITORY_ROOT/secrets/smtp_alert.json"
SMTP_PASSWORD_FILE="$REPOSITORY_ROOT/secrets/smtp_alert_password"
EMAIL_HELPER="$REPOSITORY_ROOT/scripts/send-email-alert.py"

LOCK_FILE="/run/lock/korporate-ai-google-drive.lock"
LOG_FILE="$(mktemp)"
ALERT_BODY_FILE="$(mktemp)"

cleanup() {
  rm -f "$LOG_FILE" "$ALERT_BODY_FILE"
}

trap cleanup EXIT

send_failure_alert() {
  local exit_code="$1"
  local reason="$2"

  {
    echo "Korporate AI Google Drive pipeline zlyhal."
    echo
    echo "Server: $(hostname)"
    echo "Čas UTC: $(date -u -Is)"
    echo "Exit code: $exit_code"
    echo "Dôvod: $reason"
    echo
    echo "Posledných 120 riadkov výstupu:"
    tail -n 120 "$LOG_FILE" 2>/dev/null || true
  } > "$ALERT_BODY_FILE"

  if \
    [ -s "$SMTP_CONFIG" ] &&
    [ -s "$SMTP_PASSWORD_FILE" ] &&
    [ -x "$EMAIL_HELPER" ]
  then
    if python3 "$EMAIL_HELPER" \
      --config "$SMTP_CONFIG" \
      --password-file "$SMTP_PASSWORD_FILE" \
      --subject "Korporate AI – Google Drive pipeline zlyhal" \
      --body-file "$ALERT_BODY_FILE"
    then
      echo "PIPELINE_EMAIL_ALERT=sent"
    else
      echo "PIPELINE_EMAIL_ALERT=failed" >&2
    fi
  else
    echo "PIPELINE_EMAIL_ALERT=not_configured" >&2
  fi
}

fail_pipeline() {
  local exit_code="$1"
  shift
  local reason="$*"

  echo "CHYBA: $reason" | tee -a "$LOG_FILE" >&2
  send_failure_alert "$exit_code" "$reason"
  exit "$exit_code"
}

exec 9>"$LOCK_FILE"

if ! flock -n 9; then
  echo "PIPELINE_SKIPPED=host_lock_not_acquired"
  exit 0
fi

for REQUIRED_FILE in \
  "$DB_PASSWORD_FILE" \
  "$SERVICE_ACCOUNT_FILE" \
  "$FOLDER_ID_FILE"
do
  if [ ! -s "$REQUIRED_FILE" ]; then
    fail_pipeline 2 "chýba required file $REQUIRED_FILE"
  fi
done

if ! docker image inspect "$IMPORTER_IMAGE" >/dev/null 2>&1; then
  fail_pipeline 2 "chýba Docker image $IMPORTER_IMAGE"
fi

POSTGRES_CID="$(docker compose ps -q postgres)"
API_CID="$(docker compose ps -q api)"

if [ -z "$POSTGRES_CID" ] || [ -z "$API_CID" ]; then
  fail_pipeline 2 "PostgreSQL alebo API kontajner nebeží"
fi

BACKEND_NETWORK="$(
  docker inspect "$POSTGRES_CID" \
    --format \
    '{{range $name, $settings := .NetworkSettings.Networks}}{{println $name}}{{end}}' |
  grep '_backend$' |
  head -n 1
)"

if [ -z "$BACKEND_NETWORK" ]; then
  fail_pipeline 2 "backend Docker network nebol nájdený"
fi

EGRESS_NETWORK=""

while IFS= read -r NETWORK_NAME; do
  [ -n "$NETWORK_NAME" ] || continue
  [ "$NETWORK_NAME" != "$BACKEND_NETWORK" ] || continue

  NETWORK_INTERNAL="$(
    docker network inspect "$NETWORK_NAME" \
      --format '{{.Internal}}'
  )"

  if [ "$NETWORK_INTERNAL" = "false" ]; then
    EGRESS_NETWORK="$NETWORK_NAME"
    break
  fi
done < <(
  docker inspect "$API_CID" \
    --format \
    '{{range $name, $settings := .NetworkSettings.Networks}}{{println $name}}{{end}}'
)

if [ -z "$EGRESS_NETWORK" ]; then
  fail_pipeline 2 "egress Docker network nebol nájdený"
fi

if ! mkdir -p \
  "$IMPORT_ROOT/incoming" \
  "$IMPORT_ROOT/archive" \
  "$IMPORT_ROOT/reports" \
  "$IMPORT_ROOT/quarantine"
then
  fail_pipeline 2 "importné adresáre sa nepodarilo pripraviť"
fi

set +e

docker run --rm \
  --pull never \
  --network "name=$EGRESS_NETWORK,gw-priority=1" \
  --network "$BACKEND_NETWORK" \
  --memory 1024m \
  --cpus 1.0 \
  --pids-limit 180 \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  -v "$IMPORT_ROOT":/imports \
  -v "$DB_PASSWORD_FILE":/run/secrets/postgres_app_password:ro \
  -v "$SERVICE_ACCOUNT_FILE":/run/secrets/google_drive_service_account.json:ro \
  -v "$FOLDER_ID_FILE":/run/secrets/google_drive_folder_id:ro \
  --entrypoint python \
  "$IMPORTER_IMAGE" \
  /app/scripts/google-drive-pipeline.py \
    --service-account-file \
      /run/secrets/google_drive_service_account.json \
    --folder-id-file \
      /run/secrets/google_drive_folder_id \
    --import-root /imports \
    --db-password-file \
      /run/secrets/postgres_app_password \
    --created-by google-drive-service-account \
    --max-attempts 3 \
  2>&1 | tee "$LOG_FILE"

PIPELINE_EXIT_CODE="${PIPESTATUS[0]}"

set -e

if [ "$PIPELINE_EXIT_CODE" -ne 0 ]; then
  send_failure_alert \
    "$PIPELINE_EXIT_CODE" \
    "orchestrátor vrátil nenulový exit code"

  exit "$PIPELINE_EXIT_CODE"
fi

echo "GOOGLE_DRIVE_WRAPPER_OK=ANO"
exit 0
