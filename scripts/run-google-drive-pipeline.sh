#!/usr/bin/env bash
set -uo pipefail

REPOSITORY_ROOT="$(
  cd "$(dirname "${BASH_SOURCE[0]}")/.."
  pwd
)"
cd "$REPOSITORY_ROOT" || exit 1

IMPORTER_IMAGE="${KORPORATE_IMPORTER_IMAGE:-korporate-ai-importer:0.5.0}"
ML_IMAGE="${KORPORATE_ML_IMAGE:-korporate-ai-ml:0.5.0}"
IMPORT_ROOT="/srv/korporate-ai/imports/manual"

DB_PASSWORD_FILE="$REPOSITORY_ROOT/secrets/postgres_app_password"
SERVICE_ACCOUNT_FILE="$REPOSITORY_ROOT/secrets/google_drive_service_account.json"
FOLDER_ID_FILE="$REPOSITORY_ROOT/secrets/google_drive_folder_id"

SMTP_CONFIG="$REPOSITORY_ROOT/secrets/smtp_alert.json"
SMTP_PASSWORD_FILE="$REPOSITORY_ROOT/secrets/smtp_alert_password"
EMAIL_HELPER="$REPOSITORY_ROOT/scripts/send-email-alert.py"
ML_RUNNER="$REPOSITORY_ROOT/scripts/run-ml-after-import.sh"

LOCK_FILE="/run/lock/korporate-ai-google-drive.lock"
LOG_FILE="$(mktemp)"
ALERT_BODY_FILE="$(mktemp)"
ML_SUMMARY_FILE="$(mktemp)"

cleanup() {
  rm -f "$LOG_FILE" "$ALERT_BODY_FILE" "$ML_SUMMARY_FILE"
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
    echo "Posledných 160 riadkov výstupu:"
    tail -n 160 "$LOG_FILE" 2>/dev/null || true
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

for required_file in \
  "$DB_PASSWORD_FILE" \
  "$SERVICE_ACCOUNT_FILE" \
  "$FOLDER_ID_FILE" \
  "$ML_RUNNER"
do
  if [ ! -s "$required_file" ]; then
    fail_pipeline 2 "chýba required file $required_file"
  fi
done

for image in "$IMPORTER_IMAGE" "$ML_IMAGE"; do
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    fail_pipeline 2 "chýba Docker image $image"
  fi
done

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

while IFS= read -r network_name; do
  [ -n "$network_name" ] || continue
  [ "$network_name" != "$BACKEND_NETWORK" ] || continue

  network_internal="$(
    docker network inspect "$network_name" \
      --format '{{.Internal}}'
  )"

  if [ "$network_internal" = "false" ]; then
    EGRESS_NETWORK="$network_name"
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

if [ "${KORPORATE_PIPELINE_PREFLIGHT_ONLY:-0}" = "1" ]; then
  "$ML_RUNNER" --preflight-only 2>&1 | tee -a "$LOG_FILE"
  preflight_exit="${PIPESTATUS[0]}"

  if [ "$preflight_exit" -ne 0 ]; then
    echo "PIPELINE_PREFLIGHT_ONLY=FAIL" >&2
    exit "$preflight_exit"
  fi

  echo "PIPELINE_PREFLIGHT_ONLY=PASS"
  echo "GOOGLE_DRIVE_WRAPPER_OK=ANO"
  exit 0
fi

if ! mkdir -p \
  "$IMPORT_ROOT/incoming" \
  "$IMPORT_ROOT/archive" \
  "$IMPORT_ROOT/reports" \
  "$IMPORT_ROOT/quarantine"
then
  fail_pipeline 2 "importné adresáre sa nepodarilo pripraviť"
fi

