#!/bin/sh
set -eu

APP_PASSWORD="$(cat /run/secrets/postgres_app_password_init)"

psql \
    --set=ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --set=app_password="$APP_PASSWORD" <<'SQL'

CREATE ROLE korporate_app
    LOGIN
    PASSWORD :'app_password'
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOREPLICATION;

GRANT CONNECT ON DATABASE korporate_ai TO korporate_app;

SQL
