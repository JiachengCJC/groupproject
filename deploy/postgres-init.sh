#!/bin/sh
set -eu
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 --set=app_password="$DATABASE_PASSWORD" --set=analytics_password="$ANALYTICS_PASSWORD" <<'SQL'
CREATE ROLE market_lens LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE market_lens_analytics LOGIN PASSWORD :'analytics_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO market_lens;
GRANT USAGE ON SCHEMA public TO market_lens_analytics;
ALTER ROLE market_lens_analytics SET default_transaction_read_only = on;
ALTER ROLE market_lens_analytics SET statement_timeout = '2s';
SQL
