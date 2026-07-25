# Korporate AI Logistics Platform

## Version

v0.2.0 – XLSX Import MVP

## Implemented scope

The platform now supports the complete manual XLSX import lifecycle:

```text
XLSX
-> structural validation
-> value validation
-> business validation
-> audit batch
-> raw JSONB layer
-> typed staging layer
-> normalized core model
-> JSON import report
-> verified archive
```

The import is idempotent by SHA-256. Reprocessing the same workbook does not duplicate raw, staging, or core data.

## Services

- PostgreSQL 18
- FastAPI
- Streamlit
- Alembic migrations
- Dedicated XLSX importer image

## Local server endpoints

Services remain bound only to the server loopback interface.

- API: http://127.0.0.1:18000
- API readiness: http://127.0.0.1:18000/health/ready
- Dashboard: http://127.0.0.1:18501
- Streamlit health: http://127.0.0.1:18501/_stcore/health

## Manual XLSX import

Place the workbook in:

```text
/srv/korporate-ai/imports/manual/incoming
```

Run:

```bash
scripts/run-import-workbook.sh   /srv/korporate-ai/imports/manual/incoming/workbook.xlsx   --created-by manual-user   --move-source
```

The importer writes verified artifacts to:

```text
/srv/korporate-ai/imports/manual/archive/YYYY/MM
/srv/korporate-ai/imports/manual/reports
```

See `docs/import-runbook.md` for operations and recovery procedures.

## Common commands

Run commands from `/opt/korporate-ai`:

```bash
docker compose config --quiet
docker compose ps
docker compose logs --tail 100
docker compose --profile tools build importer
docker compose --profile tools run --rm migration
scripts/smoke-test.sh
```

## Security

Never commit:

- files from `secrets/`,
- XLSX source files,
- generated JSON reports,
- database dumps.

PostgreSQL must not be published on a host port. API and dashboard remain loopback-only until reverse proxy integration is configured.

The importer container uses dropped Linux capabilities, `no-new-privileges`, bounded memory/CPU/PIDs, a read-only database secret mount, and write access only to the manual import directory.

## Database state

The XLSX pipeline uses:

- `audit.import_batches`
- `audit.import_issues`
- `raw.xlsx_*`
- `stg.*`
- normalized `core.*` logistics tables

Current migration head for v0.2.0:

```text
0006_platform_version
```

## Next milestone

Build analytical marts and dashboard outputs for:

- sales and gross margin,
- inventory health and turnover,
- procurement performance,
- expedition and vehicle utilization,
- management KPI reporting.
