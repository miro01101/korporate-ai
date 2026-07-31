#!/usr/bin/env bash
set -Eeuo pipefail

export LC_ALL=C
export LANG=C
export COMPOSE_MENU=false

REPOSITORY_ROOT="$(
  cd "$(dirname "${BASH_SOURCE[0]}")/.."
  pwd
)"
cd "$REPOSITORY_ROOT"

ML_IMAGE="${KORPORATE_ML_IMAGE:-korporate-ai-ml:0.5.0}"
ML_LOCK_FILE="/run/lock/korporate-ai-ml.lock"
SUMMARY_FILE=""
PIPELINE_RUN_ID=""
PREFLIGHT_ONLY=0

usage() {
  cat <<'EOF'
Usage:
  scripts/run-ml-after-import.sh \
    --pipeline-run-id UUID \
    --summary-file /path/summary.json

  scripts/run-ml-after-import.sh --preflight-only
EOF
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --pipeline-run-id)
      PIPELINE_RUN_ID="${2:-}"
      shift 2
      ;;
    --summary-file)
      SUMMARY_FILE="${2:-}"
      shift 2
      ;;
    --preflight-only)
      PREFLIGHT_ONLY=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "CHYBA: neznámy argument $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$PREFLIGHT_ONLY" -eq 0 ]]; then
  [[ "$PIPELINE_RUN_ID" =~ ^[0-9a-fA-F-]{36}$ ]] || {
    echo "CHYBA: neplatný pipeline run ID" >&2
    exit 2
  }
  [[ -n "$SUMMARY_FILE" ]] || {
    echo "CHYBA: --summary-file je povinný" >&2
    exit 2
  }
fi

if ! docker image inspect "$ML_IMAGE" >/dev/null 2>&1; then
  echo "CHYBA: chýba ML image $ML_IMAGE" >&2
  exit 2
fi

POSTGRES_CID="$(docker compose ps -q postgres)"
if [[ -z "$POSTGRES_CID" ]]; then
  echo "CHYBA: PostgreSQL kontajner nebeží" >&2
  exit 2
fi

POSTGRES_HEALTH="$(
  docker inspect \
    --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
    "$POSTGRES_CID"
)"
[[ "$POSTGRES_HEALTH" == "healthy" ]] || {
  echo "CHYBA: PostgreSQL nie je healthy" >&2
  exit 2
}

exec 8>"$ML_LOCK_FILE"
if ! flock -n 8; then
  echo "ML_PIPELINE_SKIPPED=ml_lock_not_acquired"
  exit 75
fi

GIT_COMMIT="$(git rev-parse HEAD)"
COMPOSE=(
  docker compose
  --profile tools
)

run_ml_command() {
  local command="$1"
  docker compose \
    --profile tools \
    run \
    --rm \
    --no-deps \
    -e "GIT_COMMIT=$GIT_COMMIT" \
    ml \
    "$command"
}

if [[ "$PREFLIGHT_ONLY" -eq 1 ]]; then
  run_ml_command self-check
  python3 scripts/smoke-test-ml-api.py
  python3 scripts/smoke-test-ml-dashboard.py
  echo "ML_ORCHESTRATION_PREFLIGHT=PASS"
  exit 0
fi

STARTED_AT="$(date -u -Is)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

STAGES=(
  validate
  build-features
  train-demand
  train-lightgbm
  select-hybrid
  calibrate-intervals
  build-inventory-risk
  build-recommendations
)

