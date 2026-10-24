# Database migrations

`Database.initialize()` applies additive schema migrations under a PostgreSQL
advisory transaction lock or a SQLite immediate transaction. Existing imported
data, original bytes, completed analyses and reviews are preserved. Schema
version 2 adds workspaces, authenticated users/sessions, source/report hashes,
audit events and durable tasks. Legacy reviewer labels remain self-reported;
they are not retroactively represented as authenticated identities.

For an existing SQLite deployment, stop new writes and make an independent
backup before switching the production endpoint. A rehearsal is supported with
`python -m app.migrate --source /path/to/old.sqlite3`. This command only reports
counts. Configure the target through `DATABASE_URL`; use `--apply` to copy data
into an empty target. The source is copied using SQLite's backup API and never
modified. Existing target business records cause an error instead of replacement.
The copy is transactional; original-file bytes, table counts and report hashes
are checked before commit. Sessions are intentionally not copied, so users sign
in again after migration. Rebuild semantic indexes from the migrated documents.

Use `ANALYTICS_DATABASE_URL` for the separately provisioned PostgreSQL read-only
login and `ANALYTICS_ROLE=market_lens_analytics`. Ingestion grants this role SELECT
on each imported data table only; application metadata tables are not granted.
Runtime analytical queries also pass table/function/operation checks and run in
a read-only transaction with a two-second statement timeout.

A downgrade is a restore operation: stop API and workers, restore the matching
database/files backup and prior application image together, then validate counts
and approved exports before reopening writes. Do not run an older application
against the migrated database as an assumed rollback strategy.

The persistent task table is the transactional queue: analysis and job rows are
committed together. Workers claim jobs with a lease, renew it, and fence stale
completion. PostgreSQL uses `FOR UPDATE SKIP LOCKED`; SQLite serializes claims.
A crash can cause a read-only model call to be repeated after lease expiry; this
system does not promise exactly-once external API calls. Approvals and final
report versions cannot be overwritten by a late worker.

## Decision-provider compatibility

The current decision provider is OpenAI Decisions. New analysis, evaluation,
schedule, and rerun requests normalize the legacy `decision_mode="jev"` value
to `openai_decisions`. This does not rewrite completed results or their provider
attribution, and it does not invalidate or replace existing approval hashes.
Use `OPENAI_API_KEY` and `OPENAI_DECISION_MODEL=gpt-6-luna` for new decision
requests; no TypeSafe credential is required. Local-only mode continues to
reject cloud decisions.