finalize_ml_audit() {
  local status="$1"
  local run_id="$2"
  local error_message="${3:-}"

  local command=(
    docker run
    --rm
    --pull never
    --network "$BACKEND_NETWORK"
    --memory 256m
    --cpus 0.25
    --pids-limit 80
    --cap-drop ALL
    --security-opt no-new-privileges:true
    -v "$DB_PASSWORD_FILE":/run/secrets/postgres_app_password:ro
    -v "$ML_SUMMARY_FILE":/run/ml-summary.json:ro
    --entrypoint python
    "$IMPORTER_IMAGE"
    /app/scripts/finalize-pipeline-ml.py
    --run-id "$run_id"
    --status "$status"
    --summary-file /run/ml-summary.json
    --db-password-file /run/secrets/postgres_app_password
  )

  if [ -n "$error_message" ]; then
    command+=(--error-message "$error_message")
  fi

  "${command[@]}" 2>&1 | tee -a "$LOG_FILE"
}

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
    --defer-completion \
  2>&1 | tee "$LOG_FILE"

PIPELINE_EXIT_CODE="${PIPESTATUS[0]}"
set -e

if [ "$PIPELINE_EXIT_CODE" -ne 0 ]; then
  send_failure_alert \
    "$PIPELINE_EXIT_CODE" \
    "importný orchestrátor vrátil nenulový exit code"
  exit "$PIPELINE_EXIT_CODE"
fi

PIPELINE_STATUS="$(
  grep '^PIPELINE_STATUS=' "$LOG_FILE" |
  tail -n 1 |
  cut -d= -f2-
)"

case "$PIPELINE_STATUS" in
  no_file|skipped_duplicate)
    echo "GOOGLE_DRIVE_WRAPPER_OK=ANO"
    exit 0
    ;;

  ready_for_ml)
    PIPELINE_RUN_ID="$(
      grep '^PIPELINE_RUN_ID=' "$LOG_FILE" |
      tail -n 1 |
      cut -d= -f2-
    )"

    if [ -z "$PIPELINE_RUN_ID" ]; then
      fail_pipeline 2 "ready_for_ml bez PIPELINE_RUN_ID"
    fi
    ;;

  *)
    fail_pipeline 2 "neočakávaný pipeline status: ${PIPELINE_STATUS:-EMPTY}"
    ;;
esac

set +e
"$ML_RUNNER" \
  --pipeline-run-id "$PIPELINE_RUN_ID" \
  --summary-file "$ML_SUMMARY_FILE" \
  2>&1 | tee -a "$LOG_FILE"
ML_EXIT_CODE="${PIPESTATUS[0]}"
set -e

if [ "$ML_EXIT_CODE" -ne 0 ]; then
  if [ ! -s "$ML_SUMMARY_FILE" ]; then
    printf '%s\n' \
      '{"status":"failed","failed_stage":"runner","exit_code":1,"stages":{},"automatic_ordering":false,"human_approval_required":true}' \
      > "$ML_SUMMARY_FILE"
  fi

  set +e
  finalize_ml_audit \
    failed \
    "$PIPELINE_RUN_ID" \
    "ML orchestrátor vrátil exit code $ML_EXIT_CODE"
  FINALIZE_EXIT_CODE="${PIPESTATUS[0]}"
  set -e

  if [ "$FINALIZE_EXIT_CODE" -ne 0 ]; then
    fail_pipeline \
      "$FINALIZE_EXIT_CODE" \
      "ML zlyhalo a audit run sa nepodarilo finalizovať"
  fi

  fail_pipeline \
    "$ML_EXIT_CODE" \
    "ML orchestrátor vrátil nenulový exit code"
fi

set +e
finalize_ml_audit completed "$PIPELINE_RUN_ID"
FINALIZE_EXIT_CODE="${PIPESTATUS[0]}"
set -e

if [ "$FINALIZE_EXIT_CODE" -ne 0 ]; then
  fail_pipeline \
    "$FINALIZE_EXIT_CODE" \
    "ML prešlo, ale audit run sa nepodarilo finalizovať"
fi

echo "PIPELINE_STATUS=completed"
echo "ML_ORCHESTRATION=PASS"
echo "GOOGLE_DRIVE_WRAPPER_OK=ANO"
exit 0
