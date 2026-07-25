# Korporate AI Logistics Platform

## Version

v0.1.0 – Platform Skeleton

## Services

- PostgreSQL 18
- FastAPI
- Streamlit
- Alembic migrations

## Local server endpoints

Services are currently bound only to the server loopback interface.

- API: http://127.0.0.1:18000
- API readiness: http://127.0.0.1:18000/health/ready
- Dashboard: http://127.0.0.1:18501
- Streamlit health: http://127.0.0.1:18501/_stcore/health

## Common commands

Run commands from /opt/korporate-ai:

- docker compose config --quiet
- docker compose ps
- docker compose logs --tail 100
- docker compose run --rm migration
- scripts/smoke-test.sh

## Security

Never commit files from the secrets directory.

PostgreSQL must not be published on a host port. API and dashboard remain bound to loopback until reverse proxy integration is configured.

## Next milestone

Implement the first XLSX data pipeline:

XLSX -> raw import -> staging validation -> core tables -> Data Quality Report
