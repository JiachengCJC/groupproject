from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import json
from pathlib import Path
import threading
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool

from .config import ROOT, Settings
from .database import Database, identifier
from .engine import Engine
from .ingestion import FORMATS, MAX_FILE, ingest
from .providers import ProviderError
from .seed import public_scenarios, scenario_csv, SCENARIOS, seed


class Input(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class AnalysisInput(Input):
    question: str = Field(min_length=8, max_length=2000)
    dataset_id: str | None = Field(default=None, min_length=1, max_length=80)
    document_ids: list[str] = Field(default_factory=list, max_length=64)
    agent_mode: Literal["local", "live", "ollama"] = "local"
    decision_mode: Literal["baseline", "jev", "llm"] = "baseline"
    model: str | None = Field(default=None, max_length=160)
    reviewer_notes: str = Field(default="", max_length=2000)
    parent_run_id: str | None = None


class CSVInput(Input):
    name: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=2_000_000)


class DocumentInput(Input):
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=2_000_000)
    source: str = Field(default="User-provided document", min_length=1, max_length=500)
    dataset_id: str | None = None


class ReviewInput(Input):
    decision: Literal["approved", "rejected"]
    reviewer: str = Field(min_length=2, max_length=100)
    notes: str = Field(default="", max_length=2000)


class SQLInput(Input):
    dataset_id: str
    sql: str = Field(min_length=6, max_length=8000)


