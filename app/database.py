"""SQLite persistence and a tightly restricted read-only analytics connection."""
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import uuid


def now():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex[:12]


def identifier(value):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value):
        raise ValueError("Invalid SQL identifier.")
    return '"' + value + '"'


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def initialize(self):
        with self.connect() as con:
            con.execute("PRAGMA journal_mode = WAL")
            con.executescript("""
                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, table_name TEXT NOT NULL UNIQUE,
                    columns_json TEXT NOT NULL, row_count INTEGER NOT NULL,
                    synthetic INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL,
                    content TEXT NOT NULL, dataset_id TEXT REFERENCES datasets(id),
                    synthetic INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                    ordinal INTEGER NOT NULL, content TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,
                    request_json TEXT NOT NULL, result_json TEXT,
                    events_json TEXT NOT NULL DEFAULT '[]', error TEXT
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
                    decision TEXT NOT NULL, reviewer TEXT NOT NULL, notes TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS benchmarks (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, result_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS uploads (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL, sha256 TEXT NOT NULL,
                    scope TEXT, category TEXT NOT NULL, method TEXT NOT NULL,
                    vision_provider TEXT NOT NULL, content BLOB NOT NULL, created_at TEXT NOT NULL
                );
            """)
            # Additive migrations preserve existing demo data and review history.
            additions = {"datasets": {"file_id": "TEXT"},
                         "documents": {"file_id": "TEXT", "category": "TEXT NOT NULL DEFAULT 'General'",
                                       "extraction_method": "TEXT NOT NULL DEFAULT 'text'", "warnings_json": "TEXT NOT NULL DEFAULT '[]'"},
                         "chunks": {"location": "TEXT NOT NULL DEFAULT ''"}}
            for table, columns in additions.items():
                existing = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
                for name, definition in columns.items():
                    if name not in existing:
                        con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

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
                raise ValueError("CSV exceeds the 10,000-row MVP limit.")
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
        converted = [tuple(None if v == "" else float(v) if types[i] == "REAL" else v
                           for i, v in enumerate(row)) for row in rows]
        dataset_id = dataset_id or uid("ds")
        table = "data_" + dataset_id
        columns = [{"name": h, "type": t, **({"label": raw} if any(ord(c)>127 for c in raw) else {})}
                   for h, t, raw in zip(headers, types, raw_headers)]
        with self.connect() as con:
            con.execute(f"CREATE TABLE {identifier(table)} (" + ", ".join(
                f"{identifier(h)} {t}" for h, t in zip(headers, types)) + ")")
            con.executemany(f"INSERT INTO {identifier(table)} VALUES (" + ",".join("?" for _ in headers) + ")", converted)
            con.execute("INSERT INTO datasets (id,name,table_name,columns_json,row_count,synthetic,created_at,file_id) VALUES (?,?,?,?,?,?,?,?)",
                        (dataset_id, name, table, json.dumps(columns), len(rows), int(synthetic), now(), file_id))
        return self.dataset(dataset_id)

    def dataset(self, dataset_id):
        with self.connect() as con:
            row = con.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
        if row is None:
            raise ValueError("Dataset not found.")
        result = dict(row)
        result["columns"] = json.loads(result.pop("columns_json"))
        result["synthetic"] = bool(result["synthetic"])
        return result

    def datasets(self):
        with self.connect() as con:
            ids = [r[0] for r in con.execute("SELECT id FROM datasets ORDER BY created_at, name")]
        return [self.dataset(i) for i in ids]

    def query(self, sql, dataset_id, parameters=()):
        """Engine-enforced table isolation, read-only mode, time and output limits."""
        dataset = self.dataset(dataset_id)
        if len(sql) > 8000 or not re.match(r"^\s*(SELECT|WITH)\b", sql, re.I):
            raise ValueError("Only one read-only SELECT query is allowed.")
        con = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        allowed_functions = {
            "sum", "avg", "count", "min", "max", "round", "abs", "coalesce", "nullif",
            "lower", "upper", "length", "substr", "strftime", "date", "ifnull", "like",
            "total", "iif", "trim", "replace", "cast", "row_number", "rank", "dense_rank",
        }
        def authorize(action, arg1, arg2, db, trigger):
            if action == sqlite3.SQLITE_READ:
                return sqlite3.SQLITE_OK if arg1 == dataset["table_name"] else sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_FUNCTION:
                return sqlite3.SQLITE_OK if (arg2 or "").lower() in allowed_functions else sqlite3.SQLITE_DENY
            if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        con.set_authorizer(authorize)
        deadline = time.monotonic() + 2
        con.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        try:
            cursor = con.execute(sql, parameters)
            records = cursor.fetchmany(201)
            # sqlite can calculate infinities from user data; JSON must remain valid.
            rows = [{k: None if isinstance(v, float) and not math.isfinite(v) else v
                     for k, v in dict(r).items()} for r in records[:200]]
            return {"sql": sql, "parameters": list(parameters), "columns": [c[0] for c in cursor.description],
                    "rows": rows, "truncated": len(records) > 200, "dataset_id": dataset_id}
        except sqlite3.Error as exc:
            raise ValueError(f"Query rejected or invalid: {exc}") from exc
        finally:
            con.close()

    def add_document(self, title, content, source, dataset_id=None, synthetic=False, *, segments=None,
                     file_id=None, category="General", extraction_method="text", warnings=None):
        if not content.strip() or len(content) > 2_000_000:
            raise ValueError("Document must contain 1–2,000,000 characters.")
        if dataset_id:
            self.dataset(dataset_id)
        doc_id = uid("doc")
        chunks = []
        for segment in segments or [{"content": content, "location": "Document"}]:
            text = segment["content"]
            words = text.split()
            if words and max(map(len, words)) < 1200:
                chunks.extend((" ".join(words[i:i+180]), segment["location"]) for i in range(0, len(words), 150))
            else:
                chunks.extend((text[i:i+2400], segment["location"]) for i in range(0, len(text), 2000))
        with self.connect() as con:
            con.execute("INSERT INTO documents (id,title,source,content,dataset_id,synthetic,created_at,file_id,category,extraction_method,warnings_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (doc_id, title, source, content, dataset_id, int(synthetic), now(), file_id, category, extraction_method, json.dumps(warnings or [])))
            con.executemany("INSERT INTO chunks (id,document_id,ordinal,content,location) VALUES (?,?,?,?,?)",
                            [(f"{doc_id}_{i+1}", doc_id, i+1, chunk, location) for i, (chunk, location) in enumerate(chunks)])
        return {"id": doc_id, "title": title, "chunk_count": len(chunks)}

    def documents(self):
        with self.connect() as con:
            return [dict(r) for r in con.execute("""SELECT d.id, d.title, d.source, d.dataset_id,
                d.synthetic, d.created_at, d.file_id, d.category, d.extraction_method, d.warnings_json, COUNT(c.id) AS chunk_count FROM documents d
                LEFT JOIN chunks c ON c.document_id=d.id GROUP BY d.id ORDER BY d.created_at""")]

    def chunks(self, dataset_id=None, document_ids=None):
        with self.connect() as con:
            query = "SELECT c.*, d.title, d.source, d.synthetic, d.file_id, d.category, d.extraction_method FROM chunks c JOIN documents d ON d.id=c.document_id"
            params = []
            if document_ids:
                query += " WHERE d.id IN ("+",".join("?" for _ in document_ids)+")"
                params.extend(document_ids)
                if dataset_id:
                    query += " AND (d.dataset_id=? OR d.dataset_id IS NULL)"
                    params.append(dataset_id)
            elif dataset_id:
                query += " WHERE d.dataset_id=? OR d.dataset_id IS NULL"
                params.append(dataset_id)
            else:
                # Document-only runs require explicit selection; never mix every project.
                return []
            return [dict(r) for r in con.execute(query, params)]

    def upload_duplicate(self, digest, scope, vision_provider):
        with self.connect() as con:
            row = con.execute("SELECT id FROM uploads WHERE sha256=? AND scope IS ? AND vision_provider=?", (digest, scope, vision_provider)).fetchone()
            return row["id"] if row else None

    def save_upload(self, filename, content, digest, scope, category, method, vision_provider):
        file_id = uid("file")
        with self.connect() as con:
            con.execute("INSERT INTO uploads VALUES (?,?,?,?,?,?,?,?,?)", (file_id, filename, digest, scope, category, method, vision_provider, content, now()))
        return file_id

    def upload(self, file_id):
        with self.connect() as con:
            row = con.execute("SELECT * FROM uploads WHERE id=?", (file_id,)).fetchone()
        if not row:
            raise ValueError("Original file not found.")
        return dict(row)

    def create_run(self, request):
        run_id = uid("run")
        with self.connect() as con:
            con.execute("INSERT INTO runs (id,created_at,status,request_json) VALUES (?,?,?,?)",
                        (run_id, now(), "queued", json.dumps(request)))
        return run_id

    def event(self, run_id, stage, message):
        with self.connect() as con:
            row = con.execute("SELECT events_json FROM runs WHERE id=?", (run_id,)).fetchone()
            events = json.loads(row[0])
            events.append({"at": now(), "stage": stage, "message": message})
            con.execute("UPDATE runs SET events_json=?,status='running' WHERE id=?",
                        (json.dumps(events), run_id))

    def finish_run(self, run_id, result=None, error=None):
        with self.connect() as con:
            con.execute("UPDATE runs SET status=?,result_json=?,error=? WHERE id=?",
                        ("failed" if error else "pending_review", json.dumps(result) if result else None, error, run_id))

    def run(self, run_id):
        with self.connect() as con:
            row = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            review = con.execute("SELECT * FROM reviews WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise ValueError("Analysis not found.")
        r = dict(row)
        for name in ("request", "result", "events"):
            r[name] = json.loads(r.pop(name + "_json") or "null")
        r["review"] = dict(review) if review else None
        return r

    def runs(self):
        with self.connect() as con:
            rows = con.execute("SELECT id,created_at,status,request_json FROM runs ORDER BY created_at DESC LIMIT 100").fetchall()
        return [{"id": r["id"], "created_at": r["created_at"], "status": r["status"],
                 "question": json.loads(r["request_json"])["question"]} for r in rows]

    def review(self, run_id, decision, reviewer, notes):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise ValueError("Analysis not found.")
            if row[0] != "pending_review":
                raise ValueError("Only an analysis awaiting review can be reviewed, once.")
            con.execute("INSERT INTO reviews VALUES (?,?,?,?,?,?)",
                        (uid("review"), run_id, decision, reviewer, notes, now()))
            con.execute("UPDATE runs SET status=? WHERE id=?", (decision, run_id))
        return self.run(run_id)
