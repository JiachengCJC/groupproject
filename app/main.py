"""HTTP interface, workspace authorization and durable analysis submission."""
from contextlib import asynccontextmanager
import hmac
import json
import threading
from typing import Literal
from urllib.parse import urlparse, quote

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool

from .auth import Auth, COOKIE
from .config import ROOT, Settings
from .database import Database, identifier
from .engine import Engine
from .ingestion import FORMATS, MAX_FILE, ingest
from .jobs import Worker
from .providers import ProviderError, normalize_decision_mode
from .local_profiles import LocalProfile, LocalProfileSnapshot, prepare_local_profile, profile_catalog
from .seed import public_scenarios, scenario_csv, SCENARIOS, seed


class Input(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class CandidateAction(Input):
    id: str = Field(min_length=1,max_length=64,pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1,max_length=200)
    description: str = Field(min_length=1,max_length=2000)


class AnalysisInput(Input):
    question: str = Field(min_length=8,max_length=2000)
    dataset_id: str | None = Field(default=None,min_length=1,max_length=80)
    dataset_ids: list[str] = Field(default_factory=list,max_length=8)
    document_ids: list[str] = Field(default_factory=list,max_length=64)
    agent_mode: Literal["local","live","ollama"] = "local"
    decision_mode: Literal["baseline","openai_decisions","llm"] = "baseline"
    model: str | None = Field(default=None,max_length=160)
    local_profile: LocalProfile = "custom"
    local_profile_snapshot: LocalProfileSnapshot | None = None
    reviewer_notes: str = Field(default="",max_length=2000)
    parent_run_id: str | None = None
    routing_mode: Literal["auto","sql","rag","hybrid"] = "auto"
    candidate_actions: list[CandidateAction] = Field(default_factory=list,max_length=20)
    max_steps: int | None = Field(default=None,ge=1,le=5)

    @field_validator("decision_mode", mode="before")
    @classmethod
    def migrate_decision_mode(cls,value):
        return normalize_decision_mode(value)

    @field_validator("candidate_actions")
    @classmethod
    def unique_actions(cls,value):
        if len({a.id for a in value})!=len(value):raise ValueError("Candidate action IDs must be unique.")
        return value


class CSVInput(Input):
    name: str = Field(min_length=1,max_length=120)
    content: str = Field(min_length=1,max_length=2_000_000)


class DocumentInput(Input):
    title: str = Field(min_length=1,max_length=160)
    content: str = Field(min_length=1,max_length=2_000_000)
    source: str = Field(default="User-provided document",min_length=1,max_length=500)
    dataset_id: str | None = None


class ReviewInput(Input):
    decision: Literal["approved","rejected"]
    reviewer: str = Field(default="",max_length=100)
    notes: str = Field(default="",max_length=2000)


class SQLInput(Input):
    dataset_id: str
    dataset_ids: list[str] = Field(default_factory=list,max_length=8)
    sql: str = Field(min_length=6,max_length=8000)


class LoginInput(Input):
    model_config = ConfigDict(str_strip_whitespace=False, extra="forbid")
    email: str = Field(min_length=3,max_length=254)
    password: str = Field(min_length=1,max_length=128)


class SetupInput(LoginInput):
    name: str = Field(min_length=2,max_length=100)
    setup_token: str = Field(default="",max_length=256)


class UserInput(LoginInput):
    name: str = Field(min_length=2,max_length=100)
    role: Literal["admin","analyst","reviewer","viewer"] = "analyst"


class UserUpdate(Input):
    model_config = ConfigDict(str_strip_whitespace=False, extra="forbid")
    role: Literal["admin","analyst","reviewer","viewer"] | None = None
    disabled: bool | None = None
    password: str | None = Field(default=None,min_length=12,max_length=128)


def validate_analysis_request(body,db,settings,providers,*,allow_revision=True):
    """Shared validation for interactive and scheduled analysis requests."""
    if body.parent_run_id and not allow_revision:
        raise HTTPException(400,"A recurring analysis cannot be a one-time revision.")
    if not body.dataset_id and body.dataset_ids:body.dataset_id=body.dataset_ids[0]
    for dataset_id in ([body.dataset_id] if body.dataset_id else [])+body.dataset_ids:db.dataset(dataset_id)
    if not body.dataset_id and not body.document_ids:raise HTTPException(400,"Select a dataset or at least one document.")
    if body.document_ids:
        available={d["id"]:d for d in db.documents()}
        if any(d not in available for d in body.document_ids):raise HTTPException(400,"A selected document no longer exists.")
        allowed_scopes={None,body.dataset_id,*body.dataset_ids}
        if body.dataset_id and any(available[d]["dataset_id"] not in allowed_scopes for d in body.document_ids):raise HTTPException(400,"Selected documents must be shared or associated with a selected dataset.")
    if settings.local_only and (body.agent_mode=="live" or body.decision_mode=="openai_decisions"):raise HTTPException(400,"Cloud models and OpenAI Decisions are disabled in local-only mode.")
    if body.agent_mode=="live" and not (settings.openai_key and settings.openai_model):raise HTTPException(400,"Set OPENAI_API_KEY and OPENAI_MODEL to enable the cloud provider.")
    if body.decision_mode=="openai_decisions" and not (settings.openai_key and settings.openai_decision_model):raise HTTPException(400,"Set OPENAI_API_KEY and OPENAI_DECISION_MODEL to enable OpenAI Decisions.")
    if body.local_profile != "custom":
        prepared=prepare_local_profile(body.model_dump(),settings,providers,refresh=True)
        body.model=prepared["model"]
        body.local_profile_snapshot=LocalProfileSnapshot.model_validate(prepared["local_profile_snapshot"])
    elif body.agent_mode=="ollama":
        models=providers.ollama_models();chosen=body.model or settings.ollama_model
        if not models["available"]:raise HTTPException(400,models["error"])
        if chosen not in models["models"]:raise HTTPException(400,"Select an installed Ollama model. Download it with ollama pull first.")
        body.model=chosen
    if body.local_profile == "custom":body.local_profile_snapshot=None
    if body.decision_mode=="llm" and body.agent_mode=="local":raise HTTPException(400,"Model recommendations require a cloud or Ollama model.")
    if body.max_steps and body.max_steps>settings.max_agent_steps:raise HTTPException(400,"Requested steps exceed the server budget.")
    if body.parent_run_id:
        parent=db.run(body.parent_run_id)
        if parent["status"]!="rejected":raise HTTPException(409,"Revisions must reference a rejected analysis.")
        body.reviewer_notes=parent["review"]["notes"]
    return body


def create_app(settings=None):
    settings=settings or Settings.from_env()
    db=Database(settings.database_url or settings.db_path,analytics_url=settings.analytics_database_url,analytics_role=settings.analytics_role)
    engine=Engine(db,settings)
    auth=Auth(db,settings)
    worker=Worker(db,settings)
    upload_slots=threading.BoundedSemaphore(2)

    @asynccontextmanager
    async def lifespan(app):
        db.initialize()
        auth.bootstrap()
        if settings.seed_demo:seed(db.scoped("default"))
        if settings.embedded_worker:worker.start()
        yield
        if settings.embedded_worker:worker.stop()

    app=FastAPI(title="Market Lens",version="1.0.0",lifespan=lifespan,docs_url=None,redoc_url=None)
    app.state.db,app.state.engine,app.state.settings,app.state.auth=db,engine,settings,auth
    app.state.worker=worker
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=[h.strip() for h in settings.allowed_hosts.split(",") if h.strip()])

    def require(request,*roles):
        if not settings.auth_enabled:return
        user=getattr(request.state,"user",None)
        if not user:raise HTTPException(401,"Sign in to continue.")
        if roles and user["role"] not in roles:raise HTTPException(403,"Your role cannot perform this action.")

    app.state.require_role=require

    @app.middleware("http")
    async def boundary(request,call_next):
        write=request.method in {"POST","PUT","PATCH","DELETE"}
        if write:
            origin=request.headers.get("origin")
            if request.headers.get("sec-fetch-site")=="cross-site" or (origin and (urlparse(origin).netloc!=request.headers.get("host") or urlparse(origin).scheme not in {"http","https"})):
                return JSONResponse({"detail":"Cross-origin writes are not allowed."},status_code=403)
            content_type=request.headers.get("content-type","").split(";")[0]
            upload=request.url.path=="/api/uploads" and content_type=="multipart/form-data"
            if content_type!="application/json" and not upload:
                return JSONResponse({"detail":"Use application/json."},status_code=415)
            body=bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body)>(MAX_FILE+1_000_000 if upload else 3_000_000):
                    return JSONResponse({"detail":"Request is too large."},status_code=413)
            request._body=bytes(body)
        user=await run_in_threadpool(auth.session_user,request.cookies.get(COOKIE)) if settings.auth_enabled else None
        request.state.user=user
        public={"/api/health","/api/auth/status","/api/auth/login","/api/auth/setup","/api/auth/logout"}
        if settings.auth_enabled and request.url.path.startswith("/api/") and request.url.path not in public and not user:
            return JSONResponse({"detail":"Sign in to continue."},status_code=401)
        with db.context(user["workspace_id"] if user else "default",user["id"] if user else None):
            response=await call_next(request)
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Referrer-Policy"]="no-referrer"
        response.headers["Cache-Control"]="no-store"
        response.headers["Content-Security-Policy"]="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    @app.exception_handler(ValueError)
    async def invalid_input(request,exc):return JSONResponse({"detail":str(exc)},status_code=400)

    @app.exception_handler(ProviderError)
    async def provider_failure(request,exc):return JSONResponse({"detail":str(exc)},status_code=400)

    def session_response(user,token):
        response=JSONResponse({"user":user})
        response.set_cookie(COOKIE,token,httponly=True,secure=settings.cookie_secure,samesite="strict",max_age=settings.session_hours*3600,path="/")
        return response

    @app.get("/api/auth/status")
    def auth_status(request:Request):
        return {"enabled":settings.auth_enabled,"setup_required":settings.auth_enabled and auth.setup_required(),"setup_token_required":bool(settings.setup_token),"user":request.state.user}

    @app.post("/api/auth/setup",status_code=201)
    def setup(body:SetupInput,request:Request):
        if not settings.auth_enabled:raise HTTPException(409,"Authentication is disabled in this development configuration.")
        if settings.setup_token:
            if not hmac.compare_digest(body.setup_token,settings.setup_token):raise HTTPException(403,"The setup token is incorrect.")
        elif not request.client or request.client.host not in {"127.0.0.1","::1","localhost","testclient"}:
            raise HTTPException(403,"Remote setup requires SETUP_TOKEN in the server configuration.")
        try:user=auth.create_user(body.email,body.password,body.name,"admin",setup=True,workspace_id="default")
        except ValueError as exc:raise HTTPException(409 if not auth.setup_required() else 400,str(exc)) from exc
        response=session_response(user,auth.issue_session(user["id"]));response.status_code=201
        return response

    @app.post("/api/auth/login")
    def login(body:LoginInput,request:Request):
        try:result=auth.login(body.email,body.password,request.client.host if request.client else "unknown")
        except PermissionError as exc:raise HTTPException(429,str(exc)) from exc
        if not result:raise HTTPException(401,"Email or password is incorrect.")
        return session_response(*result)

    @app.post("/api/auth/logout")
    def logout(request:Request):
        auth.logout(request.cookies.get(COOKIE))
        response=JSONResponse({"ok":True});response.delete_cookie(COOKIE,path="/",secure=settings.cookie_secure,httponly=True,samesite="strict")
        return response

    @app.get("/api/auth/users")
    def users(request:Request):require(request,"admin");return auth.users()

    @app.post("/api/auth/users",status_code=201)
    def create_user(body:UserInput,request:Request):
        require(request,"admin")
        user=auth.create_user(body.email,body.password,body.name,body.role)
        db.audit("user.created",user["id"],{"role":user["role"]})
        return user

    @app.patch("/api/auth/users/{user_id}")
    def update_user(user_id:str,body:UserUpdate,request:Request):
        require(request,"admin")
        result=auth.update_user(user_id,body.role,body.disabled,body.password)
        db.audit("user.updated",user_id,result)
        return result

    @app.get("/api/health")
    def health():
        with db.connect() as con:con.execute("SELECT 1")
        return {"status":"ok","version":"1.0.0","database":db.dialect}

    @app.get("/api/config")
    def config():
        return {"live_agents_available":bool(settings.openai_key and settings.openai_model and not settings.local_only),
                "openai_decisions_available":bool(settings.openai_key and settings.openai_decision_model and not settings.local_only),"openai_model":settings.openai_model or None,
                "openai_decision_model":settings.openai_decision_model,"scenarios":public_scenarios() if db.workspace_id=="default" and settings.seed_demo else [],
                "deployment":"team" if settings.auth_enabled else "local_development","database":db.dialect,
                "auth_enabled":settings.auth_enabled,"local_only":settings.local_only,"orchestration":settings.orchestration,
                "retrieval":settings.retrieval_mode,"retrieval_mode":settings.retrieval_mode,"embedding_provider":settings.embedding_provider,
                "embedding_model":settings.embedding_model,"worker_mode":"embedded" if settings.embedded_worker else "external",
                "ollama_model":settings.ollama_model,"ollama_vision_model":settings.ollama_vision_model,
                "local_profiles":profile_catalog(settings),
                "cloud_protocol":settings.cloud_protocol,"formats":FORMATS,"max_file_bytes":MAX_FILE,
                "media_enabled":bool(settings.whisper_model),"max_agent_steps":settings.max_agent_steps}

    @app.get("/api/models/ollama")
    def ollama_models():return engine.providers.ollama_models()

    @app.get("/api/sources")
    def sources():return {"datasets":db.datasets(),"documents":db.documents()}

    @app.get("/api/datasets/{dataset_id}")
    def dataset_preview(dataset_id:str):
        dataset=db.dataset(dataset_id)
        return {"dataset":dataset,"preview":db.query(f"SELECT * FROM {identifier(dataset['table_name'])} LIMIT 10",dataset_id)}

    @app.post("/api/datasets",status_code=201)
    def import_csv(body:CSVInput,request:Request):
        require(request,"admin","analyst")
        result=db.import_csv(body.name,body.content);db.audit("dataset.imported",result["id"])
        return result

    @app.delete("/api/datasets/{dataset_id}")
    def delete_dataset(dataset_id:str,request:Request):require(request,"admin","analyst");db.delete_dataset(dataset_id);return {"deleted":True}

    @app.post("/api/documents",status_code=201)
    def import_document(body:DocumentInput,request:Request):
        require(request,"admin","analyst")
        result=db.add_document(body.title,body.content,body.source,body.dataset_id);db.audit("document.imported",result["id"])
        return result

    @app.delete("/api/documents/{document_id}")
    def delete_document(document_id:str,request:Request):require(request,"admin","analyst");db.delete_document(document_id);return {"deleted":True}

    @app.post("/api/uploads",status_code=201)
    async def upload(request:Request,file:UploadFile=File(...),dataset_id:str=Form(""),category:str=Form("General"),
                     vision_provider:Literal["none","live","ollama"]=Form("none"),vision_model:str=Form(""),background:bool=Form(False)):
        require(request,"admin","analyst")
        if len(category)>80 or len(vision_model)>160:raise HTTPException(422,"Category or model name is too long.")
        if settings.local_only and vision_provider=="live":raise HTTPException(400,"Cloud vision is disabled in local-only mode.")
        if vision_provider=="live" and not (settings.openai_key and settings.openai_model):raise HTTPException(400,"Configure the cloud provider before using cloud vision.")
        if vision_provider=="ollama" and not (vision_model or settings.ollama_vision_model):raise HTTPException(400,"Select a vision-capable Ollama model for image conversion.")
        if dataset_id:db.dataset(dataset_id)
        if not upload_slots.acquire(blocking=False):raise HTTPException(429,"Upload slots are busy. Try again shortly.")
        try:
            data=await file.read(MAX_FILE+1)
            if len(data)>MAX_FILE:raise HTTPException(413,"Each file must be at most 25 MB.")
            if background:
                from .database import digest
                original=db.save_upload(file.filename or "upload",data,digest(data),dataset_id or None,category,"pending","pending")
                task_id=db.create_task("upload",{"upload_id":original,"dataset_id":dataset_id or None,"category":category,"vision_provider":vision_provider,"vision_model":vision_model or None},max_attempts=2)
                return JSONResponse({"task_id":task_id,"status":"queued"},status_code=202)
            return await run_in_threadpool(ingest,db.scoped(),settings,engine.providers,file.filename or "upload",data,
                                          dataset_id=dataset_id or None,category=category,vision_provider=vision_provider,vision_model=vision_model or None)
        finally:
            await file.close();upload_slots.release()

    @app.get("/api/files/{file_id}")
    def original_file(file_id:str):
        try:original=db.upload(file_id)
        except ValueError as exc:raise HTTPException(404,str(exc)) from exc
        return Response(original["content"],media_type="application/octet-stream",headers={"Content-Disposition":"attachment; filename*=UTF-8''"+quote(original["filename"])})

    @app.get("/api/documents/{document_id}")
    def document(document_id:str):
        try:return db.document(document_id)
        except ValueError as exc:raise HTTPException(404,str(exc)) from exc

    @app.get("/api/sample.csv")
    def sample_csv():return Response(scenario_csv(SCENARIOS[0]),media_type="text/csv",headers={"Content-Disposition":'attachment; filename="sample_business.csv"'})

    @app.post("/api/query")
    def query(body:SQLInput,request:Request):
        require(request,"admin","analyst","reviewer")
        return db.query(body.sql,body.dataset_id,dataset_ids=body.dataset_ids)

    @app.post("/api/analyses",status_code=202)
    def analyze(body:AnalysisInput,request:Request):
        require(request,"admin","analyst")
        validate_analysis_request(body,db,settings,engine.providers)
        if sum(t["status"] in {"queued","running"} for t in db.tasks())>=settings.max_queued_jobs:raise HTTPException(429,"The workspace task queue is full. Try again later.")
        run_id=db.create_run(body.model_dump());db.audit("analysis.created",run_id)
        return {"id":run_id,"status":"queued"}

    @app.get("/api/analyses")
    def analyses():return db.runs()

    @app.get("/api/analyses/{run_id}")
    def analysis(run_id:str):
        try:return db.run(run_id)
        except ValueError as exc:raise HTTPException(404,str(exc)) from exc

    @app.post("/api/analyses/{run_id}/review")
    def review(run_id:str,body:ReviewInput,request:Request):
        require(request,"admin","reviewer")
        reviewer=request.state.user["name"] if request.state.user else body.reviewer
        if len(reviewer.strip())<2:raise HTTPException(422,"Enter a reviewer name.")
        if body.decision=="rejected" and not body.notes:raise HTTPException(422,"Add a reviewer note explaining what should change.")
        try:return db.review(run_id,body.decision,reviewer,body.notes)
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc

    @app.get("/api/analyses/{run_id}/export")
    def export(run_id:str):
        try:run=db.approved_run(run_id)
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc
        return JSONResponse(run,headers={"Content-Disposition":f'attachment; filename="market-lens-{run_id}.json"'})

    @app.post("/api/analyses/{run_id}/cancel")
    def cancel_analysis(run_id:str,request:Request):
        require(request,"admin","analyst")
        task=db.analysis_task(run_id)
        if not task:raise HTTPException(404,"Analysis task not found.")
        db.cancel_task(task["id"])
        return db.run(run_id)

    @app.post("/api/analyses/{run_id}/retry",status_code=202)
    def retry_analysis(run_id:str,request:Request):
        require(request,"admin","analyst")
        task=db.analysis_task(run_id)
        if not task:raise HTTPException(404,"Analysis task not found.")
        result=db.retry_task(task["id"])
        return {"id":result["run_id"],"status":"queued"}

    @app.get("/api/tasks")
    def tasks():return db.tasks()

    @app.get("/api/tasks/{task_id}")
    def task(task_id:str):
        try:return db.task(task_id)
        except ValueError as exc:raise HTTPException(404,str(exc)) from exc

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel_task(task_id:str,request:Request):require(request,"admin","analyst");return db.cancel_task(task_id)

    @app.post("/api/tasks/{task_id}/retry",status_code=202)
    def retry_task(task_id:str,request:Request):require(request,"admin","analyst");return db.retry_task(task_id)

    @app.post("/api/benchmarks")
    def benchmark(request:Request):require(request,"admin","analyst");return Engine(db.scoped(),settings).benchmark()

    @app.get("/api/benchmarks")
    def benchmarks():return db.latest_benchmark()

    try:
        from .extensions import register_extensions
    except ImportError:
        register_extensions=None
    if register_extensions:register_extensions(app,db,engine,settings)
    app.mount("/static",StaticFiles(directory=ROOT/"app"/"static"),name="static")
    frontend=ROOT/"frontend"/"dist"
    if (frontend/"assets").is_dir():app.mount("/assets",StaticFiles(directory=frontend/"assets"),name="frontend-assets")

    @app.get("/")
    def index():return FileResponse(frontend/"index.html" if (frontend/"index.html").is_file() else ROOT/"app"/"static"/"index.html")

    return app


app=create_app()
