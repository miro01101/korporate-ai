#!/usr/bin/env bash
set -Eeuo pipefail

cd /opt/korporate-ai || exit 1

if [ "$#" -lt 1 ]; then
    echo "Usage: scripts/run-ml-pipeline.sh COMMAND [ARGS...]"
    echo
    echo "Commands:"
    echo "  self-check"
    echo "  validate"
    echo "  build-features"
    exit 2
fi

exec docker compose \
    --profile tools \
    run \
    --rm \
    ml \
    "$@"