write_summary() {
  local status="$1"
  local failed_stage="${2:-}"
  local exit_code="${3:-0}"
  local error_message="${4:-}"

  python3 - \
    "$SUMMARY_FILE" \
    "$WORK_DIR" \
    "$status" \
    "$PIPELINE_RUN_ID" \
    "$GIT_COMMIT" \
    "$STARTED_AT" \
    "$(date -u -Is)" \
    "$failed_stage" \
    "$exit_code" \
    "$error_message" <<'PY'
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from typing import Any


(
    summary_path,
    work_dir,
    status,
    pipeline_run_id,
    git_commit,
    started_at,
    finished_at,
    failed_stage,
    exit_code,
    error_message,
) = sys.argv[1:]

stage_order = [
    "validate",
    "build-features",
    "train-demand",
    "train-lightgbm",
    "select-hybrid",
    "calibrate-intervals",
    "build-inventory-risk",
    "build-recommendations",
]

pair_pattern = re.compile(r"^([A-Za-z0-9_]+)=(.*)$")


def parse_log(path: Path) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if not path.is_file():
        return values

    text = path.read_text(
        encoding="utf-8",
        errors="replace",
    )

    for line in text.splitlines():
        match = pair_pattern.match(line.strip())
        if match:
            values[match.group(1)] = match.group(2)

    values["output_tail"] = text[-4000:]
    return values


stages: dict[str, Any] = {}
root = Path(work_dir)

for stage in stage_order:
    stages[stage] = parse_log(root / f"{stage}.log")

summary: dict[str, Any] = {
    "status": status,
    "pipeline_run_id": pipeline_run_id,
    "git_commit": git_commit,
    "started_at": started_at,
    "finished_at": finished_at,
    "failed_stage": failed_stage or None,
    "exit_code": int(exit_code),
    "error_message": error_message or None,
    "automatic_ordering": False,
    "human_approval_required": True,
    "stage_order": stage_order,
    "stages": stages,
}

mapping = {
    "feature_run_id": ("build-features", "feature_run_id"),
    "product_count": ("build-features", "feature_product_count"),
    "feature_row_count": ("build-features", "feature_row_count"),
    "dataset_fingerprint": ("build-features", "dataset_fingerprint"),
    "baseline_model_run_id": ("train-demand", "model_run_id"),
    "challenger_model_run_id": ("train-lightgbm", "model_run_id"),
    "hybrid_model_run_id": ("select-hybrid", "model_run_id"),
    "calibrated_model_run_id": ("calibrate-intervals", "model_run_id"),
    "inventory_risk_ready": (
        "calibrate-intervals",
        "inventory_risk_ready",
    ),
    "inventory_risk_model_run_id": (
        "build-inventory-risk",
        "model_run_id",
    ),
    "inventory_risk_rows": (
        "build-inventory-risk",
        "inventory_risk_row_count",
    ),
    "recommendation_model_run_id": (
        "build-recommendations",
        "model_run_id",
    ),
    "recommendation_rows": (
        "build-recommendations",
        "recommendation_row_count",
    ),
    "pending_recommendations": (
        "build-recommendations",
        "pending_count",
    ),
    "recommended_quantity": (
        "build-recommendations",
        "recommended_quantity",
    ),
}

for output_key, (stage, source_key) in mapping.items():
    value = stages.get(stage, {}).get(source_key)
    if value is not None:
        summary[output_key] = value

Path(summary_path).write_text(
    json.dumps(
        summary,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
PY
}

for stage in "${STAGES[@]}"; do
  echo
  echo "========================================================================"
  echo "ML_ORCHESTRATION_STAGE=$stage"
  echo "========================================================================"

  set +e
  run_ml_command "$stage" 2>&1 | tee "$WORK_DIR/$stage.log"
  stage_exit="${PIPESTATUS[0]}"
  set -e

  if [[ "$stage_exit" -ne 0 ]]; then
    write_summary \
      failed \
      "$stage" \
      "$stage_exit" \
      "ML stage $stage vrátil exit code $stage_exit"

    echo "ML_ORCHESTRATION_FAILED_STAGE=$stage" >&2
    exit "$stage_exit"
  fi
done

write_summary completed "" 0 ""

set +e
python3 - "$SUMMARY_FILE" <<'PY'
from __future__ import annotations

import json
from pathlib import Path
import sys


path = Path(sys.argv[1])
summary = json.loads(path.read_text(encoding="utf-8"))

required = (
    "feature_run_id",
    "product_count",
    "baseline_model_run_id",
    "challenger_model_run_id",
    "hybrid_model_run_id",
    "calibrated_model_run_id",
    "inventory_risk_model_run_id",
    "inventory_risk_rows",
    "recommendation_model_run_id",
    "recommendation_rows",
    "pending_recommendations",
)

missing = [key for key in required if not summary.get(key)]
if missing:
    raise RuntimeError(
        "ML summary chýbajú polia: " + ", ".join(missing)
    )

product_count = int(summary["product_count"])
risk_rows = int(summary["inventory_risk_rows"])
recommendation_rows = int(summary["recommendation_rows"])
pending = int(summary["pending_recommendations"])

if summary.get("inventory_risk_ready") != "ANO":
    raise RuntimeError("Kalibrovaný run nie je inventory-risk ready.")

calibrated = summary["calibrated_model_run_id"]
if summary["inventory_risk_model_run_id"] != calibrated:
    raise RuntimeError("Inventory risk nepoužil kalibrovaný run.")
if summary["recommendation_model_run_id"] != calibrated:
    raise RuntimeError("Recommendations nepoužili kalibrovaný run.")

if risk_rows != product_count:
    raise RuntimeError("Počet risk rows nezodpovedá počtu produktov.")
if recommendation_rows != product_count:
    raise RuntimeError(
        "Počet recommendations nezodpovedá počtu produktov."
    )
if pending != product_count:
    raise RuntimeError(
        "Nie všetky nové recommendations sú pending."
    )

print("ML_SUMMARY_INTEGRITY=PASS")
PY
SUMMARY_VALIDATION_EXIT=$?
set -e

if [[ "$SUMMARY_VALIDATION_EXIT" -ne 0 ]]; then
  write_summary \
    failed \
    summary-validation \
    "$SUMMARY_VALIDATION_EXIT" \
    "ML summary integrity validation failed"

  exit "$SUMMARY_VALIDATION_EXIT"
fi

set +e
python3 scripts/smoke-test-ml-api.py 2>&1 | tee "$WORK_DIR/ml-api-smoke.log"
SMOKE_API_EXIT="${PIPESTATUS[0]}"
python3 scripts/smoke-test-ml-dashboard.py 2>&1 | tee "$WORK_DIR/ml-dashboard-smoke.log"
SMOKE_DASHBOARD_EXIT="${PIPESTATUS[0]}"
set -e

if [[ "$SMOKE_API_EXIT" -ne 0 || "$SMOKE_DASHBOARD_EXIT" -ne 0 ]]; then
  write_summary \
    failed \
    api-smoke \
    1 \
    "Post-ML API alebo dashboard smoke zlyhal"
  exit 1
fi

write_summary completed "" 0 ""
echo "ML_ORCHESTRATION=PASS"
