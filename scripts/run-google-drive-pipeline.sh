#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(
  cd "$(dirname "${BASH_SOURCE[0]}")/.."
  pwd
)"
cd "$REPOSITORY_ROOT"

IMPORTER_IMAGE="${KORPORATE_IMPORTER_IMAGE:-korporate-ai-importer:0.4.0}"
IMPORT_ROOT="/srv/korporate-ai/imports/manual"
DB_PASSWORD_FILE="$REPOSITORY_ROOT/secrets/postgres_app_password"
SERVICE_ACCOUNT_FILE="$REPOSITORY_ROOT/secrets/google_drive_service_account.json"
FOLDER_ID_FILE="$REPOSITORY_ROOT/secrets/google_drive_folder_id"
LOCK_FILE="/run/lock/korporate-ai-google-drive.lock"

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
  if [ ! -f "$REQUIRED_FILE" ]; then
    echo "CHYBA: chýba required file $REQUIRED_FILE" >&2
    exit 2
  fi
done

if ! docker image inspect "$IMPORTER_IMAGE" >/dev/null 2>&1; then
  echo "CHYBA: chýba image $IMPORTER_IMAGE" >&2
  exit 2
fi

POSTGRES_CID="$(docker compose ps -q postgres)"
if [ -z "$POSTGRES_CID" ]; then
  echo "CHYBA: PostgreSQL kontajner nebeží" >&2
  exit 2
fi

BACKEND_NETWORK="$(
  docker inspect "$POSTGRES_CID" \
    --format \
    '{{range $name, $settings := .NetworkSettings.Networks}}{{println $name}}{{end}}' |
  grep '_backend$' |
  head -n 1
)"

if [ -z "$BACKEND_NETWORK" ]; then
  echo "CHYBA: backend Docker network nebol nájdený" >&2
  exit 2
fi

mkdir -p \
  "$IMPORT_ROOT/incoming" \
  "$IMPORT_ROOT/archive" \
  "$IMPORT_ROOT/reports" \
  "$IMPORT_ROOT/quarantine"

docker run --rm \
  --pull never \
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
    --service-account-file /run/secrets/google_drive_service_account.json \
    --folder-id-file /run/secrets/google_drive_folder_id \
    --import-root /imports \
    --db-password-file /run/secrets/postgres_app_password \
    --created-by google-drive-service-account \
    --max-attempts 3
