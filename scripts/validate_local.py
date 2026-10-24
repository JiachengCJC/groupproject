"""Opt-in local SQL/RAG acceptance with synthetic fixtures and real model calls."""
import argparse
import json
from pathlib import Path
import tempfile

from app.config import Settings
from app.database import Database
from app.engine import Engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=["sql", "rag", "all"], default="all")
    parser.add_argument("--model", help="Installed Ollama chat model; defaults to OLLAMA_MODEL")
    parser.add_argument("--ollama-url", help="Defaults to OLLAMA_BASE_URL")
    parser.add_argument("--qdrant-url", help="Required for RAG unless QDRANT_URL is configured")
    parser.add_argument("--embedding-model", help="Installed Ollama embedding model; defaults to EMBEDDING_MODEL or bge-m3")
    parser.add_argument("--output", type=Path, default=Path("output/local-acceptance.json"))
    args = parser.parse_args()
    settings = Settings.from_env()
    settings.local_only = True
    settings.orchestration = "langgraph"
    settings.ollama_model = args.model or settings.ollama_model
    settings.ollama_base_url = args.ollama_url or settings.ollama_base_url
    settings.qdrant_url = args.qdrant_url or settings.qdrant_url
    settings.embedding_provider = "ollama"
    settings.embedding_model = args.embedding_model or settings.embedding_model or "bge-m3"
    settings.embedding_revision = ""  # Resolve the actual installed local model digest.
    settings.retrieval_mode = "hybrid"
    settings.max_agent_steps = 1
    settings.max_model_calls = 10
    settings.analysis_timeout = 300
    settings.ollama_timeout = min(settings.ollama_timeout, 120)
    settings.ollama_context = 8192
    if not settings.ollama_model:
        parser.error("Set --model or OLLAMA_MODEL to an installed chat model.")
    if args.case != "sql" and not settings.qdrant_url:
        parser.error("RAG acceptance requires --qdrant-url or QDRANT_URL and a running Qdrant server.")

    records = []
    with tempfile.TemporaryDirectory(prefix="market-lens-local-acceptance-") as directory:
        db = Database(Path(directory)/"fixtures.sqlite3", workspace_id=Path(directory).name)
        db.initialize()
        dataset = db.import_csv("Synthetic inventory",
            "product,stock_units,minimum_required\nWidget A,4,10\nWidget B,15,8\n", synthetic=True)
        db.put_record("dictionaries", dataset["id"], {
            "stock_units": "Physical units currently available.",
            "minimum_required": "Internal minimum inventory threshold in physical units. A low stock item requires replenishment review; this is not an order authorization."})
        document = db.add_document("Support policy",
            "Customer support response policy: the first response to every new support ticket is due within one business day. "
            "The support lead reviews the open queue every morning and assigns an owner to each unanswered ticket.",
            "Synthetic validation fixture", synthetic=True)
        cases = {
            "sql": {"question": "Which products have stock below their minimum_required threshold? Recommend reviewing replenishment for those products, using the exact stock and minimum values.",
                    "dataset_id": dataset["id"], "document_ids": []},
            "rag": {"question": "According to the customer support policy, when is the first response due and what daily action should the support lead take?",
                    "document_ids": [document["id"]]},
        }
        for name, request in cases.items():
            if args.case not in ("all", name):
                continue
            request.update(agent_mode="ollama", model=settings.ollama_model, decision_mode="llm", routing_mode=name, max_steps=1)
            try:
                result = Engine(db, settings).analyze(request, lambda stage, _: print(name+": "+stage, flush=True))
                evidence = result["evidence"]
                references = set(result["rationale_evidence_ids"])
                checks = {
                    "normal_recommendation": result["decision"]["provider"] == "ollama" and result["decision"]["choice"] == "model_recommendation",
                    "reviewable": result["verification"]["status"] == "review_required",
                    "valid_references": bool(references) and references <= {e["id"] for e in evidence},
                    "expected_tools": {t["tool"] for t in result["tool_trace"]} == ({"sql"} if name == "sql" else {"retrieval"}),
                }
                if name == "sql":
                    rows = [row for e in evidence if e["kind"] == "sql" for row in e["rows"]]
                    checks["correct_inventory"] = any(row.get("product") == "Widget A" and row.get("stock_units") == 4
                        and row.get("minimum_required") == 10 for row in rows) and not any(row.get("product") == "Widget B" for row in rows)
                else:
                    wording = (result["rationale"]+" "+" ".join(c["text"] for a in result["agents"] for c in a["claims"])).casefold()
                    checks["correct_policy_facts"] = "one business day" in wording and "owner" in wording and ("every morning" in wording or "daily" in wording)
                    checks["scoped_documents"] = bool(evidence) and {e["document_id"] for e in evidence} == {document["id"]}
                record = {"case": name, "passed": all(checks.values()), "checks": checks, "result": result}
            except Exception as error:
                record = {"case": name, "passed": False, "error_type": type(error).__name__}
            records.append(record)
            print(json.dumps({k: v for k, v in record.items() if k != "result"}, ensure_ascii=False), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"provider": "ollama", "model": settings.ollama_model,
        "fixture_kind": "Synthetic engineering acceptance, not independent business validation", "cases": records}, ensure_ascii=False, indent=2)+"\n")
    raise SystemExit(0 if all(record["passed"] for record in records) else 1)


if __name__ == "__main__":
    main()