def create_app(settings=None):
    settings = settings or Settings.from_env()
    db = Database(settings.db_path)
    engine = Engine(db, settings)
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="analysis")
    slots = threading.BoundedSemaphore(2)

    @asynccontextmanager
    async def lifespan(app):
        db.initialize()
        seed(db)
        with db.connect() as con:
            con.execute("UPDATE runs SET status='failed', error='Server restarted before the analysis completed. Run it again.' WHERE status IN ('queued','running')")
        yield
        pool.shutdown(wait=True)

    app = FastAPI(title="Market Lens", version="0.2.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.db, app.state.engine = db, engine
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        # Local single-user MVP: prevent a random website from posting to localhost.
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if request.headers.get("sec-fetch-site") == "cross-site" or (origin and urlparse(origin).netloc != request.headers.get("host")):
                return JSONResponse({"detail": "Cross-origin writes are not allowed."}, status_code=403)
            content_type = request.headers.get("content-type", "").split(";")[0]
            upload = request.url.path == "/api/uploads" and content_type == "multipart/form-data"
            if content_type != "application/json" and not upload:
                return JSONResponse({"detail": "Use application/json."}, status_code=415)
            # Enforce actual streamed bytes, not only a client-provided Content-Length.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > (MAX_FILE+1_000_000 if upload else 3_000_000):
                    return JSONResponse({"detail": "Request is too large."}, status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    @app.exception_handler(ValueError)
    async def invalid_input(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.2.0"}

    @app.get("/api/config")
    def config():
        return {"live_agents_available": bool(settings.openai_key and settings.openai_model),
                "jev_available": bool(settings.jev_key), "openai_model": settings.openai_model or None,
                "jev_model": settings.jev_model, "scenarios": public_scenarios(),
                "deployment": "local_single_user", "retrieval": "Multilingual TF-IDF / cosine similarity",
                "ollama_model": settings.ollama_model, "ollama_vision_model": settings.ollama_vision_model,
                "formats": FORMATS, "max_file_bytes": MAX_FILE, "media_enabled": bool(settings.whisper_model)}

    @app.get("/api/models/ollama")
    def ollama_models():
        return engine.providers.ollama_models()

    @app.get("/api/sources")
    def sources():
        return {"datasets": db.datasets(), "documents": db.documents()}

    @app.get("/api/datasets/{dataset_id}")
    def dataset_preview(dataset_id: str):
        dataset = db.dataset(dataset_id)
        return {"dataset": dataset, "preview": db.query(f"SELECT * FROM {identifier(dataset['table_name'])} LIMIT 10", dataset_id)}

    @app.post("/api/datasets", status_code=201)
    def import_csv(body: CSVInput):
        return db.import_csv(body.name, body.content)

    @app.post("/api/documents", status_code=201)
    def import_document(body: DocumentInput):
        return db.add_document(body.title, body.content, body.source, body.dataset_id)

    @app.post("/api/uploads", status_code=201)
    async def upload(file: UploadFile = File(...), dataset_id: str = Form(""), category: str = Form("General"),
                     vision_provider: Literal["none", "live", "ollama"] = Form("none"), vision_model: str = Form("")):
        if len(category) > 80 or len(vision_model) > 160:
            raise HTTPException(422, "Category or model name is too long.")
        if vision_provider == "live" and not (settings.openai_key and settings.openai_model):
            raise HTTPException(400, "Configure OpenAI before using cloud vision.")
        if vision_provider == "ollama" and not (vision_model or settings.ollama_vision_model):
            raise HTTPException(400, "Select a vision-capable Ollama model for image conversion.")
        if not slots.acquire(blocking=False):
            raise HTTPException(429, "Workers are busy. Try uploading again shortly.")
        try:
            data = await file.read(MAX_FILE+1)
            if len(data) > MAX_FILE:
                raise HTTPException(413, "Each file must be at most 25 MB.")
            try:
                return await run_in_threadpool(ingest, db, settings, engine.providers, file.filename or "upload", data,
                                              dataset_id=dataset_id or None, category=category,
                                              vision_provider=vision_provider, vision_model=vision_model or None)
            except ProviderError as exc:
                raise HTTPException(400, str(exc)) from exc
        finally:
            await file.close()
            slots.release()

    @app.get("/api/files/{file_id}")
    def original_file(file_id: str):
        from urllib.parse import quote
        try:
            original = db.upload(file_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return Response(original["content"], media_type="application/octet-stream",
                        headers={"Content-Disposition": "attachment; filename*=UTF-8''"+quote(original["filename"])})

    @app.get("/api/documents/{document_id}")
    def document(document_id: str):
        with db.connect() as con:
            row = con.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Document not found.")
        result = dict(row)
        result["warnings"] = json.loads(result.pop("warnings_json"))
        return result

    @app.get("/api/sample.csv")
    def sample_csv():
        from fastapi.responses import Response
        return Response(scenario_csv(SCENARIOS[0]), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="sample_business.csv"'})

    @app.post("/api/query")
    def query(body: SQLInput):
        return db.query(body.sql, body.dataset_id)

    @app.post("/api/analyses", status_code=202)
    def analyze(body: AnalysisInput):
        if body.dataset_id:
            db.dataset(body.dataset_id)
        if not body.dataset_id and not body.document_ids:
            raise HTTPException(400, "Select a dataset or at least one document.")
        if body.document_ids:
            available = {d["id"]: d for d in db.documents()}
            if any(d not in available for d in body.document_ids):
                raise HTTPException(400, "A selected document no longer exists.")
            if body.dataset_id and any(available[d]["dataset_id"] not in {None, body.dataset_id} for d in body.document_ids):
                raise HTTPException(400, "Selected documents must be shared or associated with this dataset.")
        if body.agent_mode == "live" and not (settings.openai_key and settings.openai_model):
            raise HTTPException(400, "Set OPENAI_API_KEY and OPENAI_MODEL in .env to enable live agents.")
        if body.decision_mode == "jev" and not settings.jev_key:
            raise HTTPException(400, "Set TYPESAFE_API_KEY in .env to enable JEV.")
        if body.agent_mode == "ollama":
            models = engine.providers.ollama_models()
            chosen = body.model or settings.ollama_model
            if not models["available"]:
                raise HTTPException(400, models["error"])
            if chosen not in models["models"]:
                raise HTTPException(400, "Select an installed Ollama model. Download it with ollama pull first.")
            body.model = chosen
        if body.decision_mode == "llm" and body.agent_mode == "local":
            raise HTTPException(400, "Model recommendations require OpenAI or Ollama specialists.")
        if body.parent_run_id:
            parent = db.run(body.parent_run_id)
            if parent["status"] != "rejected":
                raise HTTPException(409, "Revisions must reference a rejected analysis.")
            body.reviewer_notes = parent["review"]["notes"]
        if not slots.acquire(blocking=False):
            raise HTTPException(429, "Two analyses are already running. Try again shortly.")
        try:
            run_id = db.create_run(body.model_dump())
            def work():
                try:
                    engine.run_job(run_id)
                finally:
                    slots.release()
            pool.submit(work)
        except Exception:
            slots.release()
            raise
        return {"id": run_id, "status": "queued"}

    @app.get("/api/analyses")
    def analyses():
        return db.runs()

    @app.get("/api/analyses/{run_id}")
    def analysis(run_id: str):
        try:
            return db.run(run_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc))

    @app.post("/api/analyses/{run_id}/review")
    def review(run_id: str, body: ReviewInput):
        if body.decision == "rejected" and not body.notes:
            raise HTTPException(422, "Add a reviewer note explaining what should change.")
        try:
            return db.review(run_id, body.decision, body.reviewer, body.notes)
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.get("/api/analyses/{run_id}/export")
    def export(run_id: str):
        run = db.run(run_id)
        if run["status"] != "approved":
            raise HTTPException(409, "An analyst must approve this brief before it can be exported as approved.")
        return JSONResponse(run, headers={"Content-Disposition": f'attachment; filename="market-lens-{run_id}.json"'})

    @app.post("/api/benchmarks")
    def benchmark():
        if not slots.acquire(blocking=False):
            raise HTTPException(429, "Analysis workers are busy. Try again shortly.")
        try:
            return engine.benchmark()
        finally:
            slots.release()

    @app.get("/api/benchmarks")
    def benchmarks():
        with db.connect() as con:
            row = con.execute("SELECT result_json FROM benchmarks ORDER BY created_at DESC LIMIT 1").fetchone()
        return json.loads(row[0]) if row else None

    app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")

    @app.get("/")
    def index():
        return FileResponse(ROOT / "app" / "static" / "index.html")

    return app


app = create_app()
