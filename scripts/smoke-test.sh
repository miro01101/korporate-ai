#!/usr/bin/env bash
set -Eeuo pipefail

cd /opt/korporate-ai

echo "API live:"
python3 - <<'PY'
import json
import urllib.request

with urllib.request.urlopen(
    "http://127.0.0.1:18000/health/live",
    timeout=5,
) as response:
    print(json.dumps(json.load(response), indent=2))
PY

echo
echo "API ready:"
python3 - <<'PY'
import json
import urllib.request

with urllib.request.urlopen(
    "http://127.0.0.1:18000/health/ready",
    timeout=5,
) as response:
    print(json.dumps(json.load(response), indent=2))
PY

echo
echo "Streamlit health:"
python3 - <<'PY'
import urllib.request

with urllib.request.urlopen(
    "http://127.0.0.1:18501/_stcore/health",
    timeout=5,
) as response:
    print(response.read().decode("utf-8"))
PY

echo
echo "Databazove schemy:"
docker compose exec -T postgres \
    psql \
    --username korporate_admin \
    --dbname korporate_ai \
    --command '\dn'

echo
echo "Platform metadata:"
docker compose exec -T postgres \
    psql \
    --username korporate_admin \
    --dbname korporate_ai \
    --command \
    'SELECT key, value, updated_at
       FROM meta.system_info
      ORDER BY key;'
