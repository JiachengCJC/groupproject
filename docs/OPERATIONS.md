# Operations and recovery

These instructions apply to the current single-server Compose deployment. Confirm the working directory, `.env`, and Compose project name before operating on an environment. PostgreSQL is authoritative for accounts, original files, tables, reviews, and jobs. Qdrant indexes can be rebuilt from source documents.

## Initial deployment and configuration

Run `python3 scripts/configure.py` to generate random database passwords and the setup token. Existing values are preserved. Restrict `.env` to the deployment administrator and back it up through a separate secure channel. Editing a database password in `.env` does not change an existing PostgreSQL role: rotate the database credential first, then update the application configuration.

After initial registration, add administrators, analysts, reviewers, and viewers in **Workspace**. Administrators manage accounts, analysts import and analyze, reviewers review reports, and viewers browse. Reviews record the authenticated user.

`MAX_MODEL_CALLS`, `MAX_AGENT_STEPS`, and `ANALYSIS_TIMEOUT` bound a model analysis. One worker processes tasks sequentially. Observe queue times and model memory before scaling with `docker compose up -d --scale worker=2`. Concurrent large Ollama models can exceed available memory.

## Local model profiles

Install the exact model tags on the host Ollama service before selecting a profile:

```bash
ollama pull qwen3.5:4b-q4_K_M
ollama pull qwen3.5:9b-q4_K_M
ollama pull bge-m3
ollama list
```

| Application setting | Role |
| --- | --- |
| `OLLAMA_FAST_MODEL=qwen3.5:4b-q4_K_M` | Local Fast model; Local Auto planner and simpler analysis |
| `OLLAMA_QUALITY_MODEL=qwen3.5:9b-q4_K_M` | Local Quality model; Local Auto analysis when its routing rule requires the larger model |
| `OLLAMA_MODEL=qwen2.5:7b` | Default selection for Custom local; independent of the named profiles |
| `OLLAMA_VISION_MODEL=qwen3.5:4b-q4_K_M` | Default local model for explicitly selected image understanding |
| `EMBEDDING_PROVIDER=ollama`, `EMBEDDING_MODEL=bge-m3` | Local semantic embeddings; select `RETRIEVAL_MODE=hybrid` to combine them with keyword retrieval |
| `OLLAMA_CONTEXT=8192` | Initial application context budget; reduce it if memory is tight and validate the resulting answers |
| `OLLAMA_THINKING=off` | Disable thinking when the installed model advertises that control; `on` and `default` are available for explicitly tested configurations |
| `OLLAMA_NUM_PREDICT=2400` | Generation budget per request, bounded from 256 to 8192; incomplete structured responses fail visibly |
| `OLLAMA_KEEP_ALIVE=0` | Request unloading after generation and embedding calls to reduce model residency on the 16 GB machine |

Edit the application `.env`, then run `docker compose up -d --wait` to apply changes to both API and worker. Setting the analysis model does not change the embedding model or rebuild an index. A changed embedding model or revision requires a separate index rebuild. Check **Knowledge library** for index state before evaluating retrieval quality.

In **Analysis workspace → Model and analysis settings**, choose **Ollama · Local model**, then a **Local run mode**. Fast uses the small model throughout; Quality uses the larger model throughout. Auto starts with the fast planner and records a reason for the model chosen for subsequent work. **Custom local** retains manual model selection and optional OpenAI Decisions. A named profile requires a local model recommendation and local or lexical retrieval; incompatible cloud settings are rejected rather than silently substituted.

Auto selects the quality model for multiple selected tables, a plan combining SQL and document retrieval, multiple specialist tasks, revision notes, or explicitly supplied candidate actions. It also escalates when a specialist requests another evidence round. Otherwise it keeps the fast model. These are inspectable workload rules, not a claim that task difficulty or answer correctness can be measured perfectly. Choose Quality directly when a business question needs closer analysis.

New submissions record the effective profile models, installed weight digests, and thinking/context/output controls. A queued run preserves that selection; if weights or generation controls change before it executes, the run fails with a clear message instead of silently using a different configuration. Submit a new analysis after reviewing an update. Revalidating a new revision or scheduled execution uses current server profile settings. Saved, approved report payloads and hashes are not rewritten. The result's `local_routing` and per-call usage identify actual routing and models, and approved HTML/PDF reports include the routing summary. Configured vision and embedding roles describe available assignments, not proof that every run invoked them.

