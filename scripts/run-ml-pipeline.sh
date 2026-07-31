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
  echo "  train-demand"
  echo "  train-lightgbm"
  echo "  select-hybrid"
  echo "  calibrate-intervals"
  echo "  build-inventory-risk"
  echo "  build-recommendations"
  exit 2
fi

exec 8>/run/lock/korporate-ai-ml.lock

if ! flock -n 8; then
  echo "ML_PIPELINE_SKIPPED=ml_lock_not_acquired"
  exit 75
fi

exec docker compose \
  --profile tools \
  run \
  --rm \
  --no-deps \
  -e "GIT_COMMIT=$(git rev-parse HEAD)" \
  ml \
  "$@"
