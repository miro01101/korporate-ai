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

Importer image:
  korporate-ai-importer:0.2.0
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
IMPORTER_IMAGE="${KORPORATE_IMPORTER_IMAGE:-korporate-ai-importer:0.2.0}"
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

test -f "$PASSWORD_FILE" || {
    echo "CHYBA: chyba DB secret $PASSWORD_FILE" >&2
    exit 2
}

docker image inspect "$IMPORTER_IMAGE" >/dev/null 2>&1 || {
    echo "CHYBA: chyba Docker image $IMPORTER_IMAGE" >&2
    echo "Spusti: docker compose --profile tools build importer" >&2
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

docker run --rm \
    --pull never \
    --network "$BACKEND_NETWORK" \
    --memory 1024m \
    --cpus 1.0 \
    --pids-limit 180 \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    -v "$MANUAL_IMPORT_ROOT":/imports \
    -v "$PASSWORD_FILE":/run/secrets/postgres_app_password:ro \
    "$IMPORTER_IMAGE" \
        "$WORKBOOK_CONTAINER" \
        --source-path "$WORKBOOK_HOST" \
        --archive-root /imports/archive \
        --report-root /imports/reports \
        --db-password-file /run/secrets/postgres_app_password \
        "$@"
