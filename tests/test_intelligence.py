"""Behavioral contracts for bounded graphs, scoped retrieval and model boundaries.

Set QDRANT_TEST_URL, OLLAMA_TEST_URL and OLLAMA_EMBEDDING_MODEL to additionally
exercise actual local semantic retrieval; these tests never substitute fake vectors.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.config import Settings
from app.database import Database
from app.engine import Engine
from app.providers import Providers, ProviderError, QueryPlan, DecisionBrief, Rationale
from app.retrieval import retrieve, index_documents
from app.seed import seed
from app.vector_store import VectorIndex, point_id, content_hash
from app.workflow import AnalysisPlan, ResearchFindings, EvidenceAudit, CandidateSet, ActionSelection


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(db_path=Path(self.tmp.name)/"test.sqlite3", orchestration="langgraph", max_model_calls=24)
        self.db = Database(self.settings.db_path)
        self.db.initialize()
        seed(self.db)
        self.doc = self.db.add_document("Support policy", "Customers need fast support and reliable onboarding.", "Test policy")
        self.engine = Engine(self.db, self.settings)
        self.request = {"question": "How should we improve customer retention?", "document_ids": [self.doc["id"]],
                        "agent_mode": "live", "decision_mode": "llm"}
        self.calls = []
        self.route = "rag"

    def tearDown(self):
        self.tmp.cleanup()

    def respond(self, schema, instructions, context, **kwargs):
        self.calls.append((schema, deepcopy(context)))
        if schema is AnalysisPlan:
            value = {"objective": "Improve retention", "tasks": [{"specialist": "customer", "route": self.route,
                    "objective": "Inspect customer evidence", "search_query": "support onboarding churn"}], "missing_information": []}
        elif schema is QueryPlan:
            value = {"sql": "SELECT SUM(customers) AS customers FROM data_demo_retention", "search_query": "support"}
        elif schema is ResearchFindings:
            value = {"summary": "Inspect the documented evidence.", "claims": [{"text": "The selected source contains customer evidence.",
                    "evidence_ids": [context["evidence"][0]["id"]]}], "limitations": [], "next_tool": "none", "followup_sql": "", "followup_search": ""}
        elif schema is EvidenceAudit:
            value = {"unsupported_claims": [], "conflicts": [], "missing_information": [], "explanation": "The claim is supported."}
        elif schema is DecisionBrief:
            value = {"recommendation": "Pilot faster support", "explanation": "The evidence supports reviewing customer support.",
                     "alternatives": ["Measure onboarding time"], "evidence_ids": [context["evidence"][0]["id"]]}
        elif schema is CandidateSet:
            value = {"actions": [{"id": "support_pilot", "title": "Support pilot", "description": "Test faster support."},
                                 {"id": "onboarding_pilot", "title": "Onboarding pilot", "description": "Test simpler onboarding."}]}
        elif schema is ActionSelection:
            value = {"choice": "support_pilot", "explanation": "The source identifies support needs.", "evidence_ids": [context["evidence"][0]["id"]]}
        elif schema is Rationale:
            value = {"explanation": "Compare the selected pilot with the evidence.", "evidence_ids": [context["state"]["evidence"][0]["id"]]}
        else:
            raise AssertionError(schema)
        return value, {"input_tokens": 10, "output_tokens": 20}

    def run_graph(self, **overrides):
        with patch.object(self.engine.providers, "structured", side_effect=self.respond):
            return self.engine.analyze({**self.request, **overrides})

    def test_document_route_uses_no_sql_and_persists_provenance(self):
        result = self.run_graph()
        self.assertEqual(result["orchestration"], "langgraph")
        self.assertEqual([a["id"] for a in result["agents"]], ["customer"])
        self.assertEqual([t["tool"] for t in result["tool_trace"]], ["retrieval"])
        self.assertTrue(all(e["source_hash"] and e["content"] for e in result["evidence"]))
        self.assertTrue(self.db.list_records("graph_checkpoint"))
        self.assertEqual(result["verification"]["status"], "review_required")

    def test_sql_route_repairs_query_once_and_logs_failure(self):
        self.route = "sql"
        original = self.respond
        count = 0
        def provider(schema, instructions, context, **kwargs):
            nonlocal count
            if schema is QueryPlan:
                count += 1
                if count == 1:
                    return {"sql": "SELECT missing_column FROM data_demo_retention", "search_query": ""}, {}
            return original(schema, instructions, context, **kwargs)
        with patch.object(self.engine.providers, "structured", side_effect=provider):
            result = self.engine.analyze({**self.request, "dataset_id": "demo_retention", "document_ids": []})
        self.assertEqual(count, 2)
        self.assertEqual([t["status"] for t in result["tool_trace"]], ["completed", "failed", "completed"])
        self.assertNotIn("retrieval", [t["tool"] for t in result["tool_trace"]])

    def test_bounded_followup_adds_evidence_and_stops(self):
        original = self.respond
        def provider(schema, instructions, context, **kwargs):
            result, usage = original(schema, instructions, context, **kwargs)
            if schema is ResearchFindings:
                result.update(next_tool="rag", followup_search="reliable onboarding")
            return result, usage
        with patch.object(self.engine.providers, "structured", side_effect=provider):
            result = self.engine.analyze({**self.request, "max_steps": 2})
        self.assertEqual(result["agents"][0]["research_rounds"], 2)
        self.assertEqual(result["measurement"]["tool_calls"], 2)
        self.assertTrue(result["missing_information"])

    def test_failed_sql_repair_abstains_when_no_other_evidence_exists(self):
        self.route = "sql"
        dataset = self.db.import_csv("Generic accounts", "name,value\nOne,1\nTwo,2\n")
        original = self.respond
        def provider(schema, instructions, context, **kwargs):
            if schema is QueryPlan:
                return {"sql": "SELECT nonexistent FROM "+dataset["table_name"], "search_query": ""}, {}
            return original(schema, instructions, context, **kwargs)
        with patch.object(self.engine.providers, "structured", side_effect=provider):
            result = self.engine.analyze({**self.request, "dataset_id": dataset["id"], "document_ids": []})
        self.assertEqual(result["decision"]["choice"], "gather_data")
        self.assertEqual([t["status"] for t in result["tool_trace"]], ["failed", "failed"])
        self.assertTrue(any("after one repair" in gap for gap in result["missing_information"]))

    def test_specialist_may_abstain_without_fabricating_a_claim(self):
        original = self.respond
        def provider(schema, instructions, context, **kwargs):
            result, usage = original(schema, instructions, context, **kwargs)
            if schema is ResearchFindings:
                result["claims"] = []
                result["limitations"] = ["The source does not answer the question."]
            return result, usage
        with patch.object(self.engine.providers, "structured", side_effect=provider):
            result = self.engine.analyze(self.request)
        self.assertEqual(result["decision"]["choice"], "gather_data")
        self.assertEqual(result["agents"][0]["claims"], [])

    def test_completed_graph_replays_without_provider_calls(self):
        result = self.run_graph(_run_id="resume-test")
        with patch.object(self.engine.providers, "structured", side_effect=AssertionError("Provider was called during completed replay")):
            replay = self.engine.analyze({**self.request, "_run_id": "resume-test"})
        self.assertEqual(replay, result)

    def test_interrupted_graph_resumes_completed_nodes_and_cached_responses(self):
        original = self.respond
        def interrupted(schema, instructions, context, **kwargs):
            if schema is EvidenceAudit:
                raise ProviderError("temporary service failure")
            return original(schema, instructions, context, **kwargs)
        with patch.object(self.engine.providers, "structured", side_effect=interrupted), self.assertRaises(ProviderError):
            self.engine.analyze({**self.request, "_run_id": "interrupted-test"})
        before = len(self.calls)
        result = self.run_graph(_run_id="interrupted-test")
        resumed_schemas = [schema for schema, _ in self.calls[before:]]
        self.assertEqual(resumed_schemas, [EvidenceAudit, DecisionBrief])
        self.assertEqual(result["measurement"]["tool_calls"], 1)

    def test_changed_request_cannot_reuse_checkpoint(self):
        self.run_graph(_run_id="immutable-test")
        with self.assertRaisesRegex(ValueError, "changed request"):
            self.engine.analyze({**self.request, "question": "A different business question", "_run_id": "immutable-test"})

    def test_unsupported_claim_gates_decision(self):
        original = self.respond
        def unsupported(schema, instructions, context, **kwargs):
            result, usage = original(schema, instructions, context, **kwargs)
            if schema is EvidenceAudit:
                result["unsupported_claims"] = [context["claims"][0]["text"]]
            return result, usage
        with patch.object(self.engine.providers, "structured", side_effect=unsupported):
            result = self.engine.analyze(self.request)
        self.assertEqual(result["decision"]["provider"], "evidence_gate")
        self.assertEqual(result["decision"]["choice"], "gather_data")
        self.assertNotIn(DecisionBrief, [s for s, _ in self.calls])

    def test_verifier_cannot_invent_a_claim_to_reject(self):
        original = self.respond
        attempts = 0
        def provider(schema, instructions, context, **kwargs):
            nonlocal attempts
            result, usage = original(schema, instructions, context, **kwargs)
            if schema is EvidenceAudit:
                attempts += 1
                if attempts == 1:
                    result["unsupported_claims"] = ["This claim never appeared in the findings."]
            return result, usage
        with patch.object(self.engine.providers, "structured", side_effect=provider):
            result = self.engine.analyze(self.request)
        self.assertEqual(attempts, 2)
        self.assertEqual(result["verification"]["status"], "review_required")
        self.assertEqual(result["decision"]["choice"], "model_recommendation")

    def test_invalid_verifier_after_repair_is_inconclusive_not_approved(self):
        original = self.respond
        def provider(schema, instructions, context, **kwargs):
            result, usage = original(schema, instructions, context, **kwargs)
            if schema is EvidenceAudit:
                result["unsupported_claims"] = ["This claim never appeared in the findings."]
            return result, usage
        with patch.object(self.engine.providers, "structured", side_effect=provider):
            result = self.engine.analyze(self.request)
        self.assertTrue(result["verification"]["audit_errors"])
        self.assertEqual(result["decision"]["choice"], "gather_data")

    def test_custom_candidates_reach_decisions_and_values_are_preserved(self):
        actions = [{"id": "support_pilot", "title": "Support pilot", "description": "Test a smaller support queue."}]
        decision = {"provider": "openai_decisions", "model": "test-contract", "choice": "support_pilot",
                    "probabilities": {"support_pilot": .7, "gather_data": .3}, "confidence": .2, "usage": {"input_tokens": 5}}
        with patch.object(self.engine.providers, "openai_decisions", return_value=decision) as decisions:
            result = self.run_graph(decision_mode="openai_decisions", candidate_actions=actions)
        self.assertEqual(set(decisions.call_args.args[1]), {"support_pilot", "gather_data"})
        self.assertEqual(result["decision"]["probabilities"], decision["probabilities"])
        self.assertEqual(result["decision"]["confidence"], .2)
        self.assertGreater(decisions.call_args.kwargs["request_timeout"], 0)
        self.assertLessEqual(decisions.call_args.kwargs["request_timeout"], self.settings.analysis_timeout)
        self.assertEqual(result["measurement"]["provider_usage"][-2]["provider"], "openai_decisions")

    def test_decisions_cache_survives_rationale_failure_without_second_paid_decision(self):
        actions = [{"id": "support_pilot", "title": "Support pilot", "description": "Test a smaller support queue."}]
        decision = {"provider": "openai_decisions", "model": "gpt-6-luna", "choice": "support_pilot",
                    "probabilities": {"support_pilot": .7, "gather_data": .3}, "confidence": .2,
                    "usage": {"input_tokens": 5, "output_tokens": 0}}
        request = {**self.request, "decision_mode": "openai_decisions", "candidate_actions": actions, "_run_id": "decisions-resume"}
        def interrupted(schema, instructions, context, **kwargs):
            if schema is Rationale:
                raise ProviderError("Temporary explanation failure")
            return self.respond(schema, instructions, context, **kwargs)
        with patch.object(self.engine.providers, "structured", side_effect=interrupted), \
             patch.object(self.engine.providers, "openai_decisions", return_value=decision) as select:
            with self.assertRaisesRegex(ProviderError, "explanation failure"):
                self.engine.analyze(request)
        self.assertEqual(select.call_count, 1)
        with patch.object(self.engine.providers, "structured", side_effect=self.respond), \
             patch.object(self.engine.providers, "openai_decisions", side_effect=AssertionError("Paid decision repeated")):
            result = self.engine.analyze(request)
        trace = next(t for t in result["tool_trace"] if t["tool"] == "openai_decisions")
        self.assertTrue(trace["input"]["replayed"])
        self.assertEqual(result["decision"]["choice"], "support_pilot")

    def test_decisions_call_respects_graph_model_budget_before_network(self):
        self.settings.max_model_calls = 3
        actions = [{"id": "support_pilot", "title": "Support pilot", "description": "Test support."}]
        with patch.object(self.engine.providers, "openai_decisions", side_effect=AssertionError("Budget exceeded")):
            with self.assertRaisesRegex(ProviderError, "model-call budget"):
                self.run_graph(decision_mode="openai_decisions", candidate_actions=actions)

    def test_invalid_planner_route_fails_without_querying(self):
        self.route = "sql"
        with self.assertRaisesRegex(ValueError, "needs a data table"):
            self.run_graph()

    def test_model_call_budget_is_enforced(self):
        self.settings.max_model_calls = 2
        with self.assertRaisesRegex(ProviderError, "budget exhausted"):
            self.run_graph()
        self.assertEqual(len(self.calls), 2)

    def test_business_dictionary_is_passed_to_planner(self):
        self.route = "sql"
        self.db.put_record("dictionaries", "demo_retention", {"currency": "SGD", "period_days": 30})
        self.run_graph(dataset_id="demo_retention", document_ids=[])
        plan = self.calls[0][1]
        self.assertEqual(plan["tables"][0]["business_dictionary"]["currency"], "SGD")

    def test_queued_dictionary_snapshot_overrides_later_edits(self):
        self.route = "sql"
        self.db.put_record("dictionaries", "demo_retention", {"currency": "USD"})
        self.run_graph(dataset_id="demo_retention", document_ids=[],
                       _source_dictionaries={"demo_retention": {"currency": "SGD"}})
        self.assertEqual(self.calls[0][1]["tables"][0]["business_dictionary"]["currency"], "SGD")

    def test_sql_prompt_uses_actual_values_and_computed_rates(self):
        self.route = "sql"
        result = self.run_graph(dataset_id="demo_retention", document_ids=[])
        context = next(c for schema, c in self.calls if schema is QueryPlan)
        evidence = context["existing_evidence"][0]
        self.assertEqual(evidence["computed_metrics"]["churn_rate"], .19)
        self.assertEqual({r["period"] for r in evidence["rows"]}, {"current", "previous"})
        self.assertEqual(result["evidence"][0]["computed_metrics"], evidence["computed_metrics"])

    def test_generated_sql_never_replaces_document_search_or_mutates_plan(self):
        self.route = "hybrid"
        original = self.respond
        def provider(schema, instructions, context, **kwargs):
            result, usage = original(schema, instructions, context, **kwargs)
            if schema is QueryPlan:
                result["search_query"] = result["sql"]
            return result, usage
        with patch.object(self.engine.providers, "structured", side_effect=provider):
            result = self.engine.analyze({**self.request, "dataset_id": "demo_retention", "document_ids": []})
        self.assertEqual(result["plan"]["tasks"][0]["search_query"], "support onboarding churn")
        search = next(t for t in result["tool_trace"] if t["tool"] == "retrieval")
        self.assertEqual(search["input"]["query"], "support onboarding churn")

    def test_specialist_timing_includes_model_time(self):
        import time
        original = self.respond
        def slow(schema, instructions, context, **kwargs):
            if schema is ResearchFindings:
                time.sleep(.025)
            return original(schema, instructions, context, **kwargs)
        with patch.object(self.engine.providers, "structured", side_effect=slow):
            result = self.engine.analyze(self.request)
        agent = result["agents"][0]
        self.assertGreaterEqual(agent["elapsed_ms"], agent["tool_elapsed_ms"]+20)

    def test_duplicate_planner_roles_are_merged_with_warning(self):
        original = self.respond
        def duplicated(schema, instructions, context, **kwargs):
            result, usage = original(schema, instructions, context, **kwargs)
            if schema is AnalysisPlan:
                result["tasks"].append({**result["tasks"][0], "objective": "Check onboarding."})
            return result, usage
        with patch.object(self.engine.providers, "structured", side_effect=duplicated):
            result = self.engine.analyze(self.request)
        self.assertEqual(len(result["plan"]["tasks"]), 1)
        self.assertTrue(any("duplicate roles" in warning for warning in result["warnings"]))

    def test_additional_tables_are_explicitly_scoped(self):
        self.route = "sql"
        original = self.respond
        query = "SELECT COUNT(*) AS n FROM data_demo_retention a JOIN data_demo_pricing b ON a.period=b.period"
        def join(schema, instructions, context, **kwargs):
            if schema is QueryPlan:
                return {"sql": query, "search_query": ""}, {}
            return original(schema, instructions, context, **kwargs)
        with patch.object(self.engine.providers, "structured", side_effect=join):
            result = self.engine.analyze({**self.request, "dataset_id": "demo_retention", "dataset_ids": ["demo_pricing"], "document_ids": []})
        self.assertEqual(len(result["datasets"]), 2)
        self.assertTrue(any(e.get("sql") == query for e in result["evidence"]))


class ProviderBoundaryTests(unittest.TestCase):
    def test_official_cloud_adapter_respects_model_and_rejects_refusals(self):
        providers = Providers(Settings(openai_key="test", openai_model="default-model"))
        response = Mock(output_parsed=QueryPlan(sql="SELECT 1", search_query="one"), usage=None)
        client = Mock(); client.responses.parse.return_value = response
        manager = Mock(); manager.__enter__ = Mock(return_value=client); manager.__exit__ = Mock(return_value=False)
        # Providers uses its client object inside the with block, just as the SDK does.
        manager.responses = client.responses
        with patch("openai.OpenAI", return_value=manager):
            value, _ = providers.structured(QueryPlan, "Read only", {}, model="chosen-model")
        self.assertEqual(value["sql"], "SELECT 1")
        self.assertEqual(client.responses.parse.call_args.kwargs["model"], "chosen-model")
        self.assertFalse(client.responses.parse.call_args.kwargs["store"])
        response.output_parsed = None
        with patch("openai.OpenAI", return_value=manager), self.assertRaises(ProviderError):
            providers.structured(QueryPlan, "Read only", {})
        response.output_parsed = QueryPlan(sql="SELECT 1", search_query="one")
        response.status = "incomplete"
        with patch("openai.OpenAI", return_value=manager), self.assertRaisesRegex(ProviderError, "incomplete"):
            providers.structured(QueryPlan, "Read only", {})

    def test_ollama_schema_retry_respects_remaining_request_budget(self):
        providers = Providers(Settings(ollama_model="test"))
        response = Mock(); response.json.return_value = {"done": True, "message": {"content": "{}"},
                                                       "prompt_eval_count": 3, "eval_count": 2}
        with patch.object(providers, "ollama_model_info", return_value={"capabilities": ["completion"], "thinking": {}}), patch("app.providers.httpx.post", return_value=response) as post, self.assertRaises(ProviderError):
            providers.structured(QueryPlan, "test", {}, provider="ollama", max_requests=1)
        self.assertEqual(post.call_count, 1)
        good = Mock(); good.json.return_value = {"done": True, "message": {"content": '{"sql":"SELECT 1","search_query":""}'},
                                               "prompt_eval_count": 5, "eval_count": 4}
        with patch.object(providers, "ollama_model_info", return_value={"capabilities": ["completion"], "thinking": {}}), patch("app.providers.httpx.post", side_effect=[response, good]) as post:
            _, usage = providers.structured(QueryPlan, "test", {}, provider="ollama", max_requests=2)
        self.assertEqual(post.call_count, 2)
        self.assertFalse(post.call_args.kwargs["trust_env"])
        self.assertEqual(usage["request_count"], 2)
        self.assertEqual(usage["input_tokens"], 8)
        self.assertEqual(usage["output_tokens"], 6)

    def test_local_only_blocks_all_cloud_paths_before_network(self):
        settings = Settings(local_only=True, openai_key="test", openai_model="test",
                            embedding_provider="cloud", embedding_model="test")
        providers = Providers(settings)
        with patch("app.providers.httpx.post", side_effect=AssertionError("network called")):
            for operation in [lambda: providers.structured(QueryPlan, "test", {}),
                              lambda: providers.openai_decisions({}, {"a": "a", "b": "b"}), lambda: providers.embed(["text"])]:
                with self.assertRaisesRegex(ProviderError, "Local-only"):
                    operation()

    def test_local_only_rejects_cloud_ollama_models_and_remote_daemon(self):
        providers = Providers(Settings(local_only=True, ollama_model="test:cloud"))
        with self.assertRaisesRegex(ProviderError, "cloud models"):
            providers.structured(QueryPlan, "test", {}, provider="ollama")
        providers = Providers(Settings(local_only=True, ollama_base_url="https://example.org", ollama_model="test"))
        with self.assertRaisesRegex(ProviderError, "private-network"):
            providers.structured(QueryPlan, "test", {}, provider="ollama")

    def test_embedding_validates_dimensions_count_and_finiteness(self):
        providers = Providers(Settings(embedding_provider="ollama", embedding_model="test"))
        for vectors in [[], [[1, 2], [1]], [[float("nan")]], [[True]], [[0, 0]]]:
            response = Mock(); response.json.return_value = {"embeddings": vectors}
            with patch("app.providers.httpx.post", return_value=response), self.assertRaises(ProviderError):
                providers.embed(["text"])

    def test_cloud_embedding_preserves_input_order_and_rejects_duplicate_indexes(self):
        providers = Providers(Settings(openai_key="test", embedding_provider="cloud", embedding_model="test"))
        response = Mock(); response.json.return_value = {"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]}
        with patch("app.providers.httpx.post", return_value=response):
            self.assertEqual(providers.embed(["a", "b"]), [[1, 0], [0, 1]])
        response.json.return_value["data"][0]["index"] = 0
        with patch("app.providers.httpx.post", return_value=response), self.assertRaises(ProviderError):
            providers.embed(["a", "b"])


class VectorFilterTests(unittest.TestCase):
    def test_qdrant_results_are_filtered_again_by_scope_and_content(self):
        db = Mock(); db.workspace_id = "workspace-a"
        settings = Settings(qdrant_url="http://localhost:6333", embedding_provider="ollama", embedding_model="test")
        providers = Providers(settings)
        index = VectorIndex(db, settings, providers)
        chunk = {"id": "chunk", "document_id": "doc", "content": "support", "title": "Policy", "location": "Page 1"}
        pid = point_id("workspace-a", "chunk")
        wrong = {"id": pid, "score": 1.0, "payload": {"workspace_id": "workspace-b", "content_hash": content_hash(chunk)}}
        with patch.object(index, "index"), patch.object(providers, "embed", return_value=[[1, 0]]), patch.object(index, "request", return_value={"points": [wrong]}) as request:
            self.assertEqual(index.search("support", [chunk]), [])
            filter_value = request.call_args.args[2]["filter"]
            self.assertEqual(filter_value["must"][1], {"has_id": [pid]})
        wrong["payload"]["workspace_id"] = "workspace-a"
        wrong["payload"]["content_hash"] = "stale"
        with patch.object(index, "index"), patch.object(providers, "embed", return_value=[[1, 0]]), patch.object(index, "request", return_value={"points": [wrong]}):
            self.assertEqual(index.search("support", [chunk]), [])


class CostTests(unittest.TestCase):
    def test_configured_rates_use_actual_tokens_and_do_not_price_other_models(self):
        from app.costing import apply_cost
        settings = Settings(openai_model="configured-model", openai_input_usd_per_million=2,
                            openai_output_usd_per_million=8, price_version="team-approved-2026-10")
        result = {"agent_mode": "live", "model": "configured-model", "measurement": {"provider_usage": [
            {"provider": "live", "model": "configured-model", "input_tokens": 1000, "output_tokens": 500}]}}
        apply_cost(result, settings)
        self.assertEqual(result["measurement"]["estimated_cost"], .006)
        self.assertTrue(result["measurement"]["cost"]["complete"])
        result["measurement"]["provider_usage"][0]["model"] = "unpriced-model"
        apply_cost(result, settings)
        self.assertIsNone(result["measurement"]["estimated_cost"])

    def test_unknown_cloud_usage_and_decisions_are_never_reported_as_free(self):
        from app.costing import apply_cost
        settings = Settings()
        for mode, usage in [("live", []), ("live", [{"provider": "live"}]), ("ollama", [{"provider": "openai_decisions"}])]:
            result = {"agent_mode": mode, "measurement": {"provider_usage": usage}}
            apply_cost(result, settings)
            self.assertIsNone(result["measurement"]["estimated_cost"])
        local = {"agent_mode": "ollama", "measurement": {"provider_usage": [{"provider": "ollama", "input_tokens": 50}]}}
        apply_cost(local, settings)
        self.assertEqual(local["measurement"]["estimated_cost"], 0)


@unittest.skipUnless(os.getenv("QDRANT_TEST_URL") and os.getenv("OLLAMA_TEST_URL") and os.getenv("OLLAMA_EMBEDDING_MODEL"), "Real Qdrant/Ollama services not configured")
class SemanticIntegrationTests(unittest.TestCase):
    def test_real_embeddings_persist_and_retrieve_only_authorized_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(db_path=Path(tmp)/"semantic.db", qdrant_url=os.environ["QDRANT_TEST_URL"],
                    ollama_base_url=os.environ["OLLAMA_TEST_URL"], embedding_provider="ollama",
                    embedding_model=os.environ["OLLAMA_EMBEDDING_MODEL"], retrieval_mode="hybrid", local_only=True)
            db = Database(settings.db_path, workspace_id="integration-"+Path(tmp).name); db.initialize()
            good = db.add_document("Customer feedback", "Members cancel subscriptions because replies to service requests take several days.", "test")
            other = db.add_document("Manufacturing", "Metal hinges are inspected at the factory before assembly.", "test")
            first = index_documents(db, settings)
            second = index_documents(db, settings)
            self.assertEqual(first["indexed"], 2)
            self.assertEqual(second["indexed"], 0)
            hits = retrieve(db, "How can we reduce subscriber churn?", document_ids=[good["id"], other["id"]], settings=settings)
            self.assertEqual(hits[0]["document_id"], good["id"])
            restricted = retrieve(db, "Metal hinges", document_ids=[good["id"]], settings=settings)
            self.assertTrue(restricted)
            self.assertEqual({h["document_id"] for h in restricted}, {good["id"]})


if __name__ == "__main__":
    unittest.main()
