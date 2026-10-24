import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.auth import Auth
from app.connectors import public_url, feed_links, sync_connector
from app.database import Database, now
from app.engine import Engine
from app.evaluation import score_case, run_evaluation, operational_metrics
from app.extensions import execute_job, enqueue_due_schedules
from app.main import create_app
from app.reports import html_report, pdf_report, report_font
from app.seed import seed


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(db_path=Path(self.temp.name)/"test.sqlite3", embedded_worker=False)
        self.db = Database(self.settings.db_path)
        self.db.initialize()
        seed(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def result(self):
        return Engine(self.db, self.settings).analyze({"question": "Compare prices and protect our margin", "dataset_id": "demo_pricing"})

    def approved_run(self,result=None):
        run_id = self.db.create_run({"question": "Compare our prices", "dataset_id": "demo_pricing"})
        self.db.finish_run(run_id, self.result() if result is None else result)
        return self.db.review(run_id, "approved", "Test reviewer", "Verified sources")

    def test_reports_require_approval_and_escape_html(self):
        result=self.result()
        result["question"] = '<script>alert("x")</script> \u4e2d\u6587\u62a5\u544a'
        run = self.approved_run(result)
        html = html_report(run)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        pdf = pdf_report(run)
        import fitz
        with fitz.open(stream=pdf, filetype="pdf") as document:
            self.assertGreater(document.page_count, 0)
            self.assertIn("Market Lens", "".join(p.get_text() for p in document))
            self.assertIn("Page 1",document[0].get_text())
            if report_font()!="STSong-Light":
                fonts={font[0] for page in document for font in page.get_fonts()}
                self.assertTrue(any(document.extract_font(xref)[3] for xref in fonts),"Unicode report font must be embedded")
                self.assertIn("\u4e2d\u6587\u62a5\u544a","".join(p.get_text() for p in document))
        run["result"]["question"]="Changed after approval"
        with self.assertRaisesRegex(ValueError,"integrity"):pdf_report(run)
        with self.assertRaisesRegex(ValueError,"integrity"):html_report(run)
        run["status"] = "pending_review"
        with self.assertRaises(ValueError):
            html_report(run)

    def test_ssrf_private_dns_and_credentials_are_rejected(self):
        for address in ['127.0.0.1', '169.254.169.254', '10.0.0.1', '::1', '192.168.1.1']:
            with patch('app.connectors.socket.getaddrinfo', return_value=[(2, 1, 6, '', (address, 80))]):
                with self.assertRaises(ValueError):
                    public_url('http://example.com/')
        for url in ['file:///etc/passwd', 'http://user:password@example.com', 'https://example.com:444']:
            with self.assertRaises(ValueError):
                public_url(url)

    def test_rss_atom_and_entities(self):
        self.assertEqual(feed_links(b'<rss><channel><item><link>/story</link></item></channel></rss>', 'https://example.org/feed'), ['https://example.org/story'])
        self.assertEqual(feed_links(b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><link href="https://example.org/a"/></entry></feed>', 'https://example.org'), ['https://example.org/a'])
        with self.assertRaises(Exception):
            feed_links(b'<!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss>&x;</rss>', 'https://example.org')

    def test_source_versions_are_deduplicated_and_local_policy_enforced(self):
        connector = {"id": "connector_test", "kind": "web", "name": "Article", "url": "https://example.org/article", "dataset_id": None}
        self.db.put_record("connectors", connector["id"], connector)
        article = {"title": "News", "content": "Market demand improved by 12 percent.", "url": connector["url"], "published_at": "2026-10-20"}
        with patch('app.connectors.fetch_public', return_value=(b'original bytes', connector["url"], 'text/html')), patch('app.connectors.article_text', return_value=article):
            first = sync_connector(self.db, self.settings, connector["id"])
            second = sync_connector(self.db, self.settings, connector["id"])
        self.assertEqual(first['sources'][0]['document_id'], second['sources'][0]['document_id'])
        self.assertTrue(second['sources'][0]['duplicate'])
        self.assertEqual(len(self.db.list_records('source_versions')), 1)
        self.settings.local_only = True
        with patch('app.connectors.fetch_public') as fetch, self.assertRaises(ValueError):
            sync_connector(self.db, self.settings, connector["id"])
        fetch.assert_not_called()

    def test_evaluation_measures_results_not_only_execution(self):
        result = self.result()
        case = {"dataset_id": "demo_pricing", "expected_sql": 'SELECT SUM(revenue) AS r FROM data_demo_pricing', "expected_action": "protect_margin", "expected_route": "mixed"}
        scores, route = score_case(case, result, self.db)
        self.assertFalse(scores['sql_result_match'])
        self.assertTrue(scores['action_match'])
        self.assertEqual(route, 'mixed')
        case['expected_sql'] = result['evidence'][0]['sql']
        self.assertTrue(score_case(case, result, self.db)[0]['sql_result_match'])

    def test_labelled_evaluation_missing_metrics_remain_unscored(self):
        case = {"id": "case_test", "name": "Pricing", "question": "Compare prices and margins", "dataset_id": "demo_pricing", "expected_action": "protect_margin", "split": "holdout"}
        self.db.put_record('evaluation_cases', case['id'], case)
        report = run_evaluation(self.db, self.settings, {"split": "holdout", "agent_mode": "local"})
        self.assertEqual(report['completion_rate'], 1)
        self.assertEqual(report['summary']['action_match'], 1)
        self.assertNotIn('model_judged_faithfulness', report['summary'])
        self.assertNotIn('retrieval_recall', report['summary'])
        self.assertEqual(len(self.db.list_records('evaluation_results')), 1)

    def test_operational_denominators_and_feedback(self):
        empty = operational_metrics(self.db)
        self.assertIsNone(empty['human_approval_rate'])
        self.assertIsNone(empty['user_satisfaction'])
        self.approved_run()
        metrics = operational_metrics(self.db)
        self.assertEqual(metrics['reviewed_count'], 1)
        self.assertEqual(metrics['human_approval_rate'], 1)

    def test_schedule_enqueue_and_replay_deduplicate(self):
        self.db.put_record('schedules', 'schedule_test', {"id": "schedule_test", "name": "Daily", "enabled": True, "interval_minutes": 60,
                           "next_run_at": "2020-01-01T00:00:00+00:00", "request": {"question": "Compare prices and margins", "dataset_id": "demo_pricing"}})
        enqueue_due_schedules(self.db, self.settings)
        enqueue_due_schedules(self.db, self.settings)
        tasks = [t for t in self.db.tasks() if t['kind'] == 'scheduled_analysis']
        self.assertEqual(len(tasks), 1)
        payload = self.db.task(tasks[0]['id'])['payload']
        first = execute_job('scheduled_analysis', payload, self.db, self.settings)
        second = execute_job('scheduled_analysis', payload, self.db, self.settings)
        self.assertEqual(first['run_id'], second['run_id'])

    def test_disabled_schedule_owner_cannot_enqueue_or_execute(self):
        self.settings.auth_enabled = True
        auth = Auth(self.db, self.settings)
        auth.create_user("admin@example.com", "administrator-password", "Admin", "admin")
        owner = auth.create_user("analyst@example.com", "analyst-password", "Analyst", "analyst")
        schedule = {"id": "owned_schedule", "name": "Owned", "enabled": True, "interval_minutes": 60,
                    "owner_id": owner["id"], "next_run_at": "2020-01-01T00:00:00+00:00",
                    "request": {"question": "Compare prices and margins", "dataset_id": "demo_pricing"}}
        self.db.put_record("schedules", schedule["id"], schedule)
        enqueue_due_schedules(self.db, self.settings)
        task = next(t for t in self.db.tasks() if t["kind"] == "scheduled_analysis")
        auth.update_user(owner["id"], disabled=True)
        self.assertEqual(self.db.task(task["id"])["status"], "cancelled")
        self.assertTrue(execute_job("scheduled_analysis", task["payload"], self.db, self.settings)["skipped"])
        enqueue_due_schedules(self.db, self.settings)
        self.assertFalse(self.db.get_record("schedules", schedule["id"])["enabled"])
        self.assertEqual(self.db.runs(), [])

    def test_schedule_rechecks_local_policy_at_execution(self):
        self.db.put_record("schedules", "cloud_schedule", {"id": "cloud_schedule", "enabled": True,
                           "request": {"question": "Compare prices and margins", "dataset_id": "demo_pricing", "agent_mode": "live"}})
        self.settings.local_only = True
        with self.assertRaisesRegex(ValueError, "local-only"):
            execute_job("scheduled_analysis", {"schedule_id": "cloud_schedule", "occurrence_id": "occurrence_test"}, self.db, self.settings)
        self.assertEqual(self.db.runs(), [])


class ExtensionAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(Settings(db_path=Path(self.temp.name)/'test.sqlite3', embedded_worker=False)))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def test_connector_case_dictionary_and_schedule_validation(self):
        r = self.client.post('/api/connectors', json={"name": "News", "kind": "web", "url": "https://example.org/article"})
        self.assertEqual(r.status_code, 201, r.text)
        job = self.client.post(f"/api/connectors/{r.json()['id']}/sync", json={})
        self.assertEqual(job.status_code, 202, job.text)
        self.assertEqual(self.client.get('/api/connectors').json()[0]['name'], 'News')
        bad = self.client.post('/api/evaluation/cases', json={"name": "Bad", "question": "What is revenue?", "dataset_id": "missing"})
        self.assertIn(bad.status_code, (400, 404))
        good = self.client.put('/api/datasets/demo_pricing/dictionary', json={"currency": "SGD", "columns": {"revenue": "Net sales"}})
        self.assertEqual(good.status_code, 200, good.text)
        bad = self.client.put('/api/datasets/demo_pricing/dictionary', json={"columns": {"invented": "bad"}})
        self.assertEqual(bad.status_code, 400)
        schedule = self.client.post('/api/schedules', json={"name": "Hourly", "interval_minutes": 60, "request": {"question": "Compare price and margin", "dataset_id": "demo_pricing"}})
        self.assertEqual(schedule.status_code, 201, schedule.text)

    def test_report_exports_recheck_approved_result_integrity(self):
        db = self.client.app.state.db
        run_id = db.create_run({"question": "Compare our prices", "dataset_id": "demo_pricing"})
        db.finish_run(run_id, {"question": "Original report"})
        db.review(run_id, "approved", "Reviewer", "")
        with db.connect() as con:
            con.execute("UPDATE runs SET result_json=? WHERE id=?", (json.dumps({"question": "Tampered report"}), run_id))
        for format in ("html", "pdf"):
            response = self.client.get(f"/api/analyses/{run_id}/report?format={format}")
            self.assertEqual(response.status_code, 409, response.text)
            self.assertIn("integrity", response.text)

    def test_schedule_validates_all_dataset_scopes_and_models(self):
        request = {"question": "Compare our prices across datasets", "dataset_ids": ["demo_pricing", "demo_retention"]}
        response = self.client.post("/api/schedules", json={"name": "Joined analysis", "interval_minutes": 60, "request": request})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["request"]["dataset_id"], "demo_pricing")
        request["dataset_ids"].append("missing")
        self.assertEqual(self.client.post("/api/schedules", json={"name": "Invalid scope", "interval_minutes": 60, "request": request}).status_code, 400)
        for modes in ({"agent_mode": "live"}, {"decision_mode": "openai_decisions"}, {"decision_mode": "llm", "agent_mode": "local"}):
            request = {"question": "Compare our prices and margins", "dataset_id": "demo_pricing", **modes}
            self.assertEqual(self.client.post("/api/schedules", json={"name": "Invalid model", "interval_minutes": 60, "request": request}).status_code, 400)
        self.client.app.state.settings.local_only = True
        request = {"question": "Compare our prices and margins", "dataset_id": "demo_pricing", "agent_mode": "live"}
        response = self.client.post("/api/schedules", json={"name": "Local policy", "interval_minutes": 60, "request": request})
        self.assertEqual(response.status_code, 400)
        self.assertIn("local-only", response.text)


if __name__ == '__main__':
    unittest.main()
