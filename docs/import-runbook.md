# XLSX Import Runbook

## Purpose

This runbook describes the production procedure for manually importing an XLSX workbook into the Korporate AI Logistics Platform.

## Prerequisites

Run commands from:

```text
/opt/korporate-ai
```

Required conditions:

- PostgreSQL, API, and dashboard containers are healthy.
- Database migration is at `0006_platform_version`.
- Docker image `korporate-ai-importer:0.3.0` exists.
- The workbook is located under `/srv/korporate-ai/imports/manual`.
- The application database secret exists at `secrets/postgres_app_password`.

Verify:

```bash
docker compose ps
docker compose --profile tools run --rm migration alembic current
docker image inspect korporate-ai-importer:0.3.0 >/dev/null
```

## Directory layout

```text
/srv/korporate-ai/imports/manual/
├── incoming/
├── archive/YYYY/MM/
└── reports/
```

`incoming` contains files waiting for processing. A completed workbook is archived using:

```text
SHA256__original_filename.xlsx
```

The JSON report uses:

```text
IMPORT_BATCH_UUID.json
```

## Standard import

Copy or upload the workbook to `incoming`, then run:

```bash
scripts/run-import-workbook.sh   /srv/korporate-ai/imports/manual/incoming/workbook.xlsx   --created-by operator-name   --move-source
```

Successful completion ends with:

```text
IMPORT_BATCH_STATUS=completed
IMPORT_PIPELINE_OK=ANO
```

With `--move-source`, the source is removed from `incoming` only after the archive copy has matching size and SHA-256.

## Pipeline phases

1. `validate-workbook-structure.py`
2. `validate-workbook-values.py`
3. `validate-workbook-business.py`
4. registration and load to `raw.xlsx_*`
5. transformation to typed `stg.*`
6. database business validation
7. promotion to normalized `core.*`
8. JSON report generation
9. verified archival

The orchestrator stops immediately when a phase returns a non-zero exit code.

## Import batch statuses

- `registered`: batch metadata exists.
- `validating`: raw loading is being prepared.
- `raw_loaded`: raw JSONB rows are committed.
- `staging_loaded`: typed staging rows are committed.
- `completed`: core promotion completed successfully.
- `rejected`: database business validation found errors.
- `failed`: a technical error interrupted processing.

The pipeline is restartable for `raw_loaded`, `staging_loaded`, and `completed` batches.

## Idempotency

Workbook identity is the lowercase SHA-256 of the complete file.

A repeated file:

- re-runs file validations,
- reuses the existing audit batch,
- does not duplicate raw rows,
- does not duplicate staging rows,
- does not duplicate core rows,
- refreshes the report,
- verifies the existing archive.

## Operational checks

Latest batches:

```bash
docker compose exec -T postgres psql   -U korporate_admin   -d korporate_ai   -c "
SELECT
    id,
    status,
    original_filename,
    row_count_raw,
    row_count_core,
    error_count,
    warning_count,
    started_at,
    finished_at
FROM audit.import_batches
ORDER BY started_at DESC;
"
```

Validation issues:

```bash
docker compose exec -T postgres psql   -U korporate_admin   -d korporate_ai   -c "
SELECT
    severity,
    rule_code,
    sheet_name,
    source_row_number,
    business_key,
    message
FROM audit.import_issues
ORDER BY id;
"
```

## Failure handling

### File validation failure

No batch is created. Correct the workbook and run it again.

### `rejected`

Do not edit raw or staging rows manually. Review `audit.import_issues`, correct the source workbook, and import the corrected file as a new SHA-256.

### `failed`

Read the batch `error_message` and container output. Do not manually change the batch status until the technical cause is understood.

Relevant query:

```bash
docker compose exec -T postgres psql   -U korporate_admin   -d korporate_ai   -c "
SELECT id, status, error_message, metadata
FROM audit.import_batches
ORDER BY started_at DESC;
"
```

### Importer image missing

Build it:

```bash
docker compose --profile tools build importer
```

### Database migration behind

Run:

```bash
docker compose --profile tools build migration
docker compose --profile tools run --rm migration alembic upgrade head
```

## Release smoke test

Use a verified copy of an already completed workbook. The expected result is one existing batch and no new rows.

Verify:

```bash
docker compose exec -T postgres psql   -U korporate_admin   -d korporate_ai   -c "
SELECT
    count(*) AS batch_count,
    sum(row_count_raw) AS raw_rows,
    sum(row_count_core) AS core_rows
FROM audit.import_batches;

SELECT count(*) AS issue_count
FROM audit.import_issues;
"
```

For the initial reference workbook, expected totals are:

```text
batch_count = 1
raw_rows = 18494
core_rows = 22044
issue_count = 0
```

## Prohibited actions

Do not:

- commit XLSX files or generated JSON reports,
- commit anything from `secrets/`,
- delete a completed batch to re-import the same file,
- edit raw, staging, or core rows to conceal validation failures,
- run broad Docker prune commands on the shared server,
- expose PostgreSQL on a host port.
