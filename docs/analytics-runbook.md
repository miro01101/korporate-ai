# Analytics Mart and Dashboard Runbook

## Components

The v0.3.0 analytical layer consists of:

- monthly PostgreSQL mart tables,
- a transactional mart refresh,
- a reconciliation validator,
- read-only FastAPI endpoints,
- a Streamlit management dashboard.

## Refresh

Run:

```bash
docker compose --profile tools run --rm analytics-refresh
```

Expected ending:

```text
MART_REFRESH_STATUS=completed
MART_REFRESH_OK=ANO
```

The refresh uses a PostgreSQL advisory lock and replaces all dashboard-facing mart data in one transaction.

## Validation

Run:

```bash
docker compose --profile tools run --rm analytics-validate
```

Expected ending:

```text
MART_VALIDATION_ERROR_COUNT=0
MART_VALID=ANO
```

## API endpoints

```text
GET /api/v1/analytics/status
GET /api/v1/analytics/summary
GET /api/v1/analytics/monthly
GET /api/v1/analytics/sales/products
GET /api/v1/analytics/inventory
GET /api/v1/analytics/procurement/suppliers
GET /api/v1/analytics/expeditions
GET /api/v1/analytics/vehicles
```

Date parameters use the first day of a month in ISO format:

```text
2025-12-01
```

## API smoke test

Run from the server after API deployment:

```bash
python3 scripts/smoke-test-analytics-api.py
```

Expected ending:

```text
ANALYTICS_API_SMOKE_OK=ANO
```

## Dashboard

The dashboard is available on the existing loopback endpoint:

```text
http://127.0.0.1:18501
```

It reads data only through FastAPI. It does not connect directly to PostgreSQL.

## Data caveats

- Gross margin is estimated from the most recent purchase price known at the sale date.
- If no historical purchase exists, the product master purchase price is used.
- Days of cover is available only for product-month combinations with sales in the same month.
- Transport cost is not calculated because the source data does not contain route distance or kilometres.
- Vehicle utilization currently represents only expeditions marked as own delivery.
