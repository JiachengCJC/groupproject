# Implementation and acceptance record

Date: 2026-10-24. Target: a small internal team using local Ollama models and the official OpenAI API. This record separates feature implementation, real-service integration, and business quality. Synthetic cases are not real team trials, and successful tests do not prove that software has no unknown defects.

## Local model profiles update

This update adds **Local Fast**, **Local Quality**, and **Local Auto**, alongside **Custom local**. The configured roles are Qwen3.5 4B for fast analysis and image understanding, Qwen3.5 9B for quality analysis, and the existing BGE-M3 model for document embeddings. The deployment target is the owner's Apple M4 with 16 GB memory; models run under bounded work and memory constraints rather than requiring every model to stay resident together.

Fast uses the fast model throughout. Quality uses the quality model throughout. Auto starts with the fast planner and selects the subsequent analysis model using a documented routing policy, retaining the reason and actual per-stage model usage. Named local profiles use local model recommendations and reject cloud decisions and cloud embeddings. Custom local keeps manual model selection and the explicit option to pair local analysis with OpenAI Decisions. Deployment-wide `LOCAL_ONLY=true` separately blocks online source synchronization and cloud vision.

Auto's initial quality triggers are multiple tables, combined SQL/document routes, multiple specialist tasks, revision instructions, or explicit candidate comparison; requesting an extra evidence round also escalates. New submissions record model digests and thinking/context/output controls; queued runs reject changed weights or changed generation controls. Legacy requests default to Custom local. Provider capability checks, explicit thinking controls, generation limits, and per-call unloading support the memory-constrained deployment; they do not prove that a workload fits or that a larger model produces a better answer.

The final production Docker image passed **180 backend tests with zero failures, errors, or skips** in 21.008 seconds, including real PostgreSQL analysis-role enforcement, Qdrant/BGE-M3 retrieval, OCR, and Office conversion. The 14 new profile tests and 10 provider tests are included in that total. The frontend production build and lint checks passed, with **12 unit tests and 27 browser regressions**. One final live browser workflow also passed against an isolated database using the final image.

All **10 real-model acceptance cases passed the defined fact and routing checks**: SQL, RAG, and hybrid analysis in each of the three profiles, plus one image-understanding case. Text cases used the expected local models and valid evidence references. Auto retained 4B for the single-route SQL/RAG cases and used its 4B planner followed by 9B analysis for the hybrid case. The actual 4B vision call extracted the product, stock, and minimum values from the synthetic image. Observed text-analysis durations on the M4 are below; these are individual synthetic cases, not throughput or P95 benchmarks.

| Profile | Inventory SQL | Policy RAG | Inventory + approval-policy hybrid |
| --- | --- | --- | --- |
| Local Fast | 44.76 seconds | 40.44 seconds | 51.72 seconds |
| Local Quality | 76.46 seconds | 72.68 seconds | 98.43 seconds |
| Local Auto | 40.88 seconds | 34.95 seconds | 90.10 seconds |

The original Fast SQL and Auto SQL scores rejected equivalent column aliases. The corrected checker projects the three gold inventory facts from the SQL, compares their values and row multiplicity, and permits additional output columns. The model responses were not changed. Original scores are retained in `output/local-profiles-acceptance-original-scores.json`; final results and the scoring explanation are in `output/local-profiles-acceptance.json`. Profile cases use lexical retrieval to isolate generation and routing; the Docker regression separately validates semantic retrieval.

One Quality hybrid query added an unnecessary `manual_review` literal column. Its checked inventory facts and final document-based purchasing-manager approval requirement were correct, but acceptance does not validate the meaning of every extra generated SQL column. Read-only SQL enforcement is not a guarantee of semantic correctness. This observed limitation remains in the acceptance artifact, and the cases do not establish that the larger model is always more accurate.

The result and approved HTML/PDF exports include the local mode, planner, analysis model, and routing reason. Actual per-stage usage identifies executed models; configured vision and embedding roles do not imply those models were called in every run. An Ollama recommendation title that reaches its schema character limit is rejected and retried for a concise title within the existing call budget, with a provider regression covering the boundary. The completed checks below belong to prior builds and do not substitute for final local-profile acceptance. Model availability, valid structured output, source correctness, actual routing, safe abstention, latency, and resource behavior are separate claims.

