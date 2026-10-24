"""Workspace-scoped persistence, durable jobs and restricted analytical queries.

SQLite supports local development; PostgreSQL is the shared deployment backend.
Public methods always scope resources. Raw connect() is reserved for internal
migrations and audit/queue infrastructure, never for user-supplied SQL.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import uuid

_scope = ContextVar("market_lens_workspace", default=None)
_identity = ContextVar("market_lens_identity", default=None)


def now():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex[:20]


def identifier(value):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value):
        raise ValueError("Invalid SQL identifier.")
    return '"' + value + '"'


def digest(value):
    data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(data).hexdigest()


class _PGConnection:
    """Small DB-API adapter for the parameterized internal queries shared with SQLite."""
    def __init__(self, con):
        self.raw = con

    def execute(self, statement, params=()):
        return self.raw.execute(statement.replace("?", "%s"), params)

    def executemany(self, statement, params):
        with self.raw.cursor() as cur:
            cur.executemany(statement.replace("?", "%s"), params)

    def executescript(self, sql):
        for statement in sql.split(";"):
            if statement.strip():
                self.execute(statement)


class Database:
    def __init__(self, path, *, workspace_id=None, user_id=None, analytics_url="", analytics_role=""):
        self.url = str(path)
        self.dialect = "postgresql" if self.url.startswith(("postgresql://", "postgres://", "postgresql+psycopg://")) else "sqlite"
        self.path = Path(path) if self.dialect == "sqlite" else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._workspace_id, self._user_id = workspace_id, user_id
        self.analytics_url, self.analytics_role = analytics_url, analytics_role
        if analytics_role:identifier(analytics_role)
        self._lease = None
        self._transaction_connection = None

    @property
    def workspace_id(self):
        return self._workspace_id or _scope.get() or "default"

    @property
    def current_user_id(self):
        return self._user_id or _identity.get()

    def scoped(self, workspace_id=None, user_id=None):
        result = Database(self.url, workspace_id=workspace_id or self.workspace_id,
                          user_id=user_id or self.current_user_id,
                          analytics_url=self.analytics_url,analytics_role=self.analytics_role)
        result._lease = self._lease
        result._transaction_connection = self._transaction_connection
        return result

    scope = scoped

    @contextmanager
    def context(self, workspace_id, user_id=None):
        s, u = _scope.set(workspace_id), _identity.set(user_id)
        try:
            yield self
        finally:
            _scope.reset(s)
            _identity.reset(u)

    @contextmanager
    def connect(self):
        if self._transaction_connection is not None:
            yield self._transaction_connection
            return
        if self.dialect == "postgresql":
            import psycopg
            from psycopg.rows import dict_row
            con = psycopg.connect(self.url.replace("postgresql+psycopg://", "postgresql://"), row_factory=dict_row, connect_timeout=10)
            adapter = _PGConnection(con)
        else:
            con = sqlite3.connect(self.path, timeout=30)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys = ON")
            adapter = con
        try:
            yield adapter
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _lock(self, con):
        if self.dialect == "sqlite" and not con.in_transaction:
            con.execute("BEGIN IMMEDIATE")

    @contextmanager
    def transaction(self,lock_key=None):
        """Reuse a scoped connection for an atomic multi-method mutation."""
        with self.connect() as con:
            self._lock(con)
            self._check_lease(con)
            if lock_key and self.dialect=="postgresql":
                con.execute("SELECT pg_advisory_xact_lock(?)",(int(digest((self.workspace_id,lock_key))[:15],16),))
            scoped=self.scoped()
            scoped._transaction_connection=con
            yield scoped

    def _columns(self, con, table):
        if self.dialect == "sqlite":
            return {r["name"] for r in con.execute(f"PRAGMA table_info({identifier(table)})")}
        return {r["column_name"] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=?", (table,))}

    def initialize(self):
        with self.connect() as con:
            if self.dialect == "sqlite":
                con.execute("PRAGMA journal_mode = WAL")
                self._lock(con)
            else:
                con.execute("SELECT pg_advisory_xact_lock(21112026)")
            schema = """
                CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, table_name TEXT NOT NULL UNIQUE,
                    columns_json TEXT NOT NULL, row_count INTEGER NOT NULL,
                    synthetic INTEGER NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL,
                    content TEXT NOT NULL, dataset_id TEXT REFERENCES datasets(id),
                    synthetic INTEGER NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                    ordinal INTEGER NOT NULL, content TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,
                    request_json TEXT NOT NULL, result_json TEXT,
                    events_json TEXT NOT NULL DEFAULT '[]', error TEXT);
                CREATE TABLE IF NOT EXISTS reviews (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
                    decision TEXT NOT NULL, reviewer TEXT NOT NULL, notes TEXT NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS benchmarks (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, result_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS uploads (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL, sha256 TEXT NOT NULL,
                    scope TEXT, category TEXT NOT NULL, method TEXT NOT NULL,
                    vision_provider TEXT NOT NULL, content BLOB NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                    password_hash TEXT NOT NULL, role TEXT NOT NULL, workspace_id TEXT NOT NULL,
                    disabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    expires_at DOUBLE PRECISION NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS auth_attempts (
                    key_hash TEXT PRIMARY KEY, attempts INTEGER NOT NULL, reset_at DOUBLE PRECISION NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, workspace_id TEXT NOT NULL, owner_id TEXT,
                    payload_json TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT, error TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
                    worker_id TEXT, lease_token TEXT, lease_expires DOUBLE PRECISION,
                    available_at DOUBLE PRECISION NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    idempotency_key TEXT, UNIQUE(workspace_id,kind,idempotency_key));
                CREATE TABLE IF NOT EXISTS app_records (
                    workspace_id TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
                    payload_json TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY (workspace_id,kind,id));
                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, user_id TEXT,
                    action TEXT NOT NULL, resource_id TEXT, details_json TEXT NOT NULL, created_at TEXT NOT NULL);
            """
            if self.dialect == "postgresql":
                schema = schema.replace("BLOB", "BYTEA")
            for statement in schema.split(";"):
                if statement.strip():
                    con.execute(statement)
            additions = {
                "datasets": {"file_id": "TEXT", "workspace_id": "TEXT NOT NULL DEFAULT 'default'", "version_hash": "TEXT NOT NULL DEFAULT ''", "quality_json": "TEXT NOT NULL DEFAULT '{}'", "deleted": "INTEGER NOT NULL DEFAULT 0"},
                "documents": {"file_id": "TEXT", "category": "TEXT NOT NULL DEFAULT 'General'", "extraction_method": "TEXT NOT NULL DEFAULT 'text'", "warnings_json": "TEXT NOT NULL DEFAULT '[]'", "workspace_id": "TEXT NOT NULL DEFAULT 'default'", "version_hash": "TEXT NOT NULL DEFAULT ''", "deleted": "INTEGER NOT NULL DEFAULT 0"},
                "chunks": {"location": "TEXT NOT NULL DEFAULT ''"},
                "uploads": {"workspace_id": "TEXT NOT NULL DEFAULT 'default'"},
                "runs": {"workspace_id": "TEXT NOT NULL DEFAULT 'default'", "owner_id": "TEXT", "result_sha256": "TEXT", "source_snapshot_json": "TEXT NOT NULL DEFAULT '{}'", "idempotency_key": "TEXT"},
                "reviews": {"reviewer_id": "TEXT", "result_sha256": "TEXT"},
                "benchmarks": {"workspace_id": "TEXT NOT NULL DEFAULT 'default'"},
            }
            for table, columns in additions.items():
                existing = self._columns(con, table)
                for name, definition in columns.items():
                    if name not in existing:
                        con.execute(f"ALTER TABLE {identifier(table)} ADD COLUMN {identifier(name)} {definition}")
            con.execute("INSERT INTO workspaces(id,name,created_at) VALUES ('default','Market Lens',?) ON CONFLICT(id) DO NOTHING", (now(),))
            con.execute("CREATE INDEX IF NOT EXISTS tasks_dispatch ON tasks(status,available_at,lease_expires)")
            con.execute("CREATE INDEX IF NOT EXISTS runs_workspace ON runs(workspace_id,created_at)")
            con.execute("CREATE UNIQUE INDEX IF NOT EXISTS runs_idempotency ON runs(workspace_id,idempotency_key)")
            con.execute("CREATE INDEX IF NOT EXISTS documents_workspace ON documents(workspace_id,deleted)")
            con.execute("CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at)")
            con.execute("INSERT INTO schema_migrations(version,applied_at) VALUES (2,?) ON CONFLICT(version) DO NOTHING", (now(),))
            # Legacy reports retain their exact bytes and gain an integrity hash;
            # reviewers remain explicitly self-reported for pre-auth history.
            for row in con.execute("SELECT id,result_json FROM runs WHERE result_json IS NOT NULL AND result_sha256 IS NULL").fetchall():
                result_hash=digest(json.loads(row["result_json"]))
                con.execute("UPDATE runs SET result_sha256=? WHERE id=?",(result_hash,row["id"]))
                con.execute("UPDATE reviews SET result_sha256=? WHERE run_id=? AND result_sha256 IS NULL",(result_hash,row["id"]))
            for row in con.execute("SELECT id,content FROM documents WHERE version_hash=''").fetchall():
                con.execute("UPDATE documents SET version_hash=? WHERE id=?",(digest(row["content"].encode()),row["id"]))
            for row in con.execute("SELECT id,table_name,columns_json FROM datasets WHERE version_hash=''").fetchall():
                data=[dict(r) for r in con.execute(f"SELECT * FROM {identifier(row['table_name'])}")]
                con.execute("UPDATE datasets SET version_hash=? WHERE id=?",(digest({"columns":json.loads(row["columns_json"]),"rows":data}),row["id"]))
            if self.dialect=="postgresql" and self.analytics_role:
                for row in con.execute("SELECT table_name FROM datasets").fetchall():
                    con.execute(f"GRANT SELECT ON {identifier(row['table_name'])} TO {identifier(self.analytics_role)}")
            # Adopt interrupted legacy analyses into the durable queue exactly once.
            for row in con.execute("SELECT id,workspace_id,owner_id FROM runs WHERE status IN ('queued','running')").fetchall():
                if not con.execute("SELECT id FROM tasks WHERE kind='analysis' AND workspace_id=? AND idempotency_key=?", (row["workspace_id"], row["id"])).fetchone():
                    self._insert_task(con, "analysis", {"run_id": row["id"]}, row["workspace_id"], row["owner_id"], 3, row["id"])

    def all_workspace_ids(self):
        with self.connect() as con:
            return [r["id"] for r in con.execute("SELECT id FROM workspaces ORDER BY id")]

    def audit(self, action, resource_id=None, details=None):
        with self.connect() as con:
            con.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?,?)", (uid("audit"), self.workspace_id, self.current_user_id, action, resource_id, json.dumps(details or {}), now()))

    def import_csv(self, name, content, synthetic=False, dataset_id=None, file_id=None):
        if len(content.encode("utf-8")) > 2_000_000:
            raise ValueError("CSV exceeds the 2 MB limit.")
        reader = csv.reader(io.StringIO(content.lstrip("\ufeff")))
        try:
            raw_headers = next(reader)
        except StopIteration:
            raise ValueError("CSV is empty.")
        if not 1 <= len(raw_headers) <= 40:
            raise ValueError("CSV must have 1–40 columns.")
        headers = []
        for index, value in enumerate(raw_headers):
            column = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
            if not value.strip():
                raise ValueError("CSV contains an empty column name.")
            if not column:
                column = f"col_{index+1}"
            if not column[0].isalpha():
                column = "col_" + column
            column = column[:63]
            if column in headers:
                raise ValueError("Column names must be unique after normalization.")
            headers.append(column)
        rows = []
        for line, row in enumerate(reader, 2):
            if not row or all(not x.strip() for x in row):
                continue
            if len(row) != len(headers):
                raise ValueError(f"Row {line} has {len(row)} cells; expected {len(headers)}.")
            rows.append([x.strip() for x in row])
            if len(rows) > 10000:
                raise ValueError("CSV exceeds the 10,000-row import limit. Split the file.")
        if not rows:
            raise ValueError("CSV must contain at least one data row.")
        types = []
        for index in range(len(headers)):
            values = [row[index] for row in rows if row[index] != ""]
            numeric = bool(values)
            for value in values:
                try:
                    numeric = numeric and math.isfinite(float(value))
                except ValueError:
                    numeric = False
            types.append("REAL" if numeric else "TEXT")
        converted = [tuple(None if v == "" else float(v) if types[i] == "REAL" else v for i, v in enumerate(row)) for row in rows]
        dataset_id = dataset_id or uid("ds")
        table = "data_" + dataset_id
        columns = [{"name": h, "type": t, **({"label": raw} if any(ord(c)>127 for c in raw) else {})} for h, t, raw in zip(headers, types, raw_headers)]
        quality = {"missing_cells": sum(v is None for row in converted for v in row), "duplicate_rows": len(rows)-len(set(converted)), "column_missing": {h: sum(row[i] is None for row in converted) for i,h in enumerate(headers)}}
        with self.connect() as con:
            self._check_lease(con)
            con.execute(f"CREATE TABLE {identifier(table)} (" + ", ".join(f"{identifier(h)} {'DOUBLE PRECISION' if self.dialect == 'postgresql' and t == 'REAL' else t}" for h,t in zip(headers,types)) + ")")
            con.executemany(f"INSERT INTO {identifier(table)} VALUES (" + ",".join("?" for _ in headers) + ")", converted)
            if self.dialect=="postgresql" and self.analytics_role:
                con.execute(f"GRANT SELECT ON {identifier(table)} TO {identifier(self.analytics_role)}")
            con.execute("INSERT INTO datasets (id,name,table_name,columns_json,row_count,synthetic,created_at,file_id,workspace_id,version_hash,quality_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (dataset_id,name,table,json.dumps(columns),len(rows),int(synthetic),now(),file_id,self.workspace_id,digest(content.encode()),json.dumps(quality)))
        return self.dataset(dataset_id)

    def dataset(self, dataset_id):
        with self.connect() as con:
            row = con.execute("SELECT * FROM datasets WHERE id=? AND workspace_id=? AND deleted=0", (dataset_id,self.workspace_id)).fetchone()
        if row is None:
            raise ValueError("Dataset not found.")
        result = dict(row)
        result["columns"] = json.loads(result.pop("columns_json"))
        result["quality"] = json.loads(result.pop("quality_json"))
        result["synthetic"] = bool(result["synthetic"])
        return result

    def datasets(self):
        with self.connect() as con:
            ids = [r["id"] for r in con.execute("SELECT id FROM datasets WHERE workspace_id=? AND deleted=0 ORDER BY created_at,name", (self.workspace_id,))]
        return [self.dataset(i) for i in ids]

    def query(self, sql, dataset_id, parameters=(), *, dataset_ids=None):
        ids = list(dict.fromkeys([dataset_id]+list(dataset_ids or [])))
        datasets = [self.dataset(i) for i in ids if i]
        if not datasets:
            raise ValueError("Select a dataset.")
        if len(sql) > 8000 or not re.match(r"^\s*(SELECT|WITH)\b", sql, re.I):
            raise ValueError("Only one read-only SELECT query is allowed.")
        allowed = {d["table_name"] for d in datasets}
        if self.dialect == "postgresql":
            return self._postgres_query(sql, dataset_id, allowed, parameters)
        con = sqlite3.connect(self.path.resolve().as_uri()+"?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        functions = {"sum","avg","count","min","max","round","abs","coalesce","nullif","lower","upper","length","substr","strftime","date","ifnull","like","total","iif","trim","replace","cast","row_number","rank","dense_rank","lag","lead","first_value","last_value","nth_value","ntile","cume_dist","percent_rank"}
        def authorize(action, arg1, arg2, db, trigger):
            if action == sqlite3.SQLITE_READ:
                return sqlite3.SQLITE_OK if arg1 in allowed else sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_FUNCTION:
                return sqlite3.SQLITE_OK if (arg2 or "").lower() in functions else sqlite3.SQLITE_DENY
            if action in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_RECURSIVE):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        con.set_authorizer(authorize)
        deadline = time.monotonic()+2
        con.set_progress_handler(lambda: int(time.monotonic()>deadline),1000)
        try:
            cursor = con.execute(sql,parameters)
            records = cursor.fetchmany(201)
            rows = [{k: None if isinstance(v,float) and not math.isfinite(v) else v for k,v in dict(r).items()} for r in records[:200]]
            return {"sql":sql,"parameters":list(parameters),"columns":[c[0] for c in cursor.description],"rows":rows,"truncated":len(records)>200,"dataset_id":dataset_id}
        except sqlite3.Error as exc:
            raise ValueError(f"Query rejected or invalid: {exc}") from exc
        finally:
            con.close()

    def _postgres_query(self, sql, dataset_id, allowed, parameters):
        import sqlglot
        from sqlglot import exp
        from sqlglot.optimizer.scope import traverse_scope
        import psycopg
        from decimal import Decimal
        try:
            statements = sqlglot.parse(sql,read="postgres")
            if len(statements)!=1 or not isinstance(statements[0],exp.Query):
                raise ValueError("Only one read-only query is allowed.")
            tree = statements[0]
            forbidden = (exp.Insert,exp.Update,exp.Delete,exp.Create,exp.Drop,exp.Command,exp.Into,exp.Lock)
            if any(isinstance(n,forbidden) for n in tree.walk()):
                raise ValueError("Query contains a forbidden operation.")
            for scope in traverse_scope(tree):
                for table in scope.sources.values():
                    if isinstance(table,exp.Table) and (table.catalog or table.db or table.name not in allowed):
                        raise ValueError("Queries may only read selected data tables.")
            for node in tree.find_all(exp.Dot):
                if isinstance(node.expression,exp.Func):
                    raise ValueError("Qualified function calls are not allowed.")
            for cast in tree.find_all(exp.Cast):
                if cast.args["to"].sql(dialect="postgres").split("(")[0].upper() not in {"INT","INTEGER","BIGINT","SMALLINT","DOUBLE PRECISION","REAL","FLOAT","DECIMAL","NUMERIC","VARCHAR","TEXT","BOOLEAN","DATE","TIMESTAMP","TIMESTAMPTZ","INTERVAL"}:
                    raise ValueError("This cast type is not allowed.")
            functions = {"sum","avg","count","min","max","round","abs","coalesce","nullif","lower","upper","length","substring","substr","date","trim","replace","cast","row_number","rank","dense_rank","extract","date_trunc","to_char","greatest","least","current_date","concat","concat_ws","case","if","lag","lead","first_value","last_value","nth_value","ntile","cume_dist","percent_rank","count_if","filter","or","and","not","stddev","stddev_samp","stddev_pop","variance","var_samp","var_pop"}
            for function in tree.find_all(exp.Func):
                name = function.name.lower() if isinstance(function,exp.Anonymous) else function.sql_name().lower()
                if name not in functions:
                    raise ValueError(f"Function {name} is not allowed in analytical queries.")
            analytical=Database(self.analytics_url) if self.analytics_url else self
            with analytical.connect() as con:
                con.execute("SET TRANSACTION READ ONLY")
                con.execute("SET LOCAL statement_timeout = '2000ms'")
                con.execute("SET LOCAL lock_timeout = '1000ms'")
                # sqlglot normalizes dialect quirks; parameter binding remains DB-API.
                # PostgreSQL round(value, places) requires numeric, while imported
                # business numbers use double precision. Preserve baseline SQL.
                for rounding in tree.find_all(exp.Round):
                    if rounding.args.get("decimals") is not None:
                        rounding.set("this",exp.Cast(this=rounding.this.copy(),to=exp.DataType.build("DECIMAL")))
                query = tree.sql(dialect="postgres")
                if parameters:
                    # Preserve question marks and percent signs in string
                    # literals; the shared internal adapter must not rewrite
                    # arbitrary analytical SQL by string substitution.
                    bound_tree=tree.copy()
                    markers=[]
                    for node in list(bound_tree.find_all(exp.Placeholder)):
                        marker="ml_bind_"+uuid.uuid4().hex
                        markers.append(marker)
                        node.replace(exp.Var(this=marker))
                    if len(markers)!=len(parameters):
                        raise ValueError("Query parameter count does not match placeholders.")
                    executed=bound_tree.sql(dialect="postgres").replace("%","%%")
                    for marker in markers:executed=executed.replace(marker,"%s")
                    cursor=con.raw.execute(executed,parameters)
                else:
                    cursor=con.raw.execute(query)
                records = cursor.fetchmany(201)
                def clean(v):
                    if isinstance(v,Decimal): v=float(v)
                    if isinstance(v,float) and not math.isfinite(v): return None
                    if isinstance(v,(date,datetime)): return v.isoformat()
                    if isinstance(v,timedelta): return str(v)
                    return v
                rows=[{k:clean(v) for k,v in row.items()} for row in records[:200]]
                return {"sql":query,"parameters":list(parameters),"columns":[c.name for c in cursor.description],"rows":rows,"truncated":len(records)>200,"dataset_id":dataset_id}
        except (sqlglot.errors.ParseError,psycopg.Error) as exc:
            raise ValueError("Query rejected or invalid: "+str(exc).split("\n")[0]) from exc

    def add_document(self,title,content,source,dataset_id=None,synthetic=False,*,segments=None,file_id=None,category="General",extraction_method="text",warnings=None):
        if not content.strip() or len(content)>2_000_000:
            raise ValueError("Document must contain 1–2,000,000 characters.")
        if dataset_id: self.dataset(dataset_id)
        doc_id=uid("doc")
        chunks=[]
        for segment in segments or [{"content":content,"location":"Document"}]:
            text=segment["content"]; words=text.split()
            if words and max(map(len,words))<1200:
                chunks.extend((" ".join(words[i:i+180]),segment["location"]) for i in range(0,len(words),150))
            else:
                chunks.extend((text[i:i+2400],segment["location"]) for i in range(0,len(text),2000))
        with self.connect() as con:
            self._check_lease(con)
            con.execute("INSERT INTO documents(id,title,source,content,dataset_id,synthetic,created_at,file_id,category,extraction_method,warnings_json,workspace_id,version_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",(doc_id,title,source,content,dataset_id,int(synthetic),now(),file_id,category,extraction_method,json.dumps(warnings or []),self.workspace_id,digest(content.encode())))
            con.executemany("INSERT INTO chunks(id,document_id,ordinal,content,location) VALUES (?,?,?,?,?)",[(f"{doc_id}_{i+1}",doc_id,i+1,chunk,location) for i,(chunk,location) in enumerate(chunks)])
        return {"id":doc_id,"title":title,"chunk_count":len(chunks)}

    def document(self,document_id):
        with self.connect() as con:
            row=con.execute("SELECT * FROM documents WHERE id=? AND workspace_id=? AND deleted=0",(document_id,self.workspace_id)).fetchone()
        if not row: raise ValueError("Document not found.")
        result=dict(row);result["warnings"]=json.loads(result.pop("warnings_json"))
        return result

    def documents(self):
        with self.connect() as con:
            return [dict(r) for r in con.execute("""SELECT d.id,d.title,d.source,d.dataset_id,d.synthetic,d.created_at,d.file_id,d.category,d.extraction_method,d.warnings_json,d.version_hash,d.workspace_id,
                (SELECT COUNT(*) FROM chunks c WHERE c.document_id=d.id) AS chunk_count
                FROM documents d WHERE d.workspace_id=? AND d.deleted=0 ORDER BY d.created_at""",(self.workspace_id,))]

    def chunks(self,dataset_id=None,document_ids=None):
        if dataset_id: self.dataset(dataset_id)
        if not dataset_id and not document_ids: return []
        if document_ids:
            for doc_id in document_ids: self.document(doc_id)
        with self.connect() as con:
            query="SELECT c.*,d.title,d.source,d.synthetic,d.file_id,d.category,d.extraction_method,d.version_hash,d.workspace_id FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.workspace_id=? AND d.deleted=0"
            params=[self.workspace_id]
            if document_ids:
                query+=" AND d.id IN ("+",".join("?" for _ in document_ids)+")";params.extend(document_ids)
                if dataset_id: query+=" AND (d.dataset_id=? OR d.dataset_id IS NULL)";params.append(dataset_id)
            elif dataset_id:
                query+=" AND (d.dataset_id=? OR d.dataset_id IS NULL)";params.append(dataset_id)
            return [dict(r) for r in con.execute(query,params)]

    def delete_document(self,document_id):
        self.document(document_id)
        with self.connect() as con:
            con.execute("UPDATE documents SET deleted=1 WHERE id=? AND workspace_id=?",(document_id,self.workspace_id))
        self.audit("document.deleted",document_id)

    def delete_dataset(self,dataset_id):
        self.dataset(dataset_id)
        with self.connect() as con:
            con.execute("UPDATE datasets SET deleted=1 WHERE id=? AND workspace_id=?",(dataset_id,self.workspace_id))
            con.execute("UPDATE documents SET deleted=1 WHERE dataset_id=? AND workspace_id=?",(dataset_id,self.workspace_id))
        self.audit("dataset.deleted",dataset_id)

    def upload_duplicate(self,digest,scope,vision_provider):
        with self.connect() as con:
            # PostgreSQL cannot infer the type of an untyped NULL parameter in
            # `? IS NULL`; branch the predicate and keep values parameterized.
            predicate="scope IS NULL" if scope is None else "scope=?"
            params=(digest,vision_provider,self.workspace_id) if scope is None else (digest,scope,vision_provider,self.workspace_id)
            row=con.execute("SELECT id FROM uploads WHERE sha256=? AND "+predicate+" AND vision_provider=? AND workspace_id=?",params).fetchone()
            return row["id"] if row else None

    def save_upload(self,filename,content,digest,scope,category,method,vision_provider):
        if scope: self.dataset(scope)
        file_id=uid("file")
        with self.connect() as con:
            self._check_lease(con)
            con.execute("INSERT INTO uploads(id,filename,sha256,scope,category,method,vision_provider,content,created_at,workspace_id) VALUES (?,?,?,?,?,?,?,?,?,?)",(file_id,filename,digest,scope,category,method,vision_provider,content,now(),self.workspace_id))
        return file_id

    def upload(self,file_id):
        with self.connect() as con:
            row=con.execute("SELECT * FROM uploads WHERE id=? AND workspace_id=?",(file_id,self.workspace_id)).fetchone()
        if not row: raise ValueError("Original file not found.")
        result=dict(row);result["content"]=bytes(result["content"])
        return result

    def source_snapshot(self,request):
        snapshot={"datasets":{},"documents":{}}
        dataset_ids=list(dict.fromkeys(([request["dataset_id"]] if request.get("dataset_id") else [])+request.get("dataset_ids",[])))
        for dataset_id in dataset_ids:
            d=self.dataset(dataset_id);snapshot["datasets"][dataset_id]=d["version_hash"]
        document_ids=request.get("document_ids")
        if len(dataset_ids)>1:
            document_ids=document_ids or [d["id"] for d in self.documents() if d["dataset_id"] in {None,*dataset_ids}]
            chunks=self.chunks(None,document_ids)
        else:
            chunks=self.chunks(request.get("dataset_id"),document_ids)
        for c in chunks: snapshot["documents"][c["document_id"]]=c["version_hash"]
        snapshot["dictionary_definitions"]={dataset_id:self.get_record("dictionaries",dataset_id) for dataset_id in dataset_ids}
        snapshot["dictionaries"]={dataset_id:digest(definition or {}) for dataset_id,definition in snapshot["dictionary_definitions"].items()}
        return snapshot

    def _insert_task(self,con,kind,payload,workspace_id,owner_id,max_attempts,idempotency_key):
        task_id=uid("task"); stamp=now()
        con.execute("INSERT INTO tasks(id,kind,workspace_id,owner_id,payload_json,status,max_attempts,available_at,created_at,updated_at,idempotency_key) VALUES (?,?,?,?,?,'queued',?,?,?,?,?)",(task_id,kind,workspace_id,owner_id,json.dumps(payload),max_attempts,time.time(),stamp,stamp,idempotency_key))
        return task_id

    def create_run(self,request,idempotency_key=None):
        run_id=uid("run");snapshot=self.source_snapshot(request)
        request={**request,"_source_dictionaries":snapshot["dictionary_definitions"]}
        with self.connect() as con:
            self._lock(con);self._check_lease(con)
            if idempotency_key:
                if self.dialect=="postgresql":
                    con.execute("SELECT pg_advisory_xact_lock(?)",(int(digest((self.workspace_id,idempotency_key))[:15],16),))
                existing=con.execute("SELECT id FROM runs WHERE workspace_id=? AND idempotency_key=?",(self.workspace_id,idempotency_key)).fetchone()
                if existing:return existing["id"]
            con.execute("INSERT INTO runs(id,created_at,status,request_json,workspace_id,owner_id,source_snapshot_json,idempotency_key) VALUES (?,?,'queued',?,?,?,?,?)",(run_id,now(),json.dumps(request),self.workspace_id,self.current_user_id,json.dumps(snapshot),idempotency_key))
            self._insert_task(con,"analysis",{"run_id":run_id},self.workspace_id,self.current_user_id,3,run_id)
        return run_id

    def _check_lease(self,con):
        if self._lease:
            task_id,token=self._lease
            suffix=" FOR UPDATE" if self.dialect=="postgresql" else ""
            row=con.execute("SELECT id FROM tasks WHERE id=? AND status='running' AND lease_token=? AND lease_expires>?"+suffix,(task_id,token,time.time())).fetchone()
            if not row: raise ValueError("Task ownership expired or the task was cancelled.")

    def event(self,run_id,stage,message):
        with self.connect() as con:
            self._lock(con); self._check_lease(con)
            suffix=" FOR UPDATE" if self.dialect=="postgresql" else ""
            row=con.execute("SELECT events_json,status FROM runs WHERE id=? AND workspace_id=?"+suffix,(run_id,self.workspace_id)).fetchone()
            if not row or row["status"] not in {"queued","running"}: raise ValueError("Analysis is no longer running.")
            events=json.loads(row["events_json"]);events.append({"at":now(),"stage":stage,"message":message})
            con.execute("UPDATE runs SET events_json=?,status='running' WHERE id=?",(json.dumps(events),run_id))

    def finish_run(self,run_id,result=None,error=None):
        with self.connect() as con:
            self._lock(con);self._check_lease(con)
            cursor=con.execute("UPDATE runs SET status=?,result_json=?,result_sha256=?,error=? WHERE id=? AND workspace_id=? AND status IN ('queued','running')",("failed" if error else "pending_review",json.dumps(result) if result is not None else None,digest(result) if result is not None else None,error,run_id,self.workspace_id))
            if cursor.rowcount!=1: raise ValueError("Analysis cannot be overwritten after completion or cancellation.")

    def run(self,run_id):
        with self.connect() as con:
            row=con.execute("SELECT * FROM runs WHERE id=? AND workspace_id=?",(run_id,self.workspace_id)).fetchone()
            if not row: raise ValueError("Analysis not found.")
            review=con.execute("SELECT * FROM reviews WHERE run_id=?",(run_id,)).fetchone()
        result=dict(row)
        for name in ("request","result","events","source_snapshot"):
            result[name]=json.loads(result.pop(name+"_json") or "null")
        result["review"]=dict(review) if review else None
        return result

    def runs(self):
        with self.connect() as con:
            rows=con.execute("SELECT id,created_at,status,request_json,owner_id FROM runs WHERE workspace_id=? ORDER BY created_at DESC LIMIT 200",(self.workspace_id,)).fetchall()
        return [{"id":r["id"],"created_at":r["created_at"],"status":r["status"],"owner_id":r["owner_id"],"question":json.loads(r["request_json"])["question"]} for r in rows]

    def review(self,run_id,decision,reviewer,notes):
        if decision not in {"approved","rejected"}: raise ValueError("Unknown review decision.")
        if decision=="rejected" and not notes.strip(): raise ValueError("A rejection requires review notes.")
        with self.connect() as con:
            self._lock(con)
            suffix=" FOR UPDATE" if self.dialect=="postgresql" else ""
            row=con.execute("SELECT status,result_sha256,result_json FROM runs WHERE id=? AND workspace_id=?"+suffix,(run_id,self.workspace_id)).fetchone()
            if not row: raise ValueError("Analysis not found.")
            if row["status"]!="pending_review": raise ValueError("Only an analysis awaiting review can be reviewed, once.")
            actual=digest(json.loads(row["result_json"]))
            if row["result_sha256"] and actual!=row["result_sha256"]: raise ValueError("Report integrity check failed.")
            con.execute("INSERT INTO reviews(id,run_id,decision,reviewer,notes,created_at,reviewer_id,result_sha256) VALUES (?,?,?,?,?,?,?,?)",(uid("review"),run_id,decision,reviewer,notes,now(),self.current_user_id,actual))
            con.execute("UPDATE runs SET status=?,result_sha256=? WHERE id=?",(decision,actual,run_id))
        self.audit("analysis."+decision,run_id,{"result_sha256":actual})
        return self.run(run_id)

    def approved_run(self,run_id):
        run=self.run(run_id)
        if run["status"]!="approved" or not run["review"]: raise ValueError("An analyst must approve this brief before export.")
        if digest(run["result"])!=run["review"]["result_sha256"]: raise ValueError("Approved report integrity check failed.")
        return run

    def save_benchmark(self,result):
        with self.connect() as con:
            con.execute("INSERT INTO benchmarks(id,created_at,result_json,workspace_id) VALUES (?,?,?,?)",(result["id"],result["created_at"],json.dumps(result),self.workspace_id))

    def latest_benchmark(self):
        with self.connect() as con:
            row=con.execute("SELECT result_json FROM benchmarks WHERE workspace_id=? ORDER BY created_at DESC LIMIT 1",(self.workspace_id,)).fetchone()
        return json.loads(row["result_json"]) if row else None

    def put_record(self,kind,record_id,payload):
        with self.connect() as con:
            self._check_lease(con)
            con.execute("INSERT INTO app_records(workspace_id,kind,id,payload_json,updated_at) VALUES (?,?,?,?,?) ON CONFLICT(workspace_id,kind,id) DO UPDATE SET payload_json=excluded.payload_json,updated_at=excluded.updated_at",(self.workspace_id,kind,record_id,json.dumps(payload),now()))
        return payload

    def get_record(self,kind,record_id):
        with self.connect() as con:
            row=con.execute("SELECT payload_json FROM app_records WHERE workspace_id=? AND kind=? AND id=?",(self.workspace_id,kind,record_id)).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def list_records(self,kind):
        with self.connect() as con:
            rows=con.execute("SELECT payload_json FROM app_records WHERE workspace_id=? AND kind=? ORDER BY updated_at DESC",(self.workspace_id,kind)).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def delete_record(self,kind,record_id):
        with self.connect() as con:
            return con.execute("DELETE FROM app_records WHERE workspace_id=? AND kind=? AND id=?",(self.workspace_id,kind,record_id)).rowcount>0

    def create_task(self,kind,payload,max_attempts=3,idempotency_key=None):
        if not 1<=max_attempts<=5: raise ValueError("Tasks must allow between one and five attempts.")
        with self.connect() as con:
            self._lock(con);self._check_lease(con)
            if idempotency_key:
                if self.dialect=="postgresql":
                    con.execute("SELECT pg_advisory_xact_lock(?)",(int(digest((self.workspace_id,kind,idempotency_key))[:15],16),))
                row=con.execute("SELECT id FROM tasks WHERE workspace_id=? AND kind=? AND idempotency_key=?",(self.workspace_id,kind,idempotency_key)).fetchone()
                if row: return row["id"]
            return self._insert_task(con,kind,payload,self.workspace_id,self.current_user_id,max_attempts,idempotency_key)

    def _task_dict(self,row):
        task=dict(row);task["payload"]=json.loads(task.pop("payload_json"));task["result"]=json.loads(task.pop("result_json") or "null")
        return task

    def task(self,task_id):
        with self.connect() as con:
            row=con.execute("SELECT * FROM tasks WHERE id=? AND workspace_id=?",(task_id,self.workspace_id)).fetchone()
        if not row: raise ValueError("Task not found.")
        task=self._task_dict(row);task.pop("lease_token",None)
        return task

    def tasks(self):
        with self.connect() as con:
            ids=[r["id"] for r in con.execute("SELECT id FROM tasks WHERE workspace_id=? ORDER BY created_at DESC LIMIT 200",(self.workspace_id,))]
        return [self.task(i) for i in ids]

    def claim_task(self,worker_id,lease_seconds=90):
        stamp=time.time()
        with self.connect() as con:
            self._lock(con)
            # Lease expiry means a worker can be replaced without touching completed records.
            con.execute("UPDATE tasks SET status=CASE WHEN attempts>=max_attempts THEN 'failed' ELSE 'queued' END, error='Worker lease expired; task recovered.',lease_token=NULL,worker_id=NULL,updated_at=? WHERE status='running' AND lease_expires<?",(now(),stamp))
            con.execute("UPDATE runs SET status='failed',error='Worker recovery attempts exhausted.' WHERE status IN ('queued','running') AND id IN (SELECT idempotency_key FROM tasks WHERE kind='analysis' AND status='failed')")
            suffix=" FOR UPDATE SKIP LOCKED" if self.dialect=="postgresql" else ""
            row=con.execute("SELECT * FROM tasks WHERE status='queued' AND available_at<=? ORDER BY created_at LIMIT 1"+suffix,(stamp,)).fetchone()
            if not row:return None
            token=uuid.uuid4().hex
            con.execute("UPDATE tasks SET status='running',attempts=attempts+1,worker_id=?,lease_token=?,lease_expires=?,updated_at=? WHERE id=?",(worker_id,token,stamp+lease_seconds,now(),row["id"]))
            task=self._task_dict(row);task.update(status="running",attempts=task["attempts"]+1,worker_id=worker_id,lease_token=token,lease_expires=stamp+lease_seconds)
            return task

    def heartbeat_task(self,task_id,lease_token,lease_seconds=90):
        with self.connect() as con:
            return con.execute("UPDATE tasks SET lease_expires=?,updated_at=? WHERE id=? AND lease_token=? AND status='running' AND lease_expires>?",(time.time()+lease_seconds,now(),task_id,lease_token,time.time())).rowcount==1

    def finish_task(self,task_id,lease_token,result=None,error=None,retryable=False):
        with self.connect() as con:
            self._lock(con)
            suffix=" FOR UPDATE" if self.dialect=="postgresql" else ""
            row=con.execute("SELECT attempts,max_attempts FROM tasks WHERE id=? AND workspace_id=? AND status='running' AND lease_token=? AND lease_expires>?"+suffix,(task_id,self.workspace_id,lease_token,time.time())).fetchone()
            if not row:return False
            retry=bool(error and retryable and row["attempts"]<row["max_attempts"])
            status="queued" if retry else "failed" if error else "completed"
            con.execute("UPDATE tasks SET status=?,result_json=?,error=?,lease_token=NULL,worker_id=NULL,lease_expires=NULL,available_at=?,updated_at=? WHERE id=?",(status,json.dumps(result) if result is not None else None,error,time.time()+min(2**row["attempts"],30) if retry else time.time(),now(),task_id))
        return True

    def cancel_task(self,task_id):
        task=self.task(task_id)
        with self.connect() as con:
            cursor=con.execute("UPDATE tasks SET status='cancelled',lease_token=NULL,updated_at=? WHERE id=? AND workspace_id=? AND status IN ('queued','running')",(now(),task_id,self.workspace_id))
            if cursor.rowcount!=1:raise ValueError("Only queued or running tasks can be cancelled.")
            if task["kind"]=="analysis":
                con.execute("UPDATE runs SET status='cancelled',error='Cancelled by user.' WHERE id=? AND workspace_id=? AND status IN ('queued','running')",(task["payload"]["run_id"],self.workspace_id))
        self.audit("task.cancelled",task_id)
        return self.task(task_id)

    def retry_task(self,task_id):
        task=self.task(task_id)
        if task["status"] not in {"failed","cancelled"}:raise ValueError("Only failed or cancelled tasks can be retried.")
        if task["kind"]=="analysis":
            original=self.run(task["payload"]["run_id"])
            request={**original["request"],"retry_of":original["id"]}
            return {"run_id":self.create_run(request)}
        return {"task_id":self.create_task(task["kind"],task["payload"],task["max_attempts"])}

    def analysis_task(self,run_id):
        self.run(run_id)
        with self.connect() as con:
            row=con.execute("SELECT id FROM tasks WHERE workspace_id=? AND kind='analysis' AND idempotency_key=?",(self.workspace_id,run_id)).fetchone()
        return self.task(row["id"]) if row else None
