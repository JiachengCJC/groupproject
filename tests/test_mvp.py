"""Run inside Docker as documented in README.md; no keys or network needed."""
from concurrent.futures import ThreadPoolExecutor
import csv
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Database
from app.engine import ACTIONS, Engine, baseline_decision, checked_claims
from app.main import create_app
from app.providers import AgentFindings, ProviderError, Providers, QueryPlan, Rationale
from app.retrieval import retrieve
from app.seed import SCENARIOS, scenario_csv, seed


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(db_path=Path(self.tmp.name)/"test.sqlite3")
        self.db = Database(self.settings.db_path)
        self.db.initialize()
        seed(self.db)
        self.engine = Engine(self.db, self.settings)

    def tearDown(self):
        self.tmp.cleanup()

    def analyze(self, dataset="demo_pricing", **kw):
        return self.engine.analyze({"question": "What should our business prioritize and why?",
                                   "dataset_id": dataset, "agent_mode": "local", "decision_mode": "baseline", **kw})

    def test_all_five_scenarios_and_evidence(self):
        for s in SCENARIOS:
            with self.subTest(case=s["id"]):
                r = self.analyze("demo_"+s["id"])
                self.assertEqual(r["decision"]["choice"], s["expected_action"])
                self.assertEqual(len(r["agents"]), 4)
                self.assertEqual(r["measurement"]["citation_coverage"], 1)
                self.assertEqual(r["measurement"]["tool_calls"], 9)
                self.assertIsNone(r["decision"]["probabilities"])
                self.assertIsNone(r["decision"]["confidence"])
                ids = {e["id"] for e in r["evidence"]}
                for a in r["agents"]:
                    checked_claims(a["claims"], ids)

    def test_metrics_from_data_not_scenario_labels(self):
        changed = {**SCENARIOS[0], "current": SCENARIOS[2]["current"]}
        d = self.db.import_csv("Unseen dataset name", scenario_csv(changed))
        r = self.analyze(d["id"])
        self.assertEqual(r["decision"]["choice"], "fix_quality")
        self.assertAlmostEqual(r["metrics"]["return_rate"], 145/920)

    def test_generic_schema_reports_insufficient_evidence(self):
        d = self.db.import_csv("Custom", "name,value\nSample,10\nOther,20\n")
        r = self.analyze(d["id"])
        self.assertEqual(r["decision"]["choice"], "gather_data")
        self.assertEqual(r["metrics"], {})
        self.assertFalse(any(e["kind"] == "document" for e in r["evidence"]))

    def test_zero_denominators_do_not_create_fake_certainty(self):
        changed = {**SCENARIOS[0], "current": {"units": 0, "returns": 0}}
        d = self.db.import_csv("Zero revenue", scenario_csv(changed))
        r = self.analyze(d["id"])
        self.assertEqual(r["decision"]["choice"], "gather_data")
        self.assertIsNone(r["metrics"]["return_rate"])

    def test_bad_rows_cannot_be_hidden_by_aggregation(self):
        for bad in [None, -10]:
            rows = list(csv.DictReader(io.StringIO(scenario_csv(SCENARIOS[0]))))
            rows[3]["revenue"] = "" if bad is None else str(bad)
            out = io.StringIO()
            writer = csv.DictWriter(out, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
            d = self.db.import_csv("Incomplete rows", out.getvalue())
            with self.subTest(value=bad):
                self.assertEqual(self.analyze(d["id"])["decision"]["choice"], "gather_data")

    def test_extra_period_is_not_silently_ignored(self):
        content = scenario_csv(SCENARIOS[0]).replace("previous", "2025-01")
        d = self.db.import_csv("Wrong periods", content)
        self.assertEqual(self.analyze(d["id"])["decision"]["choice"], "gather_data")

    def test_csv_normalization_and_atomic_rejection(self):
        d = self.db.import_csv("Metrics", '\ufeffUnit Price,Product Name\n12.5,"Core, Plus"\n,Lite\n')
        self.assertEqual(d["columns"], [{"name":"unit_price","type":"REAL"},{"name":"product_name","type":"TEXT"}])
        result = self.db.query(f'SELECT * FROM "{d["table_name"]}"', d["id"])
        self.assertIsNone(result["rows"][1]["unit_price"])
        count = len(self.db.datasets())
        for content in ["", "a,A\n1,2\n", "a,b\n1\n", "a,b\n", ",b\n1,2\n"]:
            with self.subTest(content=content), self.assertRaises(ValueError):
                self.db.import_csv("Invalid", content)
        self.assertEqual(len(self.db.datasets()), count)

    def test_sql_blocks_writes_metadata_other_datasets_and_functions(self):
        for sql in [
            "DELETE FROM data_demo_pricing", "DROP TABLE data_demo_pricing",
            "SELECT * FROM runs", "SELECT * FROM sqlite_master", "SELECT * FROM data_demo_retention",
            "SELECT * FROM pragma_table_info('runs')", "SELECT load_extension('evil')",
            "SELECT randomblob(1000000000)", "SELECT 1; DELETE FROM data_demo_pricing",
            "WITH t AS (SELECT * FROM reviews) SELECT * FROM t",
        ]:
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                self.db.query(sql, "demo_pricing")
        self.assertEqual(self.db.query("SELECT COUNT(*) AS n FROM data_demo_pricing", "demo_pricing")["rows"][0]["n"], 6)

    def test_sql_cte_and_result_limit(self):
        q = self.db.query("WITH x AS (SELECT revenue FROM data_demo_pricing) SELECT SUM(revenue) AS revenue FROM x", "demo_pricing")
        self.assertEqual(q["rows"][0]["revenue"], 176000)
        q = self.db.query("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<300) SELECT x FROM n", "demo_pricing")
        self.assertEqual(len(q["rows"]), 200)
        self.assertTrue(q["truncated"])

    def test_retrieval_scoping_and_empty_match(self):
        self.db.add_document("Unique term", "quartzplatypus special context", "test", "demo_retention")
        self.assertEqual(retrieve(self.db, "quartzplatypus", "demo_pricing"), [])
        self.assertEqual(retrieve(self.db, "quartzplatypus", "demo_retention")[0]["content"], "quartzplatypus special context")
        self.db.add_document("Shared policy", "sharedzebra policy", "test")
        self.assertEqual(len(retrieve(self.db, "sharedzebra", "demo_pricing")), 1)

    def test_long_document_chunks_preserve_citations(self):
        d = self.db.add_document("Long", " ".join(f"word{i}" for i in range(400)), "test", "demo_pricing")
        self.assertEqual(d["chunk_count"], 3)
        hits = retrieve(self.db, "word390", "demo_pricing")
        self.assertEqual(hits[0]["ordinal"], 3)
        self.assertEqual(hits[0]["document_id"], d["id"])

    def test_invalid_citation_is_rejected(self):
        for ids in [[], ["made-up-source"]]:
            with self.assertRaises(ValueError):
                checked_claims([{"text":"unsupported", "evidence_ids":ids}], {"metrics"})

    def test_seed_is_idempotent(self):
        seed(self.db)
        self.assertEqual(len(self.db.datasets()), 5)
        self.assertEqual(len(self.db.documents()), 5)

    def test_review_is_atomic_and_single_use(self):
        run_id = self.db.create_run({"question":"Test", "dataset_id":"demo_pricing"})
        self.db.finish_run(run_id, self.analyze())
        def approve():
            try:
                self.db.review(run_id, "approved", "QA reviewer", "Synthetic test")
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: approve(), range(2)))
        self.assertEqual(sum(outcomes), 1)
        self.assertEqual(self.db.run(run_id)["status"], "approved")
        reopened = Database(self.db.path)
        self.assertEqual(reopened.run(run_id)["review"]["reviewer"], "QA reviewer")

    def test_live_pipeline_with_contract_fixtures(self):
        def structured(schema, instructions, context):
            if schema is QueryPlan:
                return {"sql":"SELECT SUM(revenue) AS revenue FROM data_demo_pricing", "search_query":"pricing"}, {"input_tokens":10}
            if schema is AgentFindings:
                return {"summary":"Evidence is available.", "claims":[{"text":"Revenue is present in the query results.", "evidence_ids":["metrics"]}], "limitations":["Contract test only"]}, {"input_tokens":20}
            self.assertIs(schema, Rationale)
            return {"explanation":"The reported metrics support reviewing a targeted offer.", "evidence_ids":["metrics"]}, {"input_tokens":10}
        with patch.object(self.engine.providers, "structured", side_effect=structured) as provider:
            result = self.analyze(agent_mode="live")
        self.assertEqual(provider.call_count, 9)
        self.assertEqual(result["agent_mode"], "live")

    def test_provider_failure_is_not_silently_replaced(self):
        run_id = self.db.create_run({"question":"Compare our prices", "dataset_id":"demo_pricing", "agent_mode":"live"})
        with patch.object(self.engine.providers, "structured", side_effect=ProviderError("Provider is unavailable")):
            self.engine.run_job(run_id)
        self.assertEqual(self.db.run(run_id)["status"], "failed")
        self.assertIsNone(self.db.run(run_id)["result"])

    def test_openai_decisions_official_contract_and_probability_validation(self):
        providers = Providers(Settings(openai_key="test-key"))
        probabilities = {k: .8 if k == "protect_margin" else .04 for k in ACTIONS}
        answer = {"type":"choice", "name":"next_action", "choice":"protect_margin", "probabilities":[
            {"value":key,"probability":value} for key,value in probabilities.items()], "confidence":.76}
        response = Mock()
        response.json.return_value = {"model":"gpt-6-luna", "answers":[answer], "usage":{}}
        with patch("app.providers.httpx.post", return_value=response) as post:
            result = providers.openai_decisions({"question":"Test"}, {k:v["description"] for k,v in ACTIONS.items()})
            self.assertEqual(result["choice"], "protect_margin")
            self.assertEqual(result["probabilities"], probabilities)
            self.assertEqual(post.call_args.args[0], "https://api.openai.com/v1/decisions")
            self.assertIn("choices", post.call_args.kwargs["json"]["questions"][0])
        for bad in [float("nan"), -.1, 1.2, True]:
            answer["probabilities"][0]["probability"] = bad
            with self.subTest(value=bad), patch("app.providers.httpx.post", return_value=response), self.assertRaises(ProviderError):
                providers.openai_decisions({}, {k:v["description"] for k,v in ACTIONS.items()})


class APITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/"api.sqlite3"
        self.client = TestClient(create_app(Settings(db_path=self.path)))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def complete_analysis(self, **kw):
        response = self.client.post("/api/analyses", json={"question":"Should we protect margins or match a competitor?", "dataset_id":"demo_pricing", **kw})
        self.assertEqual(response.status_code, 202, response.text)
        run_id = response.json()["id"]
        for _ in range(200):
            run = self.client.get("/api/analyses/"+run_id).json()
            if run["status"] not in {"running","queued"}:
                break
            time.sleep(.01)
        self.assertEqual(run["status"], "pending_review", run)
        return run_id

    def test_approval_gate_and_persisted_export(self):
        run_id = self.complete_analysis()
        url = "/api/analyses/"+run_id
        self.assertEqual(self.client.get(url+"/export").status_code, 409)
        self.assertEqual(self.client.post(url+"/review", json={"decision":"approved","reviewer":"QA reviewer","notes":"Test only"}).status_code, 200)
        response = self.client.get(url+"/export")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["review"]["reviewer"], "QA reviewer")
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertEqual(self.client.post(url+"/review", json={"decision":"approved","reviewer":"Another reviewer"}).status_code, 409)

    def test_rejection_requires_notes_and_revision_carries_them(self):
        run_id = self.complete_analysis()
        url = "/api/analyses/"+run_id
        self.assertEqual(self.client.post(url+"/review", json={"decision":"rejected","reviewer":"QA reviewer"}).status_code, 422)
        self.assertEqual(self.client.post(url+"/review", json={"decision":"rejected","reviewer":"QA reviewer","notes":"Recheck assumptions"}).status_code, 200)
        child_id = self.complete_analysis(parent_run_id=run_id)
        child = self.client.get("/api/analyses/"+child_id).json()
        self.assertEqual(child["request"]["reviewer_notes"], "Recheck assumptions")
        self.assertEqual(child["request"]["parent_run_id"], run_id)

    def test_upload_and_preview(self):
        response = self.client.post("/api/datasets", json={"name":"New data", "content":"Product,Revenue\nCore,12\n"})
        self.assertEqual(response.status_code, 201)
        dataset_id = response.json()["id"]
        preview = self.client.get("/api/datasets/"+dataset_id).json()
        self.assertEqual(preview["preview"]["rows"], [{"product":"Core", "revenue":12.0}])
        response = self.client.post("/api/documents", json={"title":"SOP", "content":"A useful policy", "dataset_id":dataset_id})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.client.get("/api/documents/"+response.json()["id"]).json()["content"], "A useful policy")

    def test_missing_keys_and_invalid_input(self):
        for mode in [{"agent_mode":"live"},{"decision_mode":"openai_decisions"}]:
            response = self.client.post("/api/analyses", json={"question":"Analyze business performance", "dataset_id":"demo_pricing", **mode})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post("/api/analyses", json={"question":"  ","dataset_id":"demo_pricing"}).status_code, 422)
        self.assertEqual(self.client.get("/api/analyses/missing").status_code, 404)

    def test_cross_origin_and_large_requests_blocked(self):
        self.assertEqual(self.client.post("/api/benchmarks", json={}, headers={"Origin":"https://example.org"}).status_code, 403)
        self.assertEqual(self.client.post("/api/benchmarks", content="{}", headers={"Content-Type":"text/plain"}).status_code, 415)
        self.assertEqual(self.client.post("/api/benchmarks", content=b"x"*3_000_001, headers={"Content-Type":"application/json"}).status_code, 413)

    def test_benchmark_is_persisted_and_explicitly_synthetic(self):
        response = self.client.post("/api/benchmarks", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["pass_rate"], 1)
        self.assertIn("Synthetic regression", response.json()["note"])
        self.assertEqual(self.client.get("/api/benchmarks").json()["id"], response.json()["id"])

    def test_web_assets_and_configuration_contain_no_keys(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertIn("Market Lens", self.client.get("/").text)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        config = self.client.get("/api/config").json()
        self.assertFalse(config["live_agents_available"])
        self.assertNotIn("openai_key", config)
        self.assertFalse(config["openai_decisions_available"])
        self.assertEqual(config["openai_decision_model"], "gpt-6-luna")


if __name__ == "__main__":
    unittest.main()
