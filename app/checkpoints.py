"""LangGraph checkpointer backed by the application's workspace-scoped records.

Only typed LangGraph serialization is used; Python pickle is never enabled.
Whole snapshots are saved to make recovery independent of local filesystem state.
"""
import base64
from hashlib import sha256
import json
import threading

from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple, WRITES_IDX_MAP


class RecordStore:
    def __init__(self, db):
        self.db = db
        self.lock = threading.RLock()
        if not hasattr(db, "put_record"):
            with db.connect() as con:
                con.execute("CREATE TABLE IF NOT EXISTS workflow_records (kind TEXT NOT NULL,id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(kind,id))")

    def put(self, kind, key, value):
        with self.lock:
            if hasattr(self.db, "put_record"):
                return self.db.put_record(kind, key, value)
            with self.db.connect() as con:
                con.execute("INSERT INTO workflow_records VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload",
                            (kind, key, json.dumps(value)))

    def get(self, kind, key):
        if hasattr(self.db, "get_record"):
            return self.db.get_record(kind, key)
        with self.db.connect() as con:
            row = con.execute("SELECT payload FROM workflow_records WHERE kind=? AND id=?", (kind, key)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, kind):
        if hasattr(self.db, "list_records"):
            return self.db.list_records(kind)
        with self.db.connect() as con:
            return [json.loads(row[0]) for row in con.execute("SELECT payload FROM workflow_records WHERE kind=?", (kind,))]


class DatabaseCheckpointer(BaseCheckpointSaver):
    def __init__(self, db):
        super().__init__()
        self.records = RecordStore(db)

    @staticmethod
    def key(*parts):
        return sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()

    def pack(self, value):
        kind, data = self.serde.dumps_typed(value)
        return [kind, base64.b64encode(data).decode()]

    def unpack(self, value):
        return self.serde.loads_typed((value[0], base64.b64decode(value[1])))

    def get_tuple(self, config):
        options = config["configurable"]
        thread, namespace = options["thread_id"], options.get("checkpoint_ns", "")
        checkpoint_id = options.get("checkpoint_id")
        if checkpoint_id:
            saved = self.records.get("graph_checkpoint", self.key(thread, namespace, checkpoint_id))
        else:
            values = [v for v in self.records.list("graph_checkpoint") if v["thread_id"] == thread and v["namespace"] == namespace]
            saved = max(values, key=lambda c: c["checkpoint_id"]) if values else None
        if saved is None:
            return None
        checkpoint_id = saved["checkpoint_id"]
        actual_config = {"configurable": {"thread_id": thread, "checkpoint_ns": namespace, "checkpoint_id": checkpoint_id}}
        pending = [v for v in self.records.list("graph_write")
                   if v["thread_id"] == thread and v["namespace"] == namespace and v["checkpoint_id"] == checkpoint_id]
        return CheckpointTuple(config=actual_config, checkpoint=self.unpack(saved["checkpoint"]),
                               metadata=self.unpack(saved["metadata"]), parent_config=saved["parent_config"],
                               pending_writes=[(v["task_id"], v["channel"], self.unpack(v["value"])) for v in pending])

    def list(self, config, *, filter=None, before=None, limit=None):
        options = config.get("configurable", {}) if config else {}
        rows = self.records.list("graph_checkpoint")
        rows = sorted(rows, key=lambda r: r["checkpoint_id"], reverse=True)
        count = 0
        for row in rows:
            if "thread_id" in options and row["thread_id"] != options["thread_id"]:
                continue
            if "checkpoint_ns" in options and row["namespace"] != options["checkpoint_ns"]:
                continue
            if before and row["checkpoint_id"] >= before["configurable"]["checkpoint_id"]:
                continue
            metadata = self.unpack(row["metadata"])
            if filter and any(metadata.get(k) != v for k, v in filter.items()):
                continue
            if limit is not None and count >= limit:
                break
            count += 1
            yield self.get_tuple({"configurable": {"thread_id": row["thread_id"], "checkpoint_ns": row["namespace"],
                                                   "checkpoint_id": row["checkpoint_id"]}})

    def put(self, config, checkpoint, metadata, new_versions):
        options = config["configurable"]
        thread, namespace = options["thread_id"], options.get("checkpoint_ns", "")
        key = self.key(thread, namespace, checkpoint["id"])
        self.records.put("graph_checkpoint", key, {
            "id": key, "thread_id": thread, "namespace": namespace, "checkpoint_id": checkpoint["id"],
            "checkpoint": self.pack(checkpoint), "metadata": self.pack(metadata),
            "parent_config": {"configurable": {"thread_id": thread, "checkpoint_ns": namespace,
                                                 "checkpoint_id": options["checkpoint_id"]}} if options.get("checkpoint_id") else None,
        })
        return {"configurable": {"thread_id": thread, "checkpoint_ns": namespace, "checkpoint_id": checkpoint["id"]}}

    def put_writes(self, config, writes, task_id, task_path=""):
        options = config["configurable"]
        for position, (channel, value) in enumerate(writes):
            index = WRITES_IDX_MAP.get(channel, position)
            key = self.key(options["thread_id"], options.get("checkpoint_ns", ""), options["checkpoint_id"], task_id, index)
            with self.records.lock:
                if index >= 0 and self.records.get("graph_write", key):
                    continue
                self.records.put("graph_write", key, {
                    "id": key, "thread_id": options["thread_id"], "namespace": options.get("checkpoint_ns", ""),
                    "checkpoint_id": options["checkpoint_id"], "task_id": task_id, "task_path": task_path,
                    "channel": channel, "value": self.pack(value),
                })