The final live **Local Auto** workflow took 48.4 seconds overall, including 41.855 seconds of analysis and four local model calls. The planner and analysis correctly retained Qwen3.5 4B for the single-route policy question. Checks covered the profile and actual usage, policy facts and references, the keyword-retrieval label, review and approval, PDF/HTML/JSON exports, file upload, session recovery, and English desktop/mobile layouts. Evidence: `output/local-profiles-browser-validation.json`. This browser fixture used keyword retrieval; semantic retrieval was exercised separately in the Docker integration suite.

The final two-page PDF was independently rendered on every page with PyMuPDF. English headings, routing details, and the complete recommendation title were verified; the first page was visually inspected. The HTML export also passed its English-language and routing-content checks.

The application and worker were upgraded to the verified image and are healthy at http://127.0.0.1:8002. All three named profiles are available with their configured models installed. Production uses Qwen3.5 4B for local image understanding, BGE-M3 with hybrid retrieval, thinking disabled, and per-call unloading. The existing account and records were preserved: one user, five datasets, five documents, and one analysis run before and after the upgrade. This is a verified internal-team release within the scope above; it is not a universal business-quality or capacity guarantee.

## Previous OpenAI Decisions and English-language update

This update replaces the active TypeSafe JEV integration with **OpenAI Decisions**. It uses the existing `OPENAI_API_KEY` and `OPENAI_DECISION_MODEL=gpt-6-luna`; a TypeSafe account is no longer required. Software-authored interface text and documentation are English. Uploaded source content and existing report evidence retain their original language.

