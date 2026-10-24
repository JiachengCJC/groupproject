"""Opt-in local profile acceptance with real Ollama models and isolated fixtures."""
import argparse
import io
import json
from pathlib import Path
import tempfile

from PIL import Image, ImageDraw, ImageFont
from sqlglot import exp, parse_one

from app.config import Settings
from app.database import Database
from app.engine import Engine
from app.evaluation import _canonical_rows
from app.ingestion import FileParser
from app.providers import Providers


def inventory_facts_match(evidence):
    """Compare the three gold facts, allowing aliases and additional SQL columns."""
    expected = [{"product": "Widget A", "stock_units": 4, "minimum_required": 10}]
    fields = tuple(expected[0])
    for item in evidence:
        if item.get("kind") != "sql" or item.get("truncated"):
            continue
        try:
            statement = parse_one(item["sql"])
            selected = statement if isinstance(statement, exp.Select) else statement.find(exp.Select)
            aliases = {field: field for field in fields}
            for expression in selected.expressions:
                value = expression.unalias()
                while isinstance(value, exp.Cast):
                    value = value.this
                if isinstance(value, exp.Column) and value.name in fields:
                    aliases[value.name] = expression.alias_or_name
            projected = [{field: row[aliases[field]] for field in fields} for row in item.get("rows", [])]
            if _canonical_rows(projected) == _canonical_rows(expected):
                return True
        except (KeyError, TypeError, AttributeError, ValueError):
            continue
    return False


