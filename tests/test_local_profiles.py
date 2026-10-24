"""Mode boundaries, model routing and persisted local profile behavior."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Database
from app.engine import Engine
from app.evaluation import run_evaluation
from app.local_profiles import prepare_local_profile, choose_auto_analysis, profile_catalog
from app.main import create_app
from app.providers import Providers, ProviderError, QueryPlan, DecisionBrief
from app.workflow import AnalysisPlan, ResearchFindings, EvidenceAudit


class LocalProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(db_path=Path(self.tmp.name)/"profiles.sqlite3", orchestration="langgraph",
                                 embedded_worker=False, seed_demo=False, ollama_fast_model="fast:4b",
                                 ollama_quality_model="quality:9b", ollama_vision_model="fast:4b")
        self.db = Database(self.settings.db_path)
        self.db.initialize()
        self.dataset = self.db.import_csv("Inventory", "product,stock,minimum\nWidget A,4,10\nWidget B,15,8\n")
        self.doc = self.db.add_document("Inventory policy", "Review replenishment for inventory below its minimum.", "Internal policy")
        self.engine = Engine(self.db, self.settings)
        self.request = {"question": "When should we review inventory replenishment?", "document_ids": [self.doc["id"]],
                        "agent_mode": "ollama", "decision_mode": "llm", "local_profile": "fast"}
        self.inventory = {"available": True, "models": ["fast:4b", "quality:9b", "legacy:7b"],
                          "details": {"fast:4b": {"digest": "fast-version-1"}, "quality:9b": {"digest": "quality-version-1"}}}
        self.route = "rag"
        self.models_called = []

    def tearDown(self):
        self.tmp.cleanup()

    def respond(self, schema, instructions, context, **kwargs):
        self.models_called.append((schema.__name__, kwargs["model"]))
        if schema is AnalysisPlan:
            value = {"objective": "Review inventory", "tasks": [{"specialist": "product", "route": self.route,
                     "objective": "Check stock and policy", "search_query": "inventory minimum replenishment"}], "missing_information": []}
        elif schema is QueryPlan:
            value = {"sql": "SELECT product, stock, minimum FROM " + self.dataset["table_name"] + " WHERE stock < minimum LIMIT 50", "search_query": "inventory minimum"}
        elif schema is ResearchFindings:
            value = {"summary": "Review the documented threshold.", "claims": [{"text": "Review replenishment when inventory is below its minimum.",
                     "evidence_ids": [context["evidence"][0]["id"]]}], "limitations": [], "next_tool": "none", "followup_sql": "", "followup_search": ""}
        elif schema is EvidenceAudit:
            value = {"unsupported_claims": [], "conflicts": [], "missing_information": [], "explanation": "The evidence supports the stated claim."}
        elif schema is DecisionBrief:
            value = {"recommendation": "Review replenishment", "explanation": "Review items against their documented stock minimum.",
                     "alternatives": ["Verify inventory counts"], "evidence_ids": [context["evidence"][0]["id"]]}
        else:
            raise AssertionError(schema)
        return value, {"input_tokens": 10, "output_tokens": 20}

    def execute(self, request=None, response=None):
        with patch.object(Providers, "ollama_models", return_value=self.inventory), \
             patch.object(self.engine.providers, "structured", side_effect=response or self.respond):
            return self.engine.analyze(request or self.request)

    def test_fast_and_quality_use_their_models_for_every_stage(self):
        for profile, expected in (("fast", "fast:4b"), ("quality", "quality:9b")):
            with self.subTest(profile=profile):
                self.models_called.clear()
                result = self.execute({**self.request, "local_profile": profile})
                self.assertEqual({model for _, model in self.models_called}, {expected})
                self.assertEqual(result["decision"]["model"], expected)
                self.assertEqual(result["local_routing"]["analysis_model"], expected)
                self.assertEqual(set(result["local_routing"]["stages"].values()), {expected})

    def test_auto_keeps_simple_document_task_on_fast_model(self):
        result = self.execute({**self.request, "local_profile": "auto"})
        self.assertEqual({model for _, model in self.models_called}, {"fast:4b"})
        self.assertIn("single evidence route", result["local_routing"]["reason"])

    def test_auto_routes_hybrid_after_fast_planner_and_tracks_actual_usage(self):
        self.route = "hybrid"
        result = self.execute({**self.request, "local_profile": "auto", "dataset_id": self.dataset["id"]})
        self.assertEqual(self.models_called[0], ("AnalysisPlan", "fast:4b"))
        self.assertEqual({model for _, model in self.models_called[1:]}, {"quality:9b"})
        self.assertEqual(result["model"], "quality:9b")
        self.assertIn("combined table and document", result["local_routing"]["reason"])
        self.assertEqual([(u["operation"], u["model"]) for u in result["measurement"]["provider_usage"]], self.models_called)

    def test_auto_structural_routing_does_not_depend_on_question_keywords(self):
        with patch.object(Providers, "ollama_models", return_value=self.inventory):
            snapshot = prepare_local_profile({**self.request, "local_profile": "auto"}, self.settings, self.engine.providers)["local_profile_snapshot"]
        plan = {"tasks": [{"route": "sql", "specialist": "product"}]}
        model, reason = choose_auto_analysis(snapshot, {"question": "ordinary question"}, plan, ["one", "two"])
        self.assertEqual(model, "quality:9b")
        self.assertIn("multiple selected data tables", reason)
        model, reason = choose_auto_analysis(snapshot, {"reviewer_notes": "Verify the reported figure."}, plan, ["one"])
        self.assertEqual(model, "quality:9b")
        self.assertIn("revision", reason)

    def test_auto_escalates_when_additional_evidence_is_requested(self):
        findings = 0
        def respond(schema, instructions, context, **kwargs):
            nonlocal findings
            value, usage = self.respond(schema, instructions, context, **kwargs)
            if schema is ResearchFindings:
                findings += 1
                if findings == 1:
                    value.update(next_tool="rag", followup_search="inventory replenishment minimum")
            return value, usage
        result = self.execute({**self.request, "local_profile": "auto", "max_steps": 2}, respond)
        stages = [model for name, model in self.models_called if name == "ResearchFindings"]
        self.assertEqual(stages, ["fast:4b", "quality:9b"])
        self.assertEqual(result["local_routing"]["analysis_model"], "quality:9b")
        self.assertIn("additional evidence round", result["local_routing"]["reason"])

    def test_custom_profile_keeps_existing_selected_model(self):
        result = self.execute({**self.request, "local_profile": "custom", "model": "legacy:7b"})
        self.assertEqual({model for _, model in self.models_called}, {"legacy:7b"})
        self.assertNotIn("local_routing", result)

    def test_direct_engine_enforces_local_boundaries_before_model_calls(self):
        for override in ({"agent_mode": "live"}, {"decision_mode": "openai_decisions"}, {"decision_mode": "baseline"}):
            with self.subTest(override=override), patch.object(self.engine.providers, "structured") as call:
                with self.assertRaises(ProviderError):
                    self.engine.analyze({**self.request, **override})
                call.assert_not_called()
        for setting, value in (("embedding_provider", "cloud"), ("ollama_base_url", "https://remote.example"),
                               ("ollama_fast_model", "fast:cloud")):
            before = getattr(self.settings, setting)
            self.settings.retrieval_mode = "hybrid" if setting == "embedding_provider" else "lexical"
            setattr(self.settings, setting, value)
            with self.subTest(setting=setting), self.assertRaises(ProviderError):
                self.engine.analyze(self.request)
            setattr(self.settings, setting, before)
        self.assertEqual(self.models_called, [])

    def test_named_profile_rejects_custom_model_and_missing_quality_model(self):
        with patch.object(Providers, "ollama_models", return_value=self.inventory):
            with self.assertRaisesRegex(ProviderError, "selects its own models"):
                self.engine.analyze({**self.request, "model": "legacy:7b"})
            self.inventory["models"].remove("quality:9b")
            with self.assertRaisesRegex(ProviderError, "quality:9b"):
                self.engine.analyze({**self.request, "local_profile": "auto"})
            result = self.execute(self.request)
            self.assertEqual(result["model"], "fast:4b")

    def test_queued_snapshot_retains_models_and_rejects_changed_weights_or_controls(self):
        with patch.object(Providers, "ollama_models", return_value=self.inventory):
            saved = prepare_local_profile(self.request, self.settings, self.engine.providers)
            self.settings.ollama_fast_model = "new:4b"
            prepared = prepare_local_profile(saved, self.settings, self.engine.providers)
            self.assertEqual(prepared["model"], "fast:4b")
            self.inventory["details"]["fast:4b"]["digest"] = "updated-weights"
            with self.assertRaisesRegex(ProviderError, "weights changed"):
                self.engine.analyze(saved)
            self.inventory["details"]["fast:4b"]["digest"] = "fast-version-1"
            self.settings.ollama_thinking = "on"
            with self.assertRaisesRegex(ProviderError, "generation settings changed"):
                self.engine.analyze(saved)

    def test_resume_preserves_auto_routing_without_replaying_completed_model_calls(self):
        self.route = "hybrid"
        with patch.object(Providers, "ollama_models", return_value=self.inventory):
            saved = prepare_local_profile({**self.request, "local_profile": "auto", "dataset_id": self.dataset["id"],
                                           "_run_id": "local-profile-resume"}, self.settings, self.engine.providers)
        def interrupted(schema, instructions, context, **kwargs):
            if schema is EvidenceAudit:
                raise RuntimeError("Synthetic interruption")
            return self.respond(schema, instructions, context, **kwargs)
        with self.assertRaisesRegex(RuntimeError, "Synthetic interruption"):
            self.execute(saved, interrupted)
        before = list(self.models_called)
        self.settings.ollama_fast_model = "different-default:4b"
        result = self.execute(saved)
        self.assertEqual(self.models_called[:len(before)], before)
        self.assertEqual([name for name, _ in self.models_called].count("AnalysisPlan"), 1)
        self.assertEqual([name for name, _ in self.models_called].count("QueryPlan"), 1)
        self.assertEqual(result["model"], "quality:9b")
        self.assertEqual(result["local_routing"]["planner_model"], "fast:4b")

    def test_http_snapshot_is_server_owned_and_config_exposes_modes(self):
        app = create_app(self.settings)
        with TestClient(app) as client, patch.object(Providers, "ollama_models", return_value=self.inventory):
            config = client.get("/api/config").json()
            self.assertEqual(set(config["local_profiles"]), {"fast", "quality", "auto"})
            snapshot = {"profile": "fast", "fast_model": "attacker:cloud", "quality_model": "attacker:cloud"}
            response = client.post("/api/analyses", json={**self.request, "local_profile_snapshot": snapshot})
            self.assertEqual(response.status_code, 202, response.text)
            run = client.get("/api/analyses/" + response.json()["id"]).json()
            self.assertEqual(run["request"]["local_profile_snapshot"]["fast_model"], "fast:4b")
            self.assertEqual(run["request"]["local_profile_snapshot"]["model_digests"], {"fast:4b": "fast-version-1"})
            denied = client.post("/api/analyses", json={**self.request, "decision_mode": "openai_decisions"})
            self.assertEqual(denied.status_code, 400)

    def test_catalog_marks_cloud_embeddings_as_unavailable(self):
        self.settings.retrieval_mode = "hybrid"
        self.settings.embedding_provider = "cloud"
        catalog = profile_catalog(self.settings)
        self.assertTrue(all(not profile["available"] for profile in catalog.values()))
        self.assertTrue(all("cloud embeddings" in profile["unavailable_reason"] for profile in catalog.values()))

    def test_evaluation_routes_profile_and_rejects_cloud_judge(self):
        self.db.put_record("evaluation_cases", "case_profile", {"id": "case_profile", "name": "Inventory policy", "split": "development",
            "question": self.request["question"], "document_ids": self.request["document_ids"], "expected_route": "rag"})
        with patch.object(Providers, "ollama_models", return_value=self.inventory), patch.object(Providers, "structured", side_effect=self.respond):
            result = run_evaluation(self.db, self.settings, {"agent_mode": "ollama", "decision_mode": "llm", "local_profile": "quality"})
            self.assertEqual(result["completion_rate"], 1)
            self.assertEqual(result["cases"][0]["local_routing"]["analysis_model"], "quality:9b")
            with self.assertRaisesRegex(ProviderError, "cloud evaluation judge"):
                run_evaluation(self.db, self.settings, {"agent_mode": "ollama", "decision_mode": "llm", "local_profile": "fast", "judge_mode": "live"})
            with self.assertRaisesRegex(ProviderError, "cloud models"):
                run_evaluation(self.db, self.settings, {"agent_mode": "ollama", "decision_mode": "llm", "local_profile": "fast",
                    "judge_mode": "ollama", "judge_model": "judge:cloud"})

    def test_schedule_persists_profile_and_refreshes_defaults_on_new_execution(self):
        from app.extensions import enqueue_due_schedules, execute_job
        app = create_app(self.settings)
        with TestClient(app) as client, patch.object(Providers, "ollama_models", return_value=self.inventory):
            response = client.post("/api/schedules", json={"name": "Inventory review", "interval_minutes": 15, "request": self.request})
            self.assertEqual(response.status_code, 201, response.text)
            schedule = response.json()
            self.assertEqual(schedule["request"]["local_profile"], "fast")
            self.settings.ollama_fast_model = "replacement:4b"
            self.inventory["models"].append("replacement:4b")
            self.inventory["details"]["replacement:4b"] = {"digest": "replacement-version"}
            schedule["next_run_at"] = "2000-01-01T00:00:00+00:00"
            self.db.put_record("schedules", schedule["id"], schedule)
            enqueue_due_schedules(self.db, self.settings)
            task = next(task for task in self.db.tasks() if task["kind"] == "scheduled_analysis")
            execute_job(task["kind"], task["payload"], self.db, self.settings)
            runs = self.db.runs()
            self.assertEqual(len(runs), 1)
            run = self.db.run(runs[0]["id"])
            self.assertEqual(run["request"]["local_profile"], "fast")
            self.assertEqual(run["request"]["model"], "replacement:4b")


if __name__ == "__main__":
    unittest.main()
