"""Local model capability, thinking and bounded generation contracts."""
import json
import unittest
from unittest.mock import Mock, patch

import httpx

from app.config import Settings
from app.providers import Providers, ProviderError, QueryPlan, DecisionBrief


def response(value):
    result = Mock()
    result.json.return_value = value
    return result


class OllamaProfileProviderTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(ollama_model="qwen3.5:test", local_only=True)
        self.provider = Providers(self.settings)
        self.metadata = response({"capabilities": ["completion", "vision", "thinking"],
                                  "thinking": {"values": [False, True], "default": True}})
        self.answer = response({"done": True, "done_reason": "stop",
                                "message": {"content": json.dumps({"sql": "SELECT 1", "search_query": ""})},
                                "prompt_eval_count": 10, "eval_count": 5,
                                "load_duration": 20_000_000, "eval_duration": 80_000_000})

    def call(self, **kwargs):
        return self.provider.structured(QueryPlan, "Return read-only SQL.", {}, provider="ollama", **kwargs)

    def test_supported_thinking_disabled_and_model_unloaded_with_real_usage(self):
        with patch("app.providers.httpx.post", side_effect=[self.metadata, self.answer]) as post:
            result, usage = self.call()
        self.assertEqual(result["sql"], "SELECT 1")
        self.assertTrue(post.call_args_list[0].args[0].endswith("/api/show"))
        payload = post.call_args.kwargs["json"]
        self.assertIs(payload["think"], False)
        self.assertEqual(payload["keep_alive"], "0")
        self.assertEqual(payload["options"]["num_predict"], 2400)
        self.assertEqual(usage["request_count"], 1)
        self.assertEqual(usage["load_duration_ms"], 20)
        self.assertEqual(usage["generation_duration_ms"], 80)
        self.assertFalse(post.call_args.kwargs["trust_env"])

    def test_capability_probe_cached_but_answer_not_cached(self):
        with patch("app.providers.httpx.post", side_effect=[self.metadata, self.answer, self.answer]) as post:
            self.call()
            self.call()
        self.assertEqual(len([c for c in post.call_args_list if c.args[0].endswith("/api/show")]), 1)
        self.assertEqual(len([c for c in post.call_args_list if c.args[0].endswith("/api/chat")]), 2)

    def test_text_model_cannot_receive_images(self):
        with patch("app.providers.httpx.post", return_value=response({"capabilities": ["completion"]})) as post:
            with self.assertRaisesRegex(ProviderError, "cannot read images"):
                self.call(images=["synthetic-image"])
        self.assertEqual(post.call_count, 1)

    def test_embedding_model_cannot_be_used_for_analysis(self):
        with patch("app.providers.httpx.post", return_value=response({"capabilities": ["embedding"]})):
            with self.assertRaisesRegex(ProviderError, "cannot generate analysis"):
                self.call()

    def test_unsupported_thinking_control_fails_without_sending_evidence(self):
        metadata = response({"capabilities": ["completion", "thinking"],
                             "thinking": {"values": ["low", "high"], "default": "low"}})
        with patch("app.providers.httpx.post", return_value=metadata) as post:
            with self.assertRaisesRegex(ProviderError, "cannot explicitly disable"):
                self.call()
        self.assertEqual(post.call_count, 1)

    def test_default_and_enabled_thinking_use_advertised_controls(self):
        for mode, expected in (("default", None), ("on", True)):
            self.settings.ollama_thinking = mode
            with patch("app.providers.httpx.post", side_effect=lambda url, **kw: self.metadata if url.endswith("/api/show") else self.answer) as post:
                self.call()
            payload = post.call_args.kwargs["json"]
            self.assertEqual(payload.get("think"), expected)

    def test_metadata_failure_is_sanitized_and_does_not_attempt_generation(self):
        with patch("app.providers.httpx.post", side_effect=httpx.ConnectError("private payload")) as post:
            with self.assertRaisesRegex(ProviderError, "Cannot inspect") as caught:
                self.call()
        self.assertNotIn("private payload", str(caught.exception))
        self.assertEqual(post.call_count, 1)

    def test_exhausted_deadline_blocks_metadata_and_generation(self):
        with patch("app.providers.httpx.post") as post:
            with self.assertRaisesRegex(ProviderError, "time budget"):
                self.call(request_timeout=0)
        post.assert_not_called()

    def test_incomplete_answer_is_not_accepted_as_valid_json(self):
        self.answer.json.return_value["done_reason"] = "length"
        with patch("app.providers.httpx.post", side_effect=[self.metadata, self.answer]):
            with self.assertRaisesRegex(ProviderError, "incomplete"):
                self.call()

    def test_title_at_schema_limit_is_regenerated_within_call_budget(self):
        cut_off = "Implement the one-business-day response deadline and require the support lead to review the open que"
        cut_off = (cut_off+"x"*100)[:100]
        brief = {"recommendation": cut_off, "explanation": "The policy specifies a daily review.",
                 "alternatives": [], "evidence_ids": ["policy_1"]}
        bad = response({"done": True, "message": {"content": json.dumps(brief)}})
        good = response({"done": True, "message": {"content": json.dumps({**brief, "recommendation": "Enforce response deadlines and daily ownership"})}})
        with patch("app.providers.httpx.post", side_effect=[self.metadata, bad, good]) as post:
            result, usage = self.provider.structured(DecisionBrief, "Recommend a next step.", {}, provider="ollama", max_requests=2)
        self.assertEqual(result["recommendation"], "Enforce response deadlines and daily ownership")
        self.assertEqual(usage["request_count"], 2)
        self.assertIn("3–8 words", post.call_args.kwargs["json"]["messages"][-1]["content"])
        with patch("app.providers.httpx.post", return_value=bad) as post:
            with self.assertRaisesRegex(ProviderError, "invalid structured"):
                self.provider.structured(DecisionBrief, "Recommend a next step.", {}, provider="ollama", max_requests=1)
        self.assertEqual(post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