The provider checks installed model capabilities before generation or image use. If a model cannot explicitly disable thinking, `OLLAMA_THINKING=off` raises a configuration error; use a compatible model or deliberately test `default`. Unloading after every call saves resident memory but adds model-loading overhead. Change these settings based on measured workloads rather than treating a model's advertised maximum context as a suitable application default.

Named profiles select local inference for the analysis. They do not disable separately configured online source synchronization. Set `LOCAL_ONLY=true` when the deployment must also block cloud vision, cloud embeddings, cloud decisions, and web/Kaggle synchronization. Existing downloaded sources remain usable.

Use one application worker on the 16 GB M4 deployment. Start with Local Fast; exercise Local Quality with a representative question while watching **Activity Monitor → Memory** and `ollama ps`. Swap growth, model-loading delays, and timeouts indicate that the chosen context or workload exceeds the available resources. A model's download size is not its total runtime memory requirement. Prefer smaller source scopes and retrieved passages before increasing time or context limits.

Ollama server controls are separate from the application `.env`. If using the Ollama macOS app and limiting the whole local model service to one loaded model and one request at a time, set these in the shell and restart Ollama:

```bash
launchctl setenv OLLAMA_MAX_LOADED_MODELS 1
launchctl setenv OLLAMA_NUM_PARALLEL 1
```

