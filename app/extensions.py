"""Workspace-scoped connectors, evaluations, scheduled analyses and approved reports."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

from .database import now, uid
from .providers import normalize_decision_mode


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ConnectorInput(Body):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["web", "rss", "kaggle"]
    url: str | None = Field(default=None, max_length=2000)
    dataset: str | None = Field(default=None, max_length=200)
    dataset_id: str | None = None

    @model_validator(mode="after")
    def source_required(self):
        if self.kind == "kaggle" and not self.dataset or self.kind != "kaggle" and not self.url:
            raise ValueError("Provide a URL for web/RSS or a dataset handle for Kaggle.")
        return self


class CaseInput(Body):
    name: str = Field(min_length=1, max_length=160)
    question: str = Field(min_length=8, max_length=2000)
    dataset_id: str | None = None
    document_ids: list[str] = Field(default_factory=list, max_length=64)
    expected_sql: str | None = Field(default=None, max_length=8000)
    expected_agent_id: str | None = None
    expected_document_ids: list[str] = Field(default_factory=list, max_length=64)
    expected_route: Literal["sql", "rag", "mixed", "none"] | None = None
    expected_action: str | None = Field(default=None, max_length=160)
    expected_answer: str | None = Field(default=None, max_length=8000)
    split: Literal["development", "holdout"] = "development"


class EvaluationInput(Body):
    agent_mode: Literal["local", "ollama", "live"] = "local"
    decision_mode: Literal["baseline", "llm", "openai_decisions"] = "baseline"
    model: str | None = None
    local_profile: Literal["custom", "fast", "quality", "auto"] = "custom"
    local_profile_snapshot: dict | None = None
    split: Literal["development", "holdout"] = "development"
    judge_mode: Literal["none", "ollama", "live"] = "none"
    judge_model: str | None = None

    @field_validator("decision_mode", mode="before")
    @classmethod
    def migrate_decision_mode(cls, value):
        return normalize_decision_mode(value)


class ScheduleInput(Body):
    name: str = Field(min_length=1, max_length=120)
    interval_minutes: int = Field(ge=15, le=525600)
    request: dict
    enabled: bool = True


class ScheduleUpdate(Body):
    enabled: bool


class FeedbackInput(Body):
    rating: int = Field(ge=1, le=5)
    task_success: bool
    notes: str = Field(default="", max_length=2000)


class DictionaryInput(Body):
    description: str = Field(default="", max_length=2000)
    currency: str = Field(default="", max_length=20)
    timezone: str = Field(default="UTC", max_length=80)
    period_definition: str = Field(default="", max_length=1000)
    columns: dict[str, str] = Field(default_factory=dict)
    metrics: dict[str, str] = Field(default_factory=dict)


def register_extensions(app, db, engine, settings):
    def require(request, roles=("admin", "analyst")):
        user = getattr(request.state, "user", None)
        if getattr(settings, "auth_enabled", False) and (not user or user.get("role") not in roles):
            raise HTTPException(403, "Your role cannot perform this action.")

    def validate_scope(dataset_id=None, document_ids=()):
        if dataset_id:
            db.dataset(dataset_id)
        documents = {d["id"]: d for d in db.documents()}
        for document_id in document_ids:
            if document_id not in documents:
                raise HTTPException(404, "Document not found in this workspace.")
            if dataset_id and documents[document_id]["dataset_id"] not in {None, dataset_id}:
                raise HTTPException(400, "Document does not belong to the selected dataset.")

    @app.get("/api/connectors")
    def connectors():
        return db.list_records("connectors")

    @app.post("/api/connectors", status_code=201)
    def create_connector(body: ConnectorInput, request: Request):
        require(request)
        validate_scope(body.dataset_id)
        if body.kind != "kaggle":
            from urllib.parse import urlsplit
            url = urlsplit(body.url)
            if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
                raise HTTPException(400, "Use an HTTP(S) URL without credentials.")
        record = {**body.model_dump(), "id": uid("connector"), "status": "new", "created_at": now()}
        db.put_record("connectors", record["id"], record)
        return record

    @app.post("/api/connectors/{connector_id}/sync", status_code=202)
    def sync(connector_id: str, request: Request):
        require(request)
        if not db.get_record("connectors", connector_id):
            raise HTTPException(404, "Connector not found.")
        if getattr(settings, "local_only", False):
            raise HTTPException(400, "Online synchronization is disabled in local-only mode.")
        task_id = db.create_task("connector", {"connector_id": connector_id})
        return {"id": task_id, "task_id": task_id, "status": "queued"}

    @app.delete("/api/connectors/{connector_id}")
    def delete_connector(connector_id: str, request: Request):
        require(request)
        if not db.delete_record("connectors", connector_id):
            raise HTTPException(404, "Connector not found.")
        return {"deleted": True}

    @app.get("/api/sources/versions")
    def source_versions():
        return db.list_records("source_versions")

    @app.get("/api/datasets/{dataset_id}/dictionary")
    def dictionary(dataset_id: str):
        dataset = db.dataset(dataset_id)
        return db.get_record("dictionaries", dataset_id) or {"dataset_id": dataset_id, "columns": {c["name"]: c.get("label", "") for c in dataset["columns"]}, "metrics": {}}

    @app.put("/api/datasets/{dataset_id}/dictionary")
    def save_dictionary(dataset_id: str, body: DictionaryInput, request: Request):
        require(request)
        dataset = db.dataset(dataset_id)
        if set(body.columns) - {c["name"] for c in dataset["columns"]}:
            raise HTTPException(400, "Dictionary contains unknown columns.")
        record = {**body.model_dump(), "dataset_id": dataset_id, "updated_at": now()}
        db.put_record("dictionaries", dataset_id, record)
        return record

    @app.get("/api/evaluation/cases")
    def cases():
        return db.list_records("evaluation_cases")

    @app.post("/api/evaluation/cases", status_code=201)
    def create_case(body: CaseInput, request: Request):
        require(request)
        if not body.dataset_id and not body.document_ids:
            raise HTTPException(400, "Select data for this evaluation case.")
        validate_scope(body.dataset_id, body.document_ids + body.expected_document_ids)
        if body.expected_sql:
            if not body.dataset_id:
                raise HTTPException(400, "Gold SQL requires a dataset.")
            db.query(body.expected_sql, body.dataset_id)
        record = {**body.model_dump(), "id": uid("case"), "version": 1, "created_at": now()}
        db.put_record("evaluation_cases", record["id"], record)
        return record

    @app.delete("/api/evaluation/cases/{case_id}")
    def delete_case(case_id: str, request: Request):
        require(request)
        if not db.delete_record("evaluation_cases", case_id):
            raise HTTPException(404, "Evaluation case not found.")
        return {"deleted": True}

    @app.post("/api/evaluation/run", status_code=202)
    def evaluate(body: EvaluationInput, request: Request):
        require(request)
        if body.local_profile != "custom":
            from .local_profiles import prepare_local_profile
            prepared=prepare_local_profile(body.model_dump(),settings,engine.providers,refresh=True)
            body.model=prepared["model"]
            body.local_profile_snapshot=prepared["local_profile_snapshot"]
        else:
            body.local_profile_snapshot=None
        if getattr(settings, "local_only", False) and (body.agent_mode == "live" or body.judge_mode == "live" or body.decision_mode == "openai_decisions"):
            raise HTTPException(400, "Cloud evaluation is disabled by the local-only policy.")
        if body.decision_mode == "openai_decisions" and not (settings.openai_key and settings.openai_decision_model):
            raise HTTPException(400, "Set OPENAI_API_KEY and OPENAI_DECISION_MODEL to enable OpenAI Decisions.")
        if not any(c.get("split") == body.split for c in db.list_records("evaluation_cases")):
            raise HTTPException(400, "Add labelled cases to this split before evaluation.")
        task_id = db.create_task("evaluation", body.model_dump())
        return {"id": task_id, "task_id": task_id, "status": "queued"}

    @app.get("/api/evaluation/results")
    def evaluation_results():
        return sorted(db.list_records("evaluation_results"), key=lambda r: r["created_at"], reverse=True)

    @app.get("/api/metrics")
    def metrics():
        from .evaluation import operational_metrics
        return operational_metrics(db)

    @app.post("/api/analyses/{run_id}/feedback")
    def feedback(run_id: str, body: FeedbackInput, request: Request):
        run = db.run(run_id)
        if not run.get("result"):
            raise HTTPException(409, "Feedback requires a completed analysis.")
        user = getattr(request.state, "user", None) or {}
        feedback_id = run_id + "_" + user.get("id", "local")
        record = {**body.model_dump(), "id": feedback_id, "run_id": run_id, "user_id": user.get("id"), "created_at": now()}
        db.put_record("feedback", feedback_id, record)
        return record

    @app.get("/api/analyses/{run_id}/report")
    def export_report(run_id: str, format: Literal["html", "pdf"] = "html"):
        from .reports import html_report, pdf_report
        try:
            run = db.approved_run(run_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        headers = {"Content-Disposition": f'attachment; filename="market-lens-{run_id}.{format}"'}
        if format == "html":
            return HTMLResponse(html_report(run), headers=headers)
        return Response(pdf_report(run), media_type="application/pdf", headers=headers)

    @app.get("/api/schedules")
    def schedules():
        return db.list_records("schedules")

    @app.post("/api/schedules", status_code=201)
    def schedule(body: ScheduleInput, request: Request):
        require(request)
        from .main import AnalysisInput, validate_analysis_request
        analysis = validate_analysis_request(AnalysisInput.model_validate(body.request), db, settings,
                                             engine.providers, allow_revision=False).model_dump()
        record = {**body.model_dump(), "request": analysis, "id": uid("schedule"), "created_at": now(),
                  "owner_id": db.current_user_id, "next_run_at": (datetime.now(timezone.utc) + timedelta(minutes=body.interval_minutes)).isoformat()}
        db.put_record("schedules", record["id"], record)
        return record

    @app.delete("/api/schedules/{schedule_id}")
    def delete_schedule(schedule_id: str, request: Request):
        require(request)
        if not db.delete_record("schedules", schedule_id):
            raise HTTPException(404, "Schedule not found.")
        return {"deleted": True}

    @app.patch("/api/schedules/{schedule_id}")
    def update_schedule(schedule_id: str, body: ScheduleUpdate, request: Request):
        require(request)
        record = db.get_record("schedules", schedule_id)
        if not record:
            raise HTTPException(404, "Schedule not found.")
        record["enabled"] = body.enabled
        if body.enabled:
            record["next_run_at"] = (datetime.now(timezone.utc) + timedelta(minutes=record["interval_minutes"])).isoformat()
        db.put_record("schedules", schedule_id, record)
        return record

    @app.post("/api/documents/index", status_code=202)
    def index(request: Request):
        require(request)
        task_id = db.create_task("index", {})
        return {"id": task_id, "task_id": task_id, "status": "queued"}


def execute_job(kind, payload, db, settings):
    if kind == "connector":
        from .connectors import sync_connector
        return sync_connector(db, settings, payload["connector_id"])
    if kind == "evaluation":
        from .evaluation import run_evaluation
        return run_evaluation(db, settings, payload)
    if kind == "scheduled_analysis":
        schedule = db.get_record("schedules", payload["schedule_id"])
        if not schedule or not schedule["enabled"]:
            return {"skipped": True}
        if not schedule_owner_active(db, settings, schedule):
            return {"skipped": True, "reason": "Schedule owner is disabled or no longer authorized."}
        # A deterministic occurrence ID makes repeated delivery safe.
        occurrence = db.get_record("schedule_runs", payload["occurrence_id"])
        if occurrence:
            return occurrence
        from .main import AnalysisInput, validate_analysis_request
        from .providers import Providers
        try:
            analysis = validate_analysis_request(AnalysisInput.model_validate(schedule["request"]), db, settings,
                                                 Providers(settings), allow_revision=False).model_dump()
        except HTTPException as exc:
            raise ValueError(str(exc.detail)) from exc
        run_id = db.create_run(analysis, idempotency_key=payload["occurrence_id"])
        result = {"id": payload["occurrence_id"], "run_id": run_id, "created_at": now()}
        db.put_record("schedule_runs", payload["occurrence_id"], result)
        return result
    if kind == "index":
        from .retrieval import index_documents
        return index_documents(db, settings)
    raise ValueError("Unknown background task type.")


def schedule_owner_active(db, settings, schedule):
    if not getattr(settings, "auth_enabled", False):
        return True
    with db.connect() as con:
        row = con.execute("SELECT role,disabled FROM users WHERE id=? AND workspace_id=?",
                          (schedule.get("owner_id"), db.workspace_id)).fetchone()
    return bool(row and not row["disabled"] and row["role"] in {"admin", "analyst"})


def enqueue_due_schedules(db, settings):
    current = datetime.now(timezone.utc)
    for workspace_id in db.all_workspace_ids():
        scoped = db.scoped(workspace_id)
        for schedule in scoped.list_records("schedules"):
            if schedule["enabled"] and not schedule_owner_active(scoped, settings, schedule):
                schedule["enabled"] = False
                schedule["last_error"] = "Schedule owner is disabled or no longer authorized. An administrator must review this schedule."
                scoped.put_record("schedules", schedule["id"], schedule)
                continue
            due = datetime.fromisoformat(schedule["next_run_at"])
            if not schedule["enabled"] or due > current:
                continue
            occurrence_id = schedule["id"] + "_" + hashlib.sha256(schedule["next_run_at"].encode()).hexdigest()[:12]
            owned = scoped.scoped(workspace_id, schedule.get("owner_id"))
            owned.create_task("scheduled_analysis", {"schedule_id": schedule["id"], "occurrence_id": occurrence_id},
                              idempotency_key=occurrence_id)
            schedule["next_run_at"] = (current + timedelta(minutes=schedule["interval_minutes"])).isoformat()
            schedule["last_enqueued_at"] = current.isoformat()
            scoped.put_record("schedules", schedule["id"], schedule)
