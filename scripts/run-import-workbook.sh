#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Pouzitie:
  scripts/run-import-workbook.sh /srv/korporate-ai/imports/manual/incoming/subor.xlsx [volby]

Volby odovzdane orchestratoru:
  --move-source
  --created-by MENO

Workbook musi byt ulozeny pod:
  /srv/korporate-ai/imports/manual
EOF
}

if [[ $# -lt 1 ]]; then
    usage
    exit 2
fi

REPOSITORY_ROOT="$(
    cd "$(dirname "${BASH_SOURCE[0]}")/.."
    pwd
)"
MANUAL_IMPORT_ROOT="/srv/korporate-ai/imports/manual"
WORKBOOK_HOST="$(realpath -e "$1")"
shift

case "$WORKBOOK_HOST" in
    "$MANUAL_IMPORT_ROOT"/*)
        ;;
    *)
        echo "CHYBA: workbook musi byt pod $MANUAL_IMPORT_ROOT" >&2
        exit 2
        ;;
esac

WORKBOOK_RELATIVE="${WORKBOOK_HOST#"$MANUAL_IMPORT_ROOT"/}"
WORKBOOK_CONTAINER="/imports/$WORKBOOK_RELATIVE"

PASSWORD_FILE="$REPOSITORY_ROOT/secrets/postgres_app_password"
ORCHESTRATOR="$REPOSITORY_ROOT/scripts/import-workbook.py"

test -f "$PASSWORD_FILE" || {
    echo "CHYBA: chyba DB secret $PASSWORD_FILE" >&2
    exit 2
}

test -f "$ORCHESTRATOR" || {
    echo "CHYBA: chyba orchestrator $ORCHESTRATOR" >&2
    exit 2
}

BACKEND_NETWORK="$(
    docker inspect korporate-ai-postgres-1 \
        --format \
        '{{range $name, $settings := .NetworkSettings.Networks}}{{println $name}}{{end}}' \
    | grep '_backend$' \
    | head -n 1
)"

test -n "$BACKEND_NETWORK" || {
    echo "CHYBA: backend Docker network nebol najdeny" >&2
    exit 2
}

DEPS_VOLUME="korporate-ai-import-deps-$(date +%s)-$$"

cleanup() {
    docker volume rm "$DEPS_VOLUME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker volume create "$DEPS_VOLUME" >/dev/null

docker run --rm \
    -v "$DEPS_VOLUME":/deps \
    -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
    python:3.12-slim \
    python -m pip install \
        --no-cache-dir \
        --root-user-action=ignore \
        --target /deps \
        openpyxl==3.1.5 \
        "psycopg[binary]>=3.2,<4"

docker run --rm \
    --network "$BACKEND_NETWORK" \
    --memory 1024m \
    --cpus 1.0 \
    --pids-limit 180 \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    -e PYTHONPATH=/deps \
    -v "$DEPS_VOLUME":/deps:ro \
    -v "$REPOSITORY_ROOT":/workspace:ro \
    -v "$MANUAL_IMPORT_ROOT":/imports \
    -v "$PASSWORD_FILE":/run/secrets/postgres_app_password:ro \
    -w /workspace \
    python:3.12-slim \
    python scripts/import-workbook.py \
        "$WORKBOOK_CONTAINER" \
        --source-path "$WORKBOOK_HOST" \
        --archive-root /imports/archive \
        --report-root /imports/reports \
        --db-password-file /run/secrets/postgres_app_password \
        "$@"