def vision_case(settings):
    image = Image.new("RGB", (1100, 400), "white")
    fonts = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
             "/System/Library/Fonts/Supplemental/Arial.ttf"]
    font = next((ImageFont.truetype(path, 42) for path in fonts if Path(path).exists()),
                ImageFont.load_default(size=42))
    ImageDraw.Draw(image).multiline_text((45, 45), "INVENTORY CHECK\nProduct: Widget A\nStock: 4 units\nMinimum required: 10 units", font=font, fill="black", spacing=18)
    data = io.BytesIO()
    image.save(data, format="PNG")
    providers = Providers(settings)
    original = providers.structured
    usage = []

    def measured(*args, **kwargs):
        result, measured_usage = original(*args, **kwargs)
        usage.append(measured_usage)
        return result, measured_usage

    providers.structured = measured
    parsed = FileParser(settings, providers, vision_provider="ollama",
                        vision_model=settings.ollama_vision_model).parse("inventory.png", data.getvalue())
    text = " ".join(part["content"] for part in parsed.segments).casefold()
    checks = {"product": "widget a" in text, "stock": "4" in text, "minimum": "10" in text,
              "vision_model_used": bool(usage) and all(item["model"] == settings.ollama_vision_model for item in usage)}
    return {"case": "vision", "passed": all(checks.values()), "checks": checks,
            "model": settings.ollama_vision_model, "usage": usage, "extracted_text": text}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["fast", "quality", "auto", "all"], default="all")
    parser.add_argument("--case", choices=["sql", "rag", "hybrid", "vision", "all"], default="all")
    parser.add_argument("--ollama-url", help="Local Ollama address; defaults to OLLAMA_BASE_URL")
    parser.add_argument("--output", type=Path, default=Path("output/local-profiles-acceptance.json"))
    args = parser.parse_args()
    settings = Settings.from_env()
    settings.local_only = True
    settings.orchestration = "langgraph"
    settings.ollama_base_url = args.ollama_url or settings.ollama_base_url
    settings.ollama_vision_model = settings.ollama_vision_model or settings.ollama_fast_model
    settings.retrieval_mode = "lexical"
    settings.embedding_provider = "lexical"
    settings.mlflow_tracking_uri = ""
    settings.ollama_keep_alive = "0"
    settings.ollama_thinking = "off"
    settings.max_agent_steps = 1
    settings.max_model_calls = 12
    settings.analysis_timeout = 300
    records = []

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({
            "fixture_kind": "Synthetic engineering acceptance, not independent business validation",
            "retrieval": "lexical; semantic retrieval is checked separately",
            "cases": records}, ensure_ascii=False, indent=2)+"\n")

    with tempfile.TemporaryDirectory(prefix="market-lens-profiles-") as directory:
        db = Database(Path(directory)/"fixtures.sqlite3", workspace_id=Path(directory).name)
        db.initialize()
        dataset = db.import_csv("Synthetic inventory", "product,stock_units,minimum_required\nWidget A,4,10\nWidget B,15,8\n", synthetic=True)
        db.put_record("dictionaries", dataset["id"], {
            "stock_units": "Physical units currently available.",
            "minimum_required": "Internal minimum inventory threshold in physical units. Low stock requires review, not automatic order placement."})
        support = db.add_document("Support policy", "The first response to every new support ticket is due within one business day. The support lead reviews the open queue every morning and assigns an owner to each unanswered ticket.", "Synthetic acceptance fixture", synthetic=True)
        inventory = db.add_document("Replenishment policy", "When stock_units is below minimum_required, review replenishment. The purchasing manager must approve any purchase order before it is placed. An analysis recommendation does not authorize an order.", "Synthetic acceptance fixture", dataset_id=dataset["id"], synthetic=True)
        cases = {
            "sql": {"question": "Which products have stock below their minimum_required threshold? Recommend reviewing replenishment for those products, using the exact stock and minimum values.", "dataset_id": dataset["id"], "document_ids": []},
            "rag": {"question": "According to the customer support policy, when is the first response due and what daily action should the support lead take?", "document_ids": [support["id"]]},
            "hybrid": {"question": "Which products need replenishment review under the policy, and who must approve any purchase order? Cite stock and minimum values plus the policy. No action is authorized by this analysis.", "dataset_id": dataset["id"], "document_ids": [inventory["id"]]},
        }
        for profile in ["fast", "quality", "auto"] if args.profile == "all" else [args.profile]:
            for name, fixture in cases.items():
                if args.case not in ("all", name):
                    continue
                request = {**fixture, "agent_mode": "ollama", "decision_mode": "llm", "local_profile": profile,
                           "routing_mode": name, "max_steps": 1}
                try:
                    result = Engine(db, settings).analyze(request, lambda stage, _: print(profile+"/"+name+": "+stage, flush=True))
                    routing = result.get("local_routing", {})
                    references = set(result.get("rationale_evidence_ids", []))
                    evidence = result["evidence"]
                    usage = result["measurement"]["provider_usage"]
                    allowed = {settings.ollama_fast_model, settings.ollama_quality_model}
                    checks = {
                        "named_profile": routing.get("profile") == profile,
                        "normal_recommendation": result["decision"]["provider"] == "ollama" and result["decision"]["choice"] == "model_recommendation",
                        "valid_references": bool(references) and references <= {item["id"] for item in evidence},
                        "only_local_models": bool(usage) and all(item.get("provider") == "ollama" and item.get("model") in allowed for item in usage),
                        "routing_explained": bool(routing.get("reason")),
                    }
                    expected = settings.ollama_quality_model if profile == "quality" or (profile == "auto" and name == "hybrid") else settings.ollama_fast_model
                    checks["expected_analysis_model"] = routing.get("analysis_model") == expected
                    if profile == "auto":
                        checks["fast_planner"] = routing.get("planner_model") == settings.ollama_fast_model
                    tools = {item["tool"] for item in result["tool_trace"]}
                    checks["expected_evidence_tools"] = ({"sql"} if name == "sql" else {"retrieval"} if name == "rag" else {"sql", "retrieval"}) <= tools
                    if name in {"sql", "hybrid"}:
                        checks["correct_inventory"] = inventory_facts_match(evidence)
                    wording = (result.get("rationale", "")+" "+" ".join(claim["text"] for agent in result.get("agents", []) for claim in agent.get("claims", []))).casefold()
                    if name == "rag":
                        checks["correct_policy"] = "one business day" in wording and "owner" in wording
                    if name == "hybrid":
                        checks["correct_approval_role"] = "purchasing manager" in wording
                    record = {"profile": profile, "case": name, "passed": all(checks.values()), "checks": checks, "result": result}
                except Exception as exc:
                    record = {"profile": profile, "case": name, "passed": False, "error_type": type(exc).__name__, "error": str(exc)}
                records.append(record)
                save()
                print(json.dumps({key: value for key, value in record.items() if key != "result"}), flush=True)
        if args.case in {"all", "vision"}:
            try:
                record = vision_case(settings)
            except Exception as exc:
                record = {"case": "vision", "passed": False, "error_type": type(exc).__name__, "error": str(exc)}
            records.append(record)
            save()
            print(json.dumps({key: value for key, value in record.items() if key not in {"extracted_text", "usage"}}), flush=True)
    raise SystemExit(0 if records and all(record["passed"] for record in records) else 1)


if __name__ == "__main__":
    main()
