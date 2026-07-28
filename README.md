# Korporate AI Logistics Platform

## Version

v0.3.1 – Dashboard UI Polish

## Implemented scope

```text
XLSX
→ validation
→ audit
→ raw
→ staging
→ normalized core
→ analytical marts
→ FastAPI analytics
→ Streamlit management dashboard
```

## Services

- PostgreSQL 18
- FastAPI
- Streamlit
- Alembic migrations
- XLSX importer and mart refresh tools

## Endpoints

- API: http://127.0.0.1:18000
- API readiness: http://127.0.0.1:18000/health/ready
- Analytics status: http://127.0.0.1:18000/api/v1/analytics/status
- OpenAPI: http://127.0.0.1:18000/docs
- Dashboard: http://127.0.0.1:18501

All endpoints remain bound to the server loopback interface.

## Manual XLSX import

```bash
scripts/run-import-workbook.sh \
  /srv/korporate-ai/imports/manual/incoming/workbook.xlsx \
  --created-by manual-user \
  --move-source
```

## Refresh analytical marts

```bash
docker compose --profile tools run --rm analytics-refresh
docker compose --profile tools run --rm analytics-validate
```

## Smoke tests

```bash
scripts/smoke-test.sh
python3 scripts/smoke-test-analytics-api.py
```

## Analytics outputs

- monthly sales and estimated gross margin,
- product and category performance,
- inventory health and days of cover,
- supplier fill rate and lead-time performance,
- expedition process metrics,
- own-fleet capacity utilization,
- consolidated monthly management scorecard.

See:

- `docs/import-runbook.md`
- `docs/analytics-runbook.md`

## Security

Never commit secrets, XLSX files, generated JSON reports, database dumps, or backups.

PostgreSQL is not exposed on a host port. The dashboard reads analytical data through FastAPI and does not connect directly to the database.

## Current database revision

```text
0008_platform_version
```

## Next milestone

Add authentication, tenant/company isolation, scheduled imports and refreshes, and agency-ready downloadable reports.
