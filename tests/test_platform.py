"""Real persistence, role, isolation and durable worker regression tests.

Set TEST_DATABASE_URL to run the same storage contracts against PostgreSQL.
Every PostgreSQL test uses a unique workspace and unique table identifiers.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.auth import Auth, COOKIE, password_hash, password_matches
from app.config import Settings
from app.database import Database, digest, uid
from app.engine import Engine
from app.jobs import Worker
from app.main import create_app
from app.seed import SCENARIOS, scenario_csv


class StorageContracts:
    def seed_business(self):
        self.dataset=self.db.import_csv("Pricing",scenario_csv(SCENARIOS[0]))
        self.doc=self.db.add_document("Policy","Use a targeted offer to protect margins.","Internal policy",self.dataset["id"])
        return self.dataset

    def request(self):
        return {"question":"Should we protect our margins?","dataset_id":self.dataset["id"],"agent_mode":"local","decision_mode":"baseline"}

    def test_import_quality_version_and_scoped_reads(self):
        d=self.db.import_csv("Quality","name,value\nA,1\nA,1\nB,\n")
        self.assertEqual(d["quality"]["duplicate_rows"],1)
        self.assertEqual(d["quality"]["missing_cells"],1)
        self.assertEqual(len(d["version_hash"]),64)
        other=self.db.scoped(uid("other"))
        self.assertEqual(other.datasets(),[])
        with self.assertRaises(ValueError):other.dataset(d["id"])
        with self.assertRaises(ValueError):other.query("SELECT * FROM "+d["table_name"],d["id"])

    def test_document_and_original_isolation(self):
        self.seed_business()
        original=self.db.save_upload("note.txt",b"private",digest(b"private"),None,"General","text","none")
        other=self.db.scoped(uid("other"))
        for operation in [lambda:other.document(self.doc["id"]),lambda:other.upload(original),lambda:other.chunks(document_ids=[self.doc["id"]])]:
            with self.assertRaises(ValueError):operation()
        self.db.delete_document(self.doc["id"])
        self.assertEqual(self.db.chunks(self.dataset["id"]),[])

    def test_shared_text_ingestion_and_duplicate_lookup(self):
        from app.ingestion import ingest
        from app.providers import Providers
        settings=Settings(seed_demo=False)
        result=ingest(self.db,settings,Providers(settings),"shared.txt",b"Private shared research with original text.")
        self.assertEqual(len(result["documents"]),1)
        duplicate=ingest(self.db,settings,Providers(settings),"same-content.txt",b"Private shared research with original text.")
        self.assertEqual(duplicate["status"],"duplicate")
        self.assertEqual(duplicate["file_id"],result["file_id"])

    def test_shared_state_records_are_workspace_scoped(self):
        self.db.put_record("checkpoint","one",{"value":1})
        other=self.db.scoped(uid("other"))
        self.assertIsNone(other.get_record("checkpoint","one"))
        other.put_record("checkpoint","one",{"value":2})
        self.assertEqual(self.db.get_record("checkpoint","one"),{"value":1})
        self.assertFalse(other.delete_record("checkpoint","missing"))

    def test_result_hash_approval_and_immutability(self):
        self.seed_business()
        run_id=self.db.create_run(self.request())
        result={"answer":"Targeted offer","evidence":[self.doc]}
        self.db.finish_run(run_id,result)
        reviewed=self.db.review(run_id,"approved","Actual reviewer","Checked")
        self.assertEqual(reviewed["review"]["reviewer_id"],"reviewer_test")
        self.assertEqual(reviewed["review"]["result_sha256"],digest(result))
        self.assertIn(self.doc["id"],reviewed["source_snapshot"]["documents"])
        with self.assertRaises(ValueError):self.db.finish_run(run_id,{"answer":"Replaced"})
        with self.assertRaises(ValueError):self.db.review(run_id,"rejected","Other","Changed")
        with self.db.connect() as con:con.execute("UPDATE runs SET result_json=? WHERE id=?",(json.dumps({"tampered":True}),run_id))
        with self.assertRaises(ValueError):self.db.approved_run(run_id)

    def test_analysis_and_task_creation_is_atomic_idempotent(self):
        self.seed_business()
        key=uid("schedule")
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids=list(pool.map(lambda _:self.db.create_run(self.request(),idempotency_key=key),range(4)))
        self.assertEqual(len(set(ids)),1)
        task=self.db.analysis_task(ids[0])
        self.assertEqual(task["payload"]["run_id"],ids[0])
        with ThreadPoolExecutor(max_workers=4) as pool:
            tasks=list(pool.map(lambda _:self.db.create_task("connector",{"id":"a"},idempotency_key=key),range(4)))
        self.assertEqual(len(set(tasks)),1)

    def test_dictionary_snapshot_is_frozen_when_run_is_queued(self):
        self.seed_business()
        initial={"currency":"SGD","columns":{"revenue":"Net sales"}}
        self.db.put_record("dictionaries",self.dataset["id"],initial)
        run_id=self.db.create_run(self.request())
        self.db.put_record("dictionaries",self.dataset["id"],{"currency":"USD"})
        run=self.db.run(run_id)
        self.assertEqual(run["request"]["_source_dictionaries"][self.dataset["id"]],initial)
        self.assertEqual(run["source_snapshot"]["dictionaries"][self.dataset["id"]],digest(initial))

    def test_worker_claims_once_and_expired_owner_is_fenced(self):
        task_id=self.db.create_task("test",{"value":1})
        with ThreadPoolExecutor(max_workers=4) as pool:
            claims=list(pool.map(lambda i:self.db.claim_task(str(i),30),range(4)))
        owned=[c for c in claims if c and c["id"]==task_id]
        self.assertEqual(len(owned),1)
        first=owned[0]
        with self.db.connect() as con:con.execute("UPDATE tasks SET lease_expires=0 WHERE id=?",(task_id,))
        second=self.db.claim_task("replacement",30)
        self.assertEqual(second["id"],task_id)
        self.assertNotEqual(second["lease_token"],first["lease_token"])
        self.assertFalse(self.db.finish_task(task_id,first["lease_token"],result={"wrong":True}))
        self.assertTrue(self.db.finish_task(task_id,second["lease_token"],result={"ok":True}))
        self.assertEqual(self.db.task(task_id)["status"],"completed")

    def test_cancel_fences_running_analysis(self):
        self.seed_business();run_id=self.db.create_run(self.request())
        task=self.db.claim_task("owner",30)
        scoped=self.db.scoped();scoped._lease=(task["id"],task["lease_token"])
        self.db.cancel_task(task["id"])
        with self.assertRaises(ValueError):scoped.finish_run(run_id,{"answer":"late"})
        with self.assertRaises(ValueError):scoped.create_run(self.request())
        with self.assertRaises(ValueError):scoped.create_task("connector",{"id":"stale-worker"})
        self.assertEqual(self.db.run(run_id)["status"],"cancelled")
        retry=self.db.retry_task(task["id"])
        self.assertNotEqual(retry["run_id"],run_id)
        self.assertEqual(self.db.run(retry["run_id"])["request"]["retry_of"],run_id)

    def test_cross_workspace_worker_uses_saved_scope(self):
        self.seed_business();run_id=self.db.create_run(self.request())
        worker=Worker(self.db,Settings(seed_demo=False))
        self.assertTrue(worker.run_once())
        self.assertEqual(self.db.run(run_id)["status"],"pending_review")
        self.assertEqual(self.db.analysis_task(run_id)["status"],"completed")
        with self.assertRaises(ValueError):self.db.scoped(uid("other")).run(run_id)

    def test_selected_table_join_and_forbidden_sql(self):
        left=self.db.import_csv("Left","id,revenue\n1,10\n2,20\n")
        right=self.db.import_csv("Right","id,category\n1,A\n2,B\n")
        sql=f'SELECT l.id,l.revenue,r.category FROM {left["table_name"]} l JOIN {right["table_name"]} r ON l.id=r.id ORDER BY l.id'
        with self.assertRaises(ValueError):self.db.query(sql,left["id"])
        result=self.db.query(sql,left["id"],dataset_ids=[right["id"]])
        self.assertEqual(len(result["rows"]),2)
        queries=["SELECT * FROM users","SELECT * FROM sessions","SELECT 1; SELECT 2"]
        if self.db.dialect=="postgresql":
            # PostgreSQL resolves a forward CTE name to the real table, whereas
            # SQLite resolves it to the later CTE (which contains only SELECT 1).
            queries.append("WITH a AS (SELECT * FROM users), users AS (SELECT 1) SELECT * FROM a")
        for unsafe in queries:
            with self.subTest(sql=unsafe),self.assertRaises(ValueError):self.db.query(unsafe,left["id"])

    def test_safe_window_functions_remain_available(self):
        data=self.db.import_csv("Windows","period,value\n1,10\n2,20\n3,30\n")
        sql="SELECT period, LAG(value) OVER (ORDER BY period) AS previous, LEAD(value) OVER (ORDER BY period) AS next, FIRST_VALUE(value) OVER (ORDER BY period) AS first, LAST_VALUE(value) OVER (ORDER BY period) AS last, NTH_VALUE(value,2) OVER (ORDER BY period) AS second, NTILE(2) OVER (ORDER BY period) AS bucket, CUME_DIST() OVER (ORDER BY period) AS distribution, PERCENT_RANK() OVER (ORDER BY period) AS percentile FROM "+data["table_name"]+" ORDER BY period"
        result=self.db.query(sql,data["id"])
        self.assertEqual([row["previous"] for row in result["rows"]],[None,10,20])
        self.assertEqual(result["rows"][-1]["percentile"],1)

    def test_connector_versions_commit_once_under_concurrency(self):
        from app.connectors import _store_article
        connector={"id":"concurrent-source","dataset_id":None}
        article={"title":"News","url":"https://example.org/news","content":"Versioned original content."}
        with ThreadPoolExecutor(max_workers=6) as pool:
            versions=list(pool.map(lambda _: _store_article(self.db,connector,article,b"original"),range(6)))
        self.assertEqual(len({v["document_id"] for v in versions}),1)
        self.assertEqual(sum(bool(v.get("duplicate")) for v in versions),5)
        self.assertEqual(len(self.db.documents()),1)
        with self.db.connect() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) AS n FROM uploads WHERE workspace_id=?",(self.db.workspace_id,)).fetchone()["n"],1)

    def test_connector_failure_rolls_back_original_and_version(self):
        from app.connectors import _store_article
        with patch.object(Database,"add_document",side_effect=ValueError("Failed extraction")),self.assertRaises(ValueError):
            _store_article(self.db,{"id":"rollback-source"},{"title":"News","url":"https://example.org/news","content":"Content"},b"original")
        self.assertEqual(self.db.list_records("source_versions"),[])
        with self.db.connect() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) AS n FROM uploads WHERE workspace_id=?",(self.db.workspace_id,)).fetchone()["n"],0)


class SQLitePlatformTests(StorageContracts,unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.tmp.name)/"platform.sqlite3",workspace_id="isolated",user_id="reviewer_test")
        self.db.initialize()
    def tearDown(self):self.tmp.cleanup()

    def test_worker_logs_do_not_leak_exception_payloads(self):
        task_id=self.db.create_task("secret_probe",{})
        worker=Worker(self.db,Settings(seed_demo=False,trace_content=False))
        def provider_failure(*args):
            try:
                raise RuntimeError("provider-payload-secret")
            except RuntimeError as cause:
                raise ValueError("wrapped-api-key-secret") from cause
        with patch("app.extensions.execute_job",side_effect=provider_failure),self.assertLogs("app.jobs",level="ERROR") as logs:
            self.assertTrue(worker.run_once())
        rendered="\n".join(logs.output)
        self.assertIn(task_id,rendered)
        self.assertIn("ValueError",rendered)
        for secret in ("provider-payload-secret","wrapped-api-key-secret"):
            self.assertNotIn(secret,rendered)
            self.assertNotIn(secret,self.db.task(task_id)["error"])


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"),"Set TEST_DATABASE_URL for PostgreSQL integration")
class PostgreSQLPlatformTests(StorageContracts,unittest.TestCase):
    def setUp(self):
        self.db=Database(os.environ["TEST_DATABASE_URL"],workspace_id=uid("test"),user_id="reviewer_test",
                         analytics_url=os.getenv("TEST_ANALYTICS_DATABASE_URL",""),analytics_role=os.getenv("TEST_ANALYTICS_ROLE",""))
        self.db.initialize()
    def tearDown(self):
        # Prevent globally dispatchable unfinished test jobs from interfering
        # with other workers or the next contract; dataset IDs remain unique.
        with self.db.connect() as con:
            con.execute("UPDATE tasks SET status='cancelled' WHERE workspace_id=? AND status IN ('queued','running')",(self.db.workspace_id,))


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.settings=Settings(db_path=Path(self.tmp.name)/"auth.sqlite3",auth_enabled=True,setup_token="setup-for-test-only",embedded_worker=False)
        self.app=create_app(self.settings)
        self.client=TestClient(self.app);self.client.__enter__()
        self.password="correct horse battery staple"
    def tearDown(self):self.client.__exit__(None,None,None);self.tmp.cleanup()
    def setup(self):
        response=self.client.post("/api/auth/setup",json={"email":"admin@example.com","password":self.password,"name":"Admin User","setup_token":"setup-for-test-only"})
        self.assertEqual(response.status_code,201,response.text)
        return response.json()["user"]
    def create_user(self,role):
        r=self.client.post("/api/auth/users",json={"email":role+"@example.com","password":self.password,"name":role.title()+" User","role":role})
        self.assertEqual(r.status_code,201,r.text);return r.json()
    def login(self,email):
        r=self.client.post("/api/auth/login",json={"email":email,"password":self.password});self.assertEqual(r.status_code,200,r.text)
    def test_setup_login_logout_and_session_cookie(self):
        self.assertEqual(self.client.get("/api/sources").status_code,401)
        self.assertTrue(self.client.get("/api/auth/status").json()["setup_required"])
        bad=self.client.post("/api/auth/setup",json={"email":"admin@example.com","password":self.password,"name":"Admin User","setup_token":"wrong"})
        self.assertEqual(bad.status_code,403)
        user=self.setup();self.assertNotIn("password_hash",user)
        self.assertFalse(self.client.get("/api/auth/status").json()["setup_required"])
        again=self.client.post("/api/auth/setup",json={"email":"new@example.com","password":self.password,"name":"Admin User","setup_token":"setup-for-test-only"})
        self.assertEqual(again.status_code,409)
        self.assertEqual(self.client.get("/api/sources").status_code,200)
        self.client.post("/api/auth/logout",json={})
        self.assertEqual(self.client.get("/api/sources").status_code,401)
        self.assertEqual(self.client.post("/api/auth/login",json={"email":"admin@example.com","password":"wrong"}).status_code,401)
        self.login("admin@example.com")
        self.assertEqual(self.client.get("/api/auth/status").json()["user"]["id"],user["id"])
    def test_roles_and_authenticated_reviewer_identity(self):
        self.setup();analyst=self.create_user("analyst");reviewer=self.create_user("reviewer");self.create_user("viewer")
        self.login("analyst@example.com")
        response=self.client.post("/api/analyses",json={"question":"Should we protect margins?","dataset_id":"demo_pricing"})
        self.assertEqual(response.status_code,202,response.text);run_id=response.json()["id"]
        Worker(self.app.state.db,self.settings).run_once()
        review={"decision":"approved","reviewer":"Forged identity","notes":"Checked"}
        self.assertEqual(self.client.post(f"/api/analyses/{run_id}/review",json=review).status_code,403)
        self.login("reviewer@example.com")
        result=self.client.post(f"/api/analyses/{run_id}/review",json=review)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()["review"]["reviewer"],reviewer["name"])
        self.assertEqual(result.json()["review"]["reviewer_id"],reviewer["id"])
        self.assertEqual(self.client.post("/api/datasets",json={"name":"bad","content":"a\n1\n"}).status_code,403)
        self.login("viewer@example.com")
        self.assertEqual(self.client.get("/api/sources").status_code,200)
        self.assertEqual(self.client.post("/api/query",json={"dataset_id":"demo_pricing","sql":"SELECT 1"}).status_code,403)
        self.assertEqual(self.client.get("/api/auth/users").status_code,403)
    def test_cross_workspace_isolation_through_api(self):
        self.setup()
        a=self.app.state.auth.create_user("foreign@example.com",self.password,"Foreign User","admin",workspace_id="foreign")
        run_id=self.app.state.db.create_run({"question":"Test protected data","dataset_id":"demo_pricing"})
        d=self.app.state.db.add_document("Private","Private content","test")
        self.login("foreign@example.com")
        self.assertEqual(self.client.get("/api/sources").json(),{"datasets":[],"documents":[]})
        self.assertEqual(self.client.get("/api/analyses/"+run_id).status_code,404)
        self.assertEqual(self.client.get("/api/documents/"+d["id"]).status_code,404)
        self.assertEqual(self.client.post("/api/query",json={"dataset_id":"demo_pricing","sql":"SELECT 1"}).status_code,400)
    def test_revoking_user_invalidates_existing_session(self):
        admin=self.setup();viewer=self.create_user("viewer")
        self.login("viewer@example.com");token=self.client.cookies.get(COOKIE)
        self.login("admin@example.com")
        self.assertEqual(self.client.patch("/api/auth/users/"+viewer["id"],json={"disabled":True}).status_code,200)
        self.assertIsNone(self.app.state.auth.session_user(token))
        self.assertEqual(self.client.patch("/api/auth/users/"+admin["id"],json={"disabled":True}).status_code,400)

    def test_admin_password_reset_revokes_sessions_and_preserves_password_spaces(self):
        self.setup();viewer=self.create_user("viewer")
        self.login("viewer@example.com");token=self.client.cookies.get(COOKIE)
        self.login("admin@example.com")
        password="  new literal password  "
        response=self.client.patch("/api/auth/users/"+viewer["id"],json={"password":password})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()["password_reset"])
        self.assertNotIn(password,response.text)
        self.assertIsNone(self.app.state.auth.session_user(token))
        self.assertEqual(self.client.post("/api/auth/login",json={"email":"viewer@example.com","password":password.strip()}).status_code,401)
        self.assertEqual(self.client.post("/api/auth/login",json={"email":"viewer@example.com","password":password}).status_code,200)
    def test_local_only_blocks_cloud_and_cross_origin_writes(self):
        self.setup();self.settings.local_only=True
        for body in [{"agent_mode":"live"},{"decision_mode":"openai_decisions"}]:
            r=self.client.post("/api/analyses",json={"question":"Analyze our sales please","dataset_id":"demo_pricing",**body})
            self.assertEqual(r.status_code,400)
            self.assertIn("local-only",r.text)
        self.assertEqual(self.client.post("/api/auth/logout",json={},headers={"Origin":"https://evil.example"}).status_code,403)
    def test_background_upload_is_durable_and_pollable(self):
        self.setup()
        r=self.client.post("/api/uploads",files={"file":("business.csv",b"product,revenue\nA,42\n","text/csv")},data={"background":"true"})
        self.assertEqual(r.status_code,202,r.text);task_id=r.json()["task_id"]
        Worker(self.app.state.db,self.settings).run_once()
        result=self.client.get("/api/tasks/"+task_id).json()
        self.assertEqual(result["status"],"completed",result)
        self.assertTrue(result["result"])
    def test_passwords_hash_randomly_and_compare(self):
        a=password_hash(self.password);b=password_hash(self.password)
        self.assertNotEqual(a,b)
        self.assertTrue(password_matches(self.password,a))
        self.assertFalse(password_matches("incorrect",a))
        self.assertFalse(password_matches(self.password,"invalid"))


class LegacyMigrationTests(unittest.TestCase):
    def test_copy_migration_preserves_source_and_rejects_existing_target(self):
        from app.migrate import migrate_sqlite
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"source.sqlite3";target=Path(directory)/"target.sqlite3"
            db=Database(source);db.initialize()
            dataset=db.import_csv("Original","a,b\n1,2\n")
            original=db.save_upload("source.csv",b"a,b\n1,2\n",digest(b"a,b\n1,2\n"),None,"General","native","none")
            run_id=db.create_run({"question":"Migration sample","dataset_id":dataset["id"]})
            db.finish_run(run_id,{"answer":"Preserved"});db.review(run_id,"approved","Reviewer","")
            before=source.read_bytes()
            self.assertFalse(migrate_sqlite(source,target)["applied"])
            self.assertFalse(target.exists())
            self.assertTrue(migrate_sqlite(source,target,apply=True)["applied"])
            self.assertEqual(source.read_bytes(),before)
            migrated=Database(target)
            self.assertEqual(migrated.approved_run(run_id)["result"]["answer"],"Preserved")
            self.assertEqual(migrated.upload(original)["content"],b"a,b\n1,2\n")
            with self.assertRaises(ValueError):migrate_sqlite(source,target,apply=True)

    def test_old_approved_report_survives_additive_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"legacy.sqlite3"
            with sqlite3.connect(path) as con:
                con.executescript("CREATE TABLE runs(id TEXT PRIMARY KEY,created_at TEXT,status TEXT,request_json TEXT,result_json TEXT,events_json TEXT DEFAULT '[]',error TEXT); CREATE TABLE reviews(id TEXT PRIMARY KEY,run_id TEXT UNIQUE,decision TEXT,reviewer TEXT,notes TEXT,created_at TEXT);")
                con.execute("INSERT INTO runs(id,created_at,status,request_json,result_json) VALUES ('run_old','2020','approved','{}',?)",(json.dumps({"legacy":"report"}),))
                con.execute("INSERT INTO reviews VALUES ('review_old','run_old','approved','Original reviewer','','2020')")
            db=Database(path);db.initialize();db.initialize()
            run=db.approved_run("run_old")
            self.assertEqual(run["result"],{"legacy":"report"})
            self.assertEqual(run["review"]["reviewer"],"Original reviewer")
            self.assertIsNone(run["review"]["reviewer_id"])


if __name__=="__main__":unittest.main()
