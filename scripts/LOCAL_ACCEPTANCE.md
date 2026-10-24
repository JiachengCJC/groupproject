# Local model acceptance

## Fast, Quality, Auto, and image understanding

The profile acceptance runner uses real installed Qwen3.5 models, a temporary SQLite database, and synthetic inventory and policy sources. It enables `local_only`, keeps test records out of the application workspace, and checks actual model routing as well as results. Profile cases use lexical retrieval to isolate generation and routing behavior; they do not establish BGE-M3 or Qdrant correctness. The separate semantic-retrieval checks below cover that path.

Install the configured fast, quality, and vision models, then run from the project root with the project's Python dependencies installed:

```bash
python -m scripts.validate_profiles --profile all --case all \
  --ollama-url http://127.0.0.1:11434 \
  --output output/local-profiles-acceptance.json
```

Use `--profile fast`, `quality`, or `auto` to limit the profile, and `--case sql`, `rag`, `hybrid`, or `vision` to limit the case. Image understanding uses the configured vision model and runs once when `--case all` or `--case vision` is selected, rather than once per text profile. For example, start with one fast SQL case before running the larger model:

```bash
python -m scripts.validate_profiles --profile fast --case sql \
  --ollama-url http://127.0.0.1:11434 \
  --output output/local-fast-sql.json
```

Inspect the output for the selected profile, actual model usage, Auto routing reason, supported facts, and evidence references. A completed request or valid JSON response alone is not an answer-quality pass. Keep safe abstentions and failures in the record rather than rerunning until a favorable answer appears. Use **Evaluation** with independent, human-labeled business cases before relying on the routing policy for the team's actual work.

Run one acceptance process at a time on the 16 GB Mac. Record elapsed time and inspect memory pressure with the application services running. Larger contexts, concurrent models, and image inputs can change resource use substantially. Results for one synthetic case do not establish latency under concurrent team use. See [ACCEPTANCE.md](../docs/ACCEPTANCE.md) for recorded outcomes and remaining limits.

## Custom model and semantic-retrieval regression

`validate_local.py` uses a temporary database and synthetic sources with real Ollama calls. RAG uses real Ollama embeddings and Qdrant rather than mocked vectors. The script enforces `local_only` and makes no cloud model calls. It does not place supplier orders or modify a customer-support system.

Start Ollama and Qdrant, install the chat and embedding models in Ollama, then run from the project root:

```bash
python -m scripts.validate_local --model qwen2.5:7b \
  --ollama-url http://127.0.0.1:11434 \
  --qdrant-url http://127.0.0.1:6333 \
  --embedding-model bge-m3
```

Use `--case sql` for inventory analysis or `--case rag` for policy retrieval. Model names and service addresses can also come from `.env`. `--output` sets the full report path; the default is `output/local-acceptance.json`.

The script checks for a substantive model recommendation, pending human review, valid citations, and the correct tool route. The SQL case checks Widget A's stock of 4 against a minimum of 10. The RAG case checks first response within one business day and a supervisor's daily assignment of an owner. Exit code 0 requires every check to pass. `gather_data` is a safe abstention, but does not pass recommendation quality for these two unambiguous factual cases. Model versions, hardware speed, and output variation can cause failure; preserve the report and investigate instead of skipping failed checks.

The earlier acceptance on 2026-10-24 used Qwen2.5:7b, bge-m3, and Qdrant 1.17.0. SQL took about 36 seconds and five model calls; RAG took about 32 seconds and four model calls. Both produced recommendations with valid citations. The 7B model still made auditing errors and abstained on complex churn causality and policy approval distinctions. These results predate the Qwen3.5 profiles and verify synthetic engineering cases, not comprehensive decision quality or real business outcomes.

The temporary database is deleted when the script exits. Test vectors use a separate Qdrant workspace and cannot appear in production retrieval. They still occupy disk space; use an isolated acceptance Qdrant instance and remove it after testing if cleanup is needed.

OpenAI Decisions is a separate cloud option and is deliberately blocked in this local-only acceptance path. Use the provider and application checks described in [ACCEPTANCE.md](../docs/ACCEPTANCE.md) to validate cloud decisions.
