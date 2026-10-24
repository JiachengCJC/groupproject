"""Reproductions found during independent connector/evaluation/export review."""
import gzip
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import httpx

from app.config import Settings
from app.connectors import bounded_kaggle_handle, fetch_public, public_url
from app.database import Database,now
from app.evaluation import run_evaluation, operational_metrics
from app.observability import log_analysis


class ConnectorNetworkTests(unittest.TestCase):
    def fetch(self,handler,url="https://example.org/article",**kwargs):
        original=httpx.Client
        def client(**options):return original(transport=httpx.MockTransport(handler),**options)
        def dns(host,port,**options):
            return [(2,1,6,"",("127.0.0.1" if host=="internal.example" else "93.184.216.34",port))]
        with patch("app.connectors.httpx.Client",side_effect=client),patch("app.connectors.socket.getaddrinfo",side_effect=dns):
            return fetch_public(url,**kwargs)

    def test_redirect_is_revalidated_before_private_host_access(self):
        requests=[]
        def handler(request):
            requests.append(request)
            return httpx.Response(302,headers={"location":"http://internal.example/secret"})
        with self.assertRaisesRegex(ValueError,"Private"):
            self.fetch(handler)
        self.assertEqual(len(requests),1)
        self.assertEqual(requests[0].url.host,"93.184.216.34")
        self.assertEqual(requests[0].headers["host"],"example.org")
        self.assertEqual(requests[0].extensions["sni_hostname"],"example.org")

    def test_stream_limits_and_compression_bombs(self):
        def oversized(request):return httpx.Response(200,stream=httpx.ByteStream(b"0123456789"))
        with self.assertRaisesRegex(ValueError,"download limit"):self.fetch(oversized,max_bytes=5)
        compressed=gzip.compress(b"x"*100000)
        def bomb(request):
            self.assertEqual(request.headers["accept-encoding"],"identity")
            return httpx.Response(200,headers={"content-encoding":"gzip"},stream=httpx.ByteStream(compressed))
        with self.assertRaisesRegex(ValueError,"uncompressed"):self.fetch(bomb)

    def test_scheme_port_mismatch_rejected(self):
        for url in ("https://example.org:80","http://example.org:443"):
            with self.assertRaises(ValueError):public_url(url)

    def test_kaggle_bundle_is_bounded_and_versioned_before_download(self):
        sdk=MagicMock()
        api=sdk.__enter__.return_value.datasets.dataset_api_client
        api.get_dataset.return_value.current_version_number=7
        api.list_dataset_files.return_value=SimpleNamespace(dataset_files=[SimpleNamespace(total_bytes=12)],next_page_token=None,error_message=None)
        with patch("kagglehub.clients.build_kaggle_client",return_value=sdk):
            self.assertEqual(bounded_kaggle_handle("owner/data"),"owner/data/versions/7")
            self.assertEqual(api.list_dataset_files.call_args.args[0].dataset_version_number,7)
            api.list_dataset_files.return_value.dataset_files=[SimpleNamespace(total_bytes=100_000_001)]
            with self.assertRaisesRegex(ValueError,"100 MB"):bounded_kaggle_handle("owner/data/versions/2")


class EvaluationFailureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.temp.name)/"evaluation.sqlite3")
        self.db.initialize()
        self.result={"decision":{"choice":"action"},"evidence":[],"measurement":{"elapsed_ms":30,"citation_coverage":1},"rationale":"answer","agents":[]}
    def tearDown(self):self.temp.cleanup()
    def case(self,id):
        self.db.put_record("evaluation_cases",id,{"id":id,"name":id,"question":"Question?","expected_action":"action","split":"holdout"})

    def test_failed_labelled_case_stays_in_accuracy_denominator(self):
        self.case("one");self.case("two")
        with patch("app.evaluation.Engine.analyze",side_effect=[self.result,ValueError("Provider failed")]):
            report=run_evaluation(self.db,Settings(),{"split":"holdout"})
        self.assertEqual(report["completion_rate"],.5)
        self.assertEqual(report["summary"]["action_match"],.5)
        self.assertEqual(report["sample_counts"]["action_match"],2)
        self.assertEqual(report["completed_summary"]["action_match"],1)
        self.assertEqual(report["completed_sample_counts"]["action_match"],1)

    def test_judge_failure_preserves_deterministic_measurements(self):
        self.case("one")
        with patch("app.evaluation.Engine.analyze",return_value=self.result),patch("app.evaluation.Providers.structured",side_effect=ValueError("Judge unavailable")):
            report=run_evaluation(self.db,Settings(),{"split":"holdout","judge_mode":"live"})
        self.assertEqual(report["completion_rate"],0)
        self.assertEqual(report["analysis_completion_rate"],1)
        self.assertEqual(report["summary"]["action_match"],1)
        self.assertNotIn("model_judged_faithfulness",report["summary"])

    def test_missing_latency_is_not_a_zero_ms_sample(self):
        run=self.db.create_run({"question":"Question?"})
        self.db.finish_run(run,{"measurement":{}})
        metrics=operational_metrics(self.db)
        self.assertIsNone(metrics["latency_p50_ms"])
        self.assertEqual(metrics["latency_sample_count"],0)

    def test_operational_denominator_includes_history_beyond_200(self):
        with self.db.connect() as con:
            con.executemany("INSERT INTO runs(id,created_at,status,request_json,result_json,workspace_id) VALUES (?,?,?,?,?,?)",
                            [("old_run_"+str(i),now(),"failed" if i==0 else "pending_review",'{"question":"test"}',None if i==0 else '{"measurement":{}}',"default") for i in range(201)])
        metrics=operational_metrics(self.db)
        self.assertEqual(metrics["run_count"],201)
        self.assertEqual(metrics["terminal_count"],201)
        self.assertEqual(metrics["completion_rate"],200/201)


class ObservabilityPrivacyTests(unittest.TestCase):
    def test_local_only_refuses_proxy_before_exporting(self):
        module, client = self.mlflow()
        with patch.dict("sys.modules", {"mlflow": module}), patch.dict("os.environ", {"HTTPS_PROXY": "https://external-proxy.invalid"}):
            result = log_analysis(self.result(), Settings(local_only=True, mlflow_tracking_uri="http://mlflow:5000"))
        self.assertEqual(result["status"], "failed")
        client.create_run.assert_not_called()

    def result(self):
        return {"question":"private-question","decision":{"choice":"act"},"evidence":[{"id":"e1","content":"private-evidence"}],
                "measurement":{"elapsed_ms":1,"raw_response":"private-response","provider_usage":[{"input_tokens":10,"body":"private-usage-body"}]},
                "tool_trace":[{"tool":"sql","input":"private-sql","error":"private-error","elapsed_ms":1,"status":"completed"}]}
    def mlflow(self):
        module=MagicMock()
        client=module.MlflowClient.return_value
        client.get_experiment_by_name.return_value.experiment_id="experiment"
        client.create_run.return_value.info.run_id="mlrun"
        client.start_trace.return_value.trace_id="trace"
        client.start_trace.return_value.span_id="root"
        return module,client

    def test_default_trace_excludes_nested_provider_payloads(self):
        module,client=self.mlflow()
        with patch.dict("sys.modules",{"mlflow":module}):
            result=log_analysis(self.result(),Settings(mlflow_tracking_uri="http://mlflow:5000"),"localrun")
        self.assertEqual(result["status"],"recorded")
        snapshot=json.dumps(client.log_dict.call_args.args[1])
        self.assertNotIn("private-",snapshot)
        self.assertEqual(client.log_dict.call_args.args[1]["measurement"]["provider_usage"][0]["input_tokens"],10)

    def test_failed_export_terminates_mlflow_run_without_raw_error(self):
        module,client=self.mlflow()
        client.log_dict.side_effect=ValueError("private-server-response")
        with patch.dict("sys.modules",{"mlflow":module}):
            result=log_analysis(self.result(),Settings(mlflow_tracking_uri="http://mlflow:5000"),"localrun")
        self.assertEqual(result["status"],"failed")
        self.assertNotIn("private-server-response",str(result))
        client.set_terminated.assert_called_once_with("mlrun",status="FAILED")


if __name__=="__main__":unittest.main()
