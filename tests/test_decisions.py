"""Official Decisions contract, migration, privacy and cost separation."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.costing import apply_cost
from app.engine import Engine
from app.extensions import EvaluationInput, execute_job
from app.main import AnalysisInput, create_app
from app.providers import ENGLISH_OUTPUT_INSTRUCTION, ProviderError, Providers, QueryPlan


CHOICES = {"review_restock": "Review the stock replenishment plan.", "gather_data": "Gather missing evidence."}


def decision_response():
    return {"model": "gpt-6-luna", "answers": [{"type": "choice", "name": "next_action",
            "choice": "review_restock", "probabilities": [
                {"value": "review_restock", "probability": .75},
                {"value": "gather_data", "probability": .25}], "confidence": .6}],
            "usage": {"input_tokens": 160, "output_tokens": 0, "total_tokens": 160}}


class DecisionsProviderTests(unittest.TestCase):
    def setUp(self):
        self.providers = Providers(Settings(openai_key="test-private-key", openai_model="",
            cloud_base_url="https://unrelated-compatible-provider.example/v1"))

    def test_official_http_contract_uses_shared_key_and_separate_model(self):
        response = Mock(); response.json.return_value = decision_response()
        evidence = {"question": "What should we do?", "evidence": [{"id": "source-1", "text": "Stock is low."}]}
        with patch("app.providers.httpx.post", return_value=response) as post:
            result = self.providers.openai_decisions(evidence, CHOICES, request_timeout=3)
        args = post.call_args
        self.assertEqual(args.args[0], "https://api.openai.com/v1/decisions")
        self.assertEqual(args.kwargs["headers"], {"Authorization": "Bearer test-private-key"})
        self.assertEqual(args.kwargs["timeout"], 3)
        self.assertFalse(args.kwargs["follow_redirects"])
        payload = args.kwargs["json"]
        self.assertEqual(set(payload), {"model", "input", "questions"})
        self.assertEqual(payload["model"], "gpt-6-luna")
        self.assertEqual(json.loads(payload["input"]), evidence)
        self.assertEqual(len(payload["questions"]), 1)
        question = payload["questions"][0]
        self.assertEqual(question["name"], "next_action")
        self.assertEqual(question["type"], "choice")
        self.assertEqual(question["choices"], [{"value": k, "description": v} for k, v in CHOICES.items()])
        self.assertEqual(result["provider"], "openai_decisions")
        self.assertEqual(result["probabilities"], {"review_restock": .75, "gather_data": .25})
        self.assertEqual(result["confidence"], .6)
        self.assertEqual(result["usage"]["request_count"], 1)
        self.assertEqual(result["usage"]["input_tokens"], 160)

    def test_refused_missing_ambiguous_and_invalid_distributions_fail_closed(self):
        cases = []
        for answers in ([], {}, [{"type": "refusal", "name": "next_action"}],
                        decision_response()["answers"] * 2):
            cases.append({**decision_response(), "answers": answers})
        for key, value in (("name", "another_question"), ("type", "score"), ("choice", "unknown"),
                           ("choice", "gather_data"), ("confidence", True), ("confidence", float("nan")),
                           ("confidence", 1.1), ("probabilities", {}), ("probabilities", [])):
            data = decision_response(); data["answers"][0][key] = value; cases.append(data)
        for value in (True, -.1, 1.1, float("inf"), float("nan"), 10**400, "0.75", None, .1):
            data = decision_response(); data["answers"][0]["probabilities"][0]["probability"] = value; cases.append(data)
        for values in ([{"value": "review_restock", "probability": 1}],
                       [{"value": "review_restock", "probability": .75}, {"value": "unknown", "probability": .25}],
                       [{"value": "review_restock", "probability": .5}, {"value": "review_restock", "probability": .5}],
                       [{"value": ["invalid"], "probability": .5}], [{"value": "review_restock"}], [None]):
            data = decision_response(); data["answers"][0]["probabilities"] = values; cases.append(data)
        cases.extend([{}, None, {**decision_response(), "usage": None}, {**decision_response(), "model": None}])
        for data in cases:
            with self.subTest(data=data):
                response = Mock(); response.json.return_value = data
                with patch("app.providers.httpx.post", return_value=response), self.assertRaises(ProviderError) as caught:
                    self.providers.openai_decisions({}, CHOICES)
                self.assertIn("No substitute probability", str(caught.exception))

    def test_http_error_does_not_expose_response_content_key_or_evidence(self):
        request = httpx.Request("POST", "https://api.openai.com/v1/decisions")
        for status in (302, 401, 403, 429, 500):
            response = httpx.Response(status, request=request, text="test-private-key secret source content")
            with self.subTest(status=status), patch("app.providers.httpx.post", return_value=response):
                with self.assertRaises(ProviderError) as caught:
                    self.providers.openai_decisions({"secret": "source content"}, CHOICES)
                self.assertNotIn("test-private-key", str(caught.exception))
                self.assertNotIn("source content", str(caught.exception))

    def test_invalid_configuration_choices_and_expired_budget_never_call_network(self):
        with patch("app.providers.httpx.post", side_effect=AssertionError("Network must not be used")):
            for criteria in (None, {}, {"a": "one"}, {"a": "", "b": "two"}, {"a": {}, "b": "two"},
                             {str(i): "action" for i in range(65)}):
                with self.subTest(criteria=criteria), self.assertRaises(ProviderError):
                    self.providers.openai_decisions({}, criteria)
            for timeout in (0, -1):
                with self.assertRaisesRegex(ProviderError, "time budget"):
                    self.providers.openai_decisions({}, CHOICES, request_timeout=timeout)
            for settings in (Settings(), Settings(openai_key="key", openai_decision_model=""), Settings(local_only=True, openai_key="key")):
                with self.assertRaises(ProviderError):
                    Providers(settings).openai_decisions({}, CHOICES)

    def test_cloud_and_ollama_receive_english_instruction_without_changing_evidence(self):
        evidence = {"source": "\u539f\u59cb\u8bc1\u636e", "id": "source-1"}
        manager = Mock()
        manager.__enter__ = Mock(return_value=manager); manager.__exit__ = Mock(return_value=False)
        manager.responses.parse.return_value = Mock(output_parsed=QueryPlan(sql="SELECT 1", search_query=""), usage=None)
        with patch("openai.OpenAI", return_value=manager):
            self.providers.structured(QueryPlan, "Plan a read-only query.", evidence, model="test")
        args = manager.responses.parse.call_args.kwargs
        self.assertIn(ENGLISH_OUTPUT_INSTRUCTION, args["instructions"])
        self.assertEqual(json.loads(args["input"]), evidence)
        response = Mock(); response.json.return_value = {"done": True, "message": {"content": '{"sql":"SELECT 1","search_query":""}'}}
        with patch("app.providers.httpx.post", return_value=response) as post:
            self.providers.structured(QueryPlan, "Plan a read-only query.", evidence, provider="ollama", model="test")
        messages = post.call_args.kwargs["json"]["messages"]
        self.assertIn(ENGLISH_OUTPUT_INSTRUCTION, messages[0]["content"])
        self.assertEqual(json.loads(messages[1]["content"]), evidence)


class DecisionsMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(db_path=Path(self.tmp.name)/"test.db", openai_key="test-key", embedded_worker=False)
        self.client = TestClient(create_app(self.settings)); self.client.__enter__()
        self.db = self.client.app.state.db
        self.request = {"question": "Should we replenish stock?", "dataset_id": "demo_pricing", "agent_mode": "local"}

    def tearDown(self):
        self.client.__exit__(None, None, None); self.tmp.cleanup()

    def test_configuration_and_submission_need_no_separate_key_or_chat_model(self):
        config = self.client.get("/api/config").json()
        self.assertTrue(config["openai_decisions_available"])
        self.assertFalse(config["live_agents_available"])
        self.assertNotIn("jev_available", config)
        self.assertNotIn("test-key", json.dumps(config))
        for mode in ("openai_decisions", "jev"):
            response = self.client.post("/api/analyses", json={**self.request, "decision_mode": mode})
            self.assertEqual(response.status_code, 202, response.text)
            saved = self.db.run(response.json()["id"])
            self.assertEqual(saved["request"]["decision_mode"], "openai_decisions")
        self.settings.local_only = True
        self.assertFalse(self.client.get("/api/config").json()["openai_decisions_available"])
        for mode in ("openai_decisions", "jev"):
            self.assertEqual(self.client.post("/api/analyses", json={**self.request, "decision_mode": mode}).status_code, 400)

    def test_new_and_persisted_schedules_migrate_legacy_requests(self):
        response = self.client.post("/api/schedules", json={"name": "Stock review", "interval_minutes": 60,
            "request": {**self.request, "decision_mode": "jev"}})
        self.assertEqual(response.status_code, 201, response.text)
        schedule = response.json()
        self.assertEqual(schedule["request"]["decision_mode"], "openai_decisions")
        schedule["request"]["decision_mode"] = "jev"
        self.db.put_record("schedules", schedule["id"], schedule)
        result = execute_job("scheduled_analysis", {"schedule_id": schedule["id"], "occurrence_id": "migration-check"}, self.db, self.settings)
        self.assertEqual(self.db.run(result["run_id"])["request"]["decision_mode"], "openai_decisions")
        self.assertEqual(EvaluationInput.model_validate({"decision_mode": "jev"}).decision_mode, "openai_decisions")

    def test_engine_rerun_migrates_without_mutating_historical_approved_result(self):
        engine = Engine(self.db, self.settings)
        old_request = {**self.request, "decision_mode": "jev"}
        historical = engine.analyze({**self.request, "decision_mode": "baseline"})
        historical["decision_mode"] = "jev"; historical["decision"]["provider"] = "jev"
        from app.workflow import digest
        historical.pop("result_hash"); historical["result_hash"] = digest(historical)
        run_id = self.db.create_run(old_request)
        self.db.finish_run(run_id, historical)
        self.db.review(run_id, "approved", "Historical reviewer", "Reviewed before migration")
        before = deepcopy(self.db.approved_run(run_id))
        supplied = [{"id": "review_restock", "title": "Review restocking", "description": CHOICES["review_restock"]}]
        response = Mock(); response.json.return_value = decision_response()
        with patch("app.providers.httpx.post", return_value=response):
            result = engine.analyze({**old_request, "candidate_actions": supplied})
        self.assertEqual(result["decision_mode"], "openai_decisions")
        self.assertEqual(result["decision"]["provider"], "openai_decisions")
        self.assertEqual(set(result["decision"]["actions"]), set(CHOICES))
        self.assertEqual(old_request["decision_mode"], "jev")
        self.assertEqual(self.db.approved_run(run_id), before)
        self.assertIsNone(result["measurement"]["estimated_cost"])

    def test_rejected_revision_normalizes_request_and_keeps_reviewer_notes(self):
        engine = Engine(self.db, self.settings)
        run_id = self.db.create_run({**self.request, "decision_mode": "jev"})
        self.db.finish_run(run_id, engine.analyze(self.request))
        self.db.review(run_id, "rejected", "Reviewer", "Check the replenishment evidence")
        response = self.client.post("/api/analyses", json={**self.request, "decision_mode": "jev", "parent_run_id": run_id})
        self.assertEqual(response.status_code, 202, response.text)
        request = self.db.run(response.json()["id"])["request"]
        self.assertEqual(request["decision_mode"], "openai_decisions")
        self.assertEqual(request["reviewer_notes"], "Check the replenishment evidence")


class DecisionsPricingTests(unittest.TestCase):
    def result(self):
        return {"agent_mode": "local", "decision_mode": "openai_decisions", "measurement": {"provider_usage": [
            {"provider": "openai_decisions", "model": "gpt-6-luna", "input_tokens": 1000, "output_tokens": 0}]}}

    def test_responses_rates_never_price_decisions_even_when_models_match(self):
        settings = Settings(openai_model="gpt-6-luna", openai_input_usd_per_million=2,
                            openai_output_usd_per_million=8, price_version="responses-test")
        result = apply_cost(self.result(), settings)
        self.assertIsNone(result["measurement"]["estimated_cost"])
        self.assertFalse(result["measurement"]["cost"]["complete"])

    def test_separate_versioned_rate_prices_decisions_input_and_preserves_unknowns(self):
        settings = Settings(openai_decisions_input_usd_per_million=.1, openai_decisions_price_version="decisions-test")
        result = apply_cost(self.result(), settings)
        self.assertEqual(result["measurement"]["estimated_cost"], .0001)
        self.assertEqual(result["measurement"]["cost"]["components"][0]["price_version"], "decisions-test")
        for change in ({"input_tokens": True}, {"input_tokens": -1}, {"model": "another-model"}, {"output_tokens": None}):
            result = self.result(); result["measurement"]["provider_usage"][0].update(change)
            self.assertIsNone(apply_cost(result, settings)["measurement"]["estimated_cost"])
        for rate in (-1, float("nan"), float("inf"), True):
            settings.openai_decisions_input_usd_per_million = rate
            self.assertIsNone(apply_cost(self.result(), settings)["measurement"]["estimated_cost"])


if __name__ == "__main__":
    unittest.main()