The provider contract is `POST /v1/decisions` with a choice question, shared evidence, and candidate descriptions. The returned selected choice, probabilities, and confidence are validated and retained. Those probabilities are model estimates, not observed business success rates. See the [official Decisions guide](https://developers.openai.com/api/docs/guides/decisions).

Compatibility behavior preserves saved results and approval hashes. New requests, evaluation inputs, schedules, and reruns containing the old `decision_mode="jev"` value are normalized to `openai_decisions`. Existing approved reports remain attributed to the provider that originally produced them. Local-only mode blocks cloud decisions. Responses and Decisions use separate model and pricing settings.

Real checks for this update used the configured OpenAI account and `gpt-6-luna`:

| Check | Result |
| --- | --- |
| Direct Decisions API | HTTP 200 with a selected choice, probabilities, and confidence. Evidence: `output/openai-decisions-api-validation.json`. |
| Application baseline analysis followed by Decisions | Confirmed `actual_provider=openai_decisions`, selected `protect_margin`, and validated the distribution; the analysis took about 0.43 seconds. Evidence: `output/openai-decisions-baseline-validation.json`. |
| OpenAI document analysis, Decisions, and rationale | Completed in about 10.51 seconds; confirmed the actual provider, selected `review_without_credit`, and retained probability 0.82 and confidence 0.77. Evidence: `output/openai-decisions-rag-validation.json`. |
| SQL analysis with decision selection requested | SQL results and routing were correct, but the evidence gate abstained. This is an explicit abstention, not a passed Decisions API invocation. |

That production image passed **156 backend tests with zero failures or skips**, including real PostgreSQL, Qdrant/Ollama embeddings, OCR, and Office conversion. The frontend production build, TypeScript and lint checks passed, together with **8 unit tests and 18 browser regressions**. These counts describe the Decisions and English-language release before the local-profile changes.

A separate live browser workflow passed against an isolated PostgreSQL database: first-run setup, sign-in, actual OpenAI Decisions execution, evidence inspection, approval, PDF/HTML/JSON export, upload, refresh, and sign-out/sign-in recovery. The approved decision used `gpt-6-luna`, selected `protect_margin`, and returned six valid probabilities totaling 1. Desktop and mobile screenshots were checked in English with no horizontal overflow. The five-page exported PDF was independently rendered with PyMuPDF, its English headings and extracted text were checked, and its opening and closing pages were visually inspected. HTML language and section checks also passed.

The local Qwen2.5:7b inventory regression also passed after the language update: correct stock/minimum values, a normal recommendation, valid evidence references, and the expected SQL route, in about 51.27 seconds. Evidence: `output/openai-decisions-local-regression.json`.

An authored-source audit found no literal Chinese text in application code, frontend code, documentation, configuration templates, or tests. Unicode regression fixtures, multilingual OCR, and fonts remain available to preserve original uploaded evidence. At that update's handoff, the application and worker were healthy at http://127.0.0.1:8002, with existing data counts and first-run setup state preserved. Current deployment state must be checked for the local-profile update rather than inferred from that historical result. These live checks use synthetic cases and do not measure business-outcome accuracy.

## Reference architecture module coverage

The following is the verified refactor baseline, with the decision-provider replacement identified explicitly.

| Reference module | Implementation | Verification and scope |
| --- | --- | --- |
| Kaggle datasets | Versioned kagglehub downloads; file-count and size prechecks; PostgreSQL import; source versions | Real `uciml/iris/versions/2` import of 150 rows and repeated-sync deduplication passed. |
| News articles | Specific web pages and RSS/Atom; body, timestamps, content hashes; Trafilatura extraction | Real Python website HTTPS fetch and extraction and official RSS parsing passed. Up to 20 articles per sync; source access restrictions apply. |
| Business rules / SOPs | Document upload, OCR, conversion, original-file preservation, and business dictionaries | Text, table, scan, and Office conversion tests passed, including browser upload and preview. |
| ETL / schema loader / PostgreSQL | Table parsing, types, quality summaries, data versions, and a separate read-only analysis role | SQLite and PostgreSQL contract tests passed. Internal-table access, writes, and table creation were denied. |
| Chunking + embedding / vector store | Location-aware chunks; Ollama / cloud embeddings; Qdrant; keyword and vector fusion | Real bge-m3 + Qdrant ranking, persistence, reuse, and permission filters passed. Cloud embeddings were not tested live. |
| API gateway | FastAPI authentication, sessions, workspace boundaries, and role permissions | Administrator, analyst, reviewer, viewer, session revocation, and cross-workspace isolation tests passed. |
| Orchestrator + four agents | LangGraph plans and persistent checkpoints; competitive, customer, product, and market roles selected by the question | Routing, bounded follow-up, SQL repair, recovery, cancellation, and call budgets tested; real local and OpenAI analysis calls passed. |
| Query router / Text-to-SQL / RAG | SQL-only, RAG-only, and hybrid; SQLGlot plus database-enforced read-only restrictions | All three OpenAI paths passed live. Local SQL and semantic RAG passed live. |
| Shared agent state | Structured plans, evidence snapshots, references, gaps, conflicts, candidate actions, and usage | Replaying completed nodes avoided duplicate calls. An interrupted external request without a recorded response can still be charged again. |
| Decision model, replacing JEV | OpenAI Decisions choice adapter; dynamic candidates, distribution/confidence checks, and error handling | Direct API, application baseline-to-Decisions, full RAG-to-Decisions/rationale, regression, and deployed browser checks passed. The old TypeSafe adapter was contract-tested but never verified with a live account. |
| LLM rationale | Citation constraints, model-assisted support checks, explicit inference and limitations | Real multilingual source-analysis responses and references passed. Reference existence does not prove every claim. |
| Human-in-the-loop | Authenticated reviewers, result-hash approval binding, revisions after rejection, and approved exports | Real browser approval, JSON/HTML/PDF export, refresh, and sign-in passed. A changed result cannot reuse an old approval. |
| Trace & experiment tracking | Real MLflow runs and traces, tool timings, usage, and content redaction by default | A written trace was read back successfully. Tracking failure preserves the report and reports the failure. |
| Agentic benchmark / evaluation | Development/holdout splits, human gold labels, SQL result agreement, retrieval recall, routing, citation coverage, and optional model judging | Missing labels are not scored. Labeled failed analyses score zero, with completed-case results separately reported. No real-business frozen holdout set exists yet. |
| Task completion / human approval / user testing | Workspace completion and approval rates, P50/P95, satisfaction feedback, and task success | Denominators, missing values, and failures are explicit. Feedback collection exists; real team UAT has not been conducted. |
| Latency & cost | Tool, specialist, and total timings; actual tokens; configured model price versions | Unknown prices and unmetered embeddings remain unknown. Decisions uses its own pricing settings. Local hardware and electricity are not estimated. |
| Docker / cloud infrastructure | Multi-service Compose, persistent volumes, independent worker, and Caddy HTTPS template | Clean local installation, health checks, backup, and isolated database restore passed. No public cloud server has been deployed. |

## Previous real-model acceptance

These are synthetic acceptance cases verifying engineering paths, not cross-industry accuracy measurements.

| Service and case | Result |
| --- | --- |
| OpenAI `gpt-5.4-mini-2026-03-17`, inventory SQL | About 10 seconds; SQL result matched the human reference query and the SQL route was correct. |
| Same OpenAI model, document-only SOP | About 7.4 seconds; correct RAG route; relevant-document recall and reference coverage were 1 for this single case. |
| Same OpenAI model, hybrid customer retention | Five model calls; about 16 seconds; correctly calculated 4% to 19%, stated that causal evidence was insufficient, and produced a readable MLflow trace. |
| Deployed Docker + API + worker + OpenAI, multilingual SOP | About 9.6 seconds of analysis; authentication, durable queuing, answer, approval, and a PDF with an embedded multilingual font passed. |
| Local Qwen2.5:7b, inventory SQL | About 36 seconds / five model calls; correctly identified stock of 4 below the minimum of 10 and recommended reviewing replenishment. |
| Local Qwen2.5:7b + bge-m3 + Qdrant, support SLA | About 32 seconds / four model calls; correctly cited first response within one business day and a supervisor's daily assignment of owners. |

The local 7B model produced incorrect plans or excessive skepticism for some complex retention and approval-semantics cases. The application requested more evidence conservatively. These failures remain in acceptance output and are not counted as recommendation-quality passes. Evaluate pilot work with your own sources, explicit answers, and an appropriate model.

Run `python -m scripts.validate_model --provider live --case all` for real model checks, or add `--decision-mode openai_decisions` to require actual Decisions-provider execution in the decision acceptance path; API charges apply. Successful local cases are implemented in `scripts/validate_local.py`. Raw reports are stored under the Git-ignored local `output/` directory.

The previous Responses acceptance estimated standard text usage at $0.75 per million input tokens and $4.50 per million output tokens, using the price version recorded on the test date. This is an estimate, not a bill, and does not subtract cache discounts. Those rates apply to the tested Responses model, not Decisions. [Model and pricing reference](https://developers.openai.com/api/docs/models/gpt-5.4-mini).

## Previous automation and interface acceptance

These counts belong to the previously frozen production image, before the Decisions and English-language update.

- Frontend production build, TypeScript, and lint checks passed; six unit tests and 15 browser regressions passed.
- A separate live browser workflow passed: sign-in, analysis, evidence and trace, review, three export formats, upload, refreshed preview, sign-out, and sign-in again.
- PDFs were rendered using the independent Poppler reader. Multilingual fonts were embedded, and body text, footers, and page breaks were checked; verification went beyond the `%PDF-` header.
- `pip-audit` found no known vulnerabilities across 110 locked Python dependencies; `npm audit` also reported zero. These results reflect the advisory databases available on the test date.
- A database backup was restored to an independent database, with five synthetic datasets, five documents, and analysis records checked.
- The frozen production image passed **143/143 backend tests, with zero failures, errors, or skips**. Coverage included a real PostgreSQL read-only role, Qdrant/Ollama, Tesseract OCR, LibreOffice Word/PPT conversion, and embedded PDF fonts. The host run discovered the same 143 tests and skipped only LibreOffice because it was absent on the host; Docker covered that integration.
- GitHub Actions is configured. The recorded checks ran locally and in the production image; they do not establish that the remote Actions workflow has executed.

## Implementation choices relative to the proposal

To keep a small-team deployment manageable, a PostgreSQL transactional task table and separate worker provide leases, heartbeats, idempotency, and recovery. Celery/RabbitMQ were not introduced. Data access retains the dual-database adapter and explicit repeatable migrations, rather than SQLAlchemy/Alembic. Parsing uses the verified format adapters, OCR, LibreOffice, and Trafilatura rather than Docling. Qdrant retrieval uses reciprocal-rank fusion, not a trained cross-encoder.

Original files are stored in PostgreSQL; S3 is not integrated. The target is one server serving an internal team. OIDC, external SaaS, object storage, and cross-region availability remain outside scope. Authorization and content-hash filters exclude stale vectors after source changes, but physical cleanup of old collections requires an operator.

There is no independent real-business holdout set, long-duration capacity benchmark, or real-user UAT. The software is suitable for an internal pilot within its verified scope; the evidence does not establish universal business correctness or unlimited production scale.

## Local deployment baseline

The previously delivered instance uses http://127.0.0.1:8002 and Compose project `market-lens-refactor`, saved in local `.env`. At the baseline handoff, test accounts and test data were removed so the owner could register using `SETUP_TOKEN`. The original `groupproject` application and data were preserved separately for comparison and rollback. Subsequent owner-created records must be preserved during upgrades.

The baseline defaults are Qwen2.5:7b, bge-m3, and hybrid retrieval, with the standard Ollama service running. The Responses model is pinned to `gpt-5.4-mini-2026-03-17`. Provider credentials remain in the Git-ignored `.env`. Five synthetic scenarios and their document indexes were prepared for initial use. Current service status and post-update validation should be checked separately from this historical handoff description.