These controls also affect other applications using that Ollama service. Model switches can add loading time, especially when embeddings and generation alternate. See the [Ollama server and concurrency documentation](https://docs.ollama.com/faq). Use the [local acceptance guide](../scripts/LOCAL_ACCEPTANCE.md) after changing a model, runtime setting, or Ollama version. Retain an installed, previously verified model until the replacement passes the required cases; the app does not silently downgrade a selected profile after a provider failure.

## OpenAI Decisions configuration

The decision integration uses the same `OPENAI_API_KEY` as cloud analysis and a separate `OPENAI_DECISION_MODEL=gpt-6-luna`. `OPENAI_MODEL` continues to select the Responses model used for analysis and explanations. TypeSafe credentials are no longer required for current runs.

Apply changes to both API and worker containers:

```bash
docker compose up --build -d --wait
```

Choose **OpenAI Decisions** under **Analysis workspace → Model and analysis settings → Decision method**. A configured key does not guarantee that an account can access every model; provider permission errors are reported explicitly. A decision request sends shared analysis evidence and the candidate actions to OpenAI, including when specialists use Ollama. `LOCAL_ONLY=true` blocks this request. Use a local model recommendation for fully local analysis.

Do not apply Responses token prices to Decisions usage. Optional Decisions estimates use `OPENAI_DECISIONS_INPUT_USD_PER_MILLION` (default `-1`, unknown) and `OPENAI_DECISIONS_PRICE_VERSION` (default empty). Both an explicit nonnegative rate and a price version are required before the cost is known. The Decisions API does not charge output tokens under its documented contract. Reconcile estimates with the provider bill. Existing approved reports retain their original content, provider labels, and hashes; updating a provider does not retroactively rewrite evidence or approval history.

The API contract and supported model are documented in the [OpenAI Decisions guide](https://developers.openai.com/api/docs/guides/decisions). The [acceptance record](ACCEPTANCE.md) records the latest verified behavior.

## Database backup

```bash
python3 scripts/backup.py
```

The script uses a consistent PostgreSQL snapshot and writes a private `.dump` plus a SHA256 manifest. Passwords are not passed in command-line arguments. The backup includes original files, business tables, accounts, reviews, checkpoints, and evaluations. It excludes `.env`, Qdrant vectors, Ollama models, and MLflow's separate volume.

Copy backups to a separate disk or controlled remote location, and regularly restore them in an isolated environment. To retain complete MLflow history, separately back up the `mlflow-data` volume using a volume snapshot while MLflow is stopped, or SQLite's online backup facility. Database reports retain their evidence and tracking identifiers.

## Restore in isolation

Restoring clears the target database. Use a confirmed recovery target, not the only production copy.

1. Create a recovery environment with a different Compose project name and port, and a separate `.env`. Start PostgreSQL to provision the application and analytics roles.
2. Stop that environment's API and worker. Verify the dump against its SHA256 manifest.
3. Restore to the target PostgreSQL instance:

```bash
docker compose stop market-lens worker
docker compose exec -T postgres pg_restore -U postgres -d market_lens --clean --if-exists --exit-on-error < backups/your-backup.dump
docker compose up -d --wait
```

4. Sign in and verify source data, originals, approved reports, and evaluations. Unfinished jobs may resume after their leases expire; cancel any jobs that should not run again. External API calls are not guaranteed to execute exactly once.
5. Rebuild Qdrant indexes from **Knowledge library**, then verify citations and exports before directing users to the restored environment.

## Legacy SQLite migration

Preserve the old application and its volume, and rehearse using a consistent SQLite copy. Copying only an actively written `.sqlite3` file can omit WAL data. Use Python's `sqlite3.Connection.backup()` to create a consistent copy.

`python -m app.migrate --source /path/legacy.sqlite3` reports the scope without changing source or target. Set `DATABASE_URL` to a new target and add `--apply` to migrate. The tool rejects nonempty targets, checks row counts, and preserves approved-version relationships. Sessions are not migrated. A legacy installation without accounts requires initial administrator registration.

For Compose, place the copy in the current directory and set `SEED_DEMO=false` in `.env` to avoid inserting demo records into the empty target. Start PostgreSQL, run the one-off migration container, then start the API and worker:

```bash
docker compose up -d postgres
# Mount the consistent legacy.sqlite3 backup read-only.
docker compose run --rm --no-deps -v "$PWD/legacy.sqlite3:/tmp/legacy.sqlite3:ro" market-lens python -m app.migrate --source /tmp/legacy.sqlite3
# Review the reported scope before applying the migration.
docker compose run --rm --no-deps -v "$PWD/legacy.sqlite3:/tmp/legacy.sqlite3:ro" market-lens python -m app.migrate --source /tmp/legacy.sqlite3 --apply
docker compose up -d --wait
```

Keep the old installation available for rollback. Switching back to an old image does not migrate new PostgreSQL records back to SQLite.

## HTTPS cloud deployment

Configure DNS and open ports 80/443 on the server. Set `SITE_DOMAIN` in `.env` to a domain you control, then run:

```bash
docker compose -f compose.yaml -f compose.cloud.yaml up --build -d --wait
```

Caddy terminates TLS. The application uses Secure cookies and a hostname allowlist. PostgreSQL, Qdrant, and MLflow remain on the internal network. Public certificate acceptance requires an actual domain and server; local testing does not establish that a public deployment exists. The operator manages cloud data, backups, machines, and model services.

## Troubleshooting

| Symptom | Checks |
| --- | --- |
| Initial deployment fails | Inspect `docker compose logs postgres` and generated passwords. Preserve existing volumes during diagnosis. |
| Local model is unreachable | Check host Ollama and `OLLAMA_BASE_URL`; containers normally use `host.docker.internal`. |
| A local profile is unavailable | Compare its exact model tags with `ollama list`, refresh the local model list, and recreate containers after changing profile settings. Auto requires both its fast and quality models to be installed. |
| A local profile rejects the decision or retrieval configuration | Use Model recommendation and local Ollama or lexical retrieval. Choose Custom local deliberately if a cloud decision is intended. |
| A queued local run reports changed weights or generation settings | Review the model or configuration update and submit a new analysis. The saved model digest and generation controls intentionally prevent silent substitution. |
| A model rejects the thinking configuration | Inspect its capabilities; use a model that supports the selected control or deliberately test `OLLAMA_THINKING=default`. |
| Model times out or runs out of memory | Choose a model that fits, reduce concurrency, and inspect context and task budgets. |
| Task remains queued | Check worker health, `docker compose logs worker`, and task status. |
| Recovery after a worker restart is slow | Allow the previous lease to expire; avoid duplicate manual submissions. |
| Semantic search is unavailable | Check the downloaded embedding model, Qdrant, and index rebuild status. Retrieval failures are reported explicitly. |
| Cloud analysis is unavailable | Check `OPENAI_API_KEY`, `OPENAI_MODEL`, and `LOCAL_ONLY`; recreate containers after configuration changes. |
| OpenAI Decisions is unavailable or fails | Check `OPENAI_API_KEY`, `OPENAI_DECISION_MODEL`, `LOCAL_ONLY`, and account access to the Decisions model. Inspect the reported provider error. |
| Report cannot be exported | Confirm approval, a matching result hash, and the current member's access. |
| MLflow tracking fails | Check the internal `mlflow` service. The report remains saved; failed tracking is reported explicitly. |

In `LOCAL_ONLY=true` mode, Ollama and Qdrant requests do not inherit external proxies. MLflow export explicitly rejects a configured HTTP(S) proxy in that mode; clear the proxy configuration to restore local tracking.

Tracking excludes questions and source content by default. Keep `.env`, database backups, and full source material out of public issues. A bug report can include the version, task ID, status, and a redacted error type.
