FROM postgres:17-alpine
COPY --chmod=0644 postgres-init.sh /docker-entrypoint-initdb.d/10-market-lens.sh
