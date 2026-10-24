from dataclasses import dataclass, fields
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path=ROOT / ".env"):
    """Read simple KEY=value settings without overriding the process environment."""
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


@dataclass
class Settings:
    db_path: Path = ROOT / "data" / "market_lens.sqlite3"
    openai_key: str = ""
    openai_model: str = ""
    openai_decision_model: str = "gpt-6-luna"
    api_timeout: float = 45.0
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = ""
    ollama_fast_model: str = "qwen3.5:4b-q4_K_M"
    ollama_quality_model: str = "qwen3.5:9b-q4_K_M"
    ollama_vision_model: str = ""
    ollama_thinking: str = "off"
    ollama_num_predict: int = 2400
    ollama_keep_alive: str = "0"
    ollama_timeout: float = 180.0
    ollama_context: int = 8192
    ocr_languages: str = "eng+chi_sim+chi_tra"
    whisper_model: str = ""
    database_url: str = ""
    analytics_database_url: str = ""
    analytics_role: str = ""
    auth_enabled: bool = False  # Bare Settings is for tests/explicit local development.
    setup_token: str = ""
    admin_email: str = ""
    admin_password: str = ""
    cookie_secure: bool = False
    session_hours: int = 12
    allowed_hosts: str = "localhost,127.0.0.1,[::1],testserver"
    embedded_worker: bool = True
    worker_poll_seconds: float = 0.15
    job_lease_seconds: int = 90
    seed_demo: bool = True
    local_only: bool = False
    orchestration: str = "legacy"
    cloud_protocol: str = "responses"
    cloud_base_url: str = ""
    embedding_provider: str = "lexical"
    embedding_model: str = ""
    embedding_revision: str = ""
    qdrant_url: str = ""
    qdrant_path: str = ""
    qdrant_api_key: str = ""
    retrieval_mode: str = "lexical"
    retrieval_limit: int = 5
    max_agent_steps: int = 3
    max_model_calls: int = 20
    analysis_timeout: int = 300
    mlflow_tracking_uri: str = ""
    mlflow_experiment: str = "Market Lens"
    allow_external_sources: bool = True
    source_allowed_hosts: str = ""
    max_queued_jobs: int = 100
    trace_content: bool = False
    openai_input_usd_per_million: float = -1.0
    openai_output_usd_per_million: float = -1.0
    price_version: str = ""
    openai_decisions_input_usd_per_million: float = -1.0
    openai_decisions_price_version: str = ""

    @classmethod
    def from_env(cls):
        load_env()
        settings = cls(
            db_path=Path(os.getenv("DATABASE_PATH", str(ROOT / "data" / "market_lens.sqlite3"))),
            openai_key=os.getenv("OPENAI_API_KEY", ""),
            openai_model=os.getenv("OPENAI_MODEL", ""),
            openai_decision_model=os.getenv("OPENAI_DECISION_MODEL", "gpt-6-luna"),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
            ollama_model=os.getenv("OLLAMA_MODEL", ""),
            ollama_vision_model=os.getenv("OLLAMA_VISION_MODEL", ""),
            ollama_timeout=float(os.getenv("OLLAMA_TIMEOUT", "180")),
            ollama_context=int(os.getenv("OLLAMA_CONTEXT", "8192")),
            ocr_languages=os.getenv("OCR_LANGUAGES", "eng+chi_sim+chi_tra"),
            whisper_model=os.getenv("WHISPER_MODEL", ""),
        )
        aliases = {"database_url": "DATABASE_URL"}
        for field in fields(cls):
            env = aliases.get(field.name, field.name.upper())
            if env not in os.environ:
                continue
            value = os.environ[env]
            default = getattr(settings, field.name)
            if isinstance(default, bool):
                if value.lower() not in {"1", "0", "true", "false", "yes", "no"}:
                    raise ValueError(f"{env} must be true or false.")
                value = value.lower() in {"1", "true", "yes"}
            elif isinstance(default, int):
                value = int(value)
            elif isinstance(default, float):
                value = float(value)
            elif isinstance(default, Path):
                continue
            setattr(settings, field.name, value)
        settings.auth_enabled = os.getenv("AUTH_ENABLED", "true").lower() in {"true", "1", "yes"}
        settings.orchestration = os.getenv("ORCHESTRATION", "langgraph")
        settings.analytics_role = os.getenv("ANALYTICS_ROLE", "market_lens_analytics" if settings.analytics_database_url else "")
        settings.embedded_worker = os.getenv("EMBEDDED_WORKER", "false" if settings.database_url else "true").lower() in {"true", "1", "yes"}
        if settings.job_lease_seconds < 10 or settings.worker_poll_seconds <= 0:
            raise ValueError("Worker lease must be at least 10 seconds and poll interval must be positive.")
        if settings.session_hours < 1 or settings.max_queued_jobs < 1:
            raise ValueError("Session duration and queue limit must be positive.")
        if settings.ollama_thinking not in {"off", "on", "default"}:
            raise ValueError("OLLAMA_THINKING must be off, on or default.")
        if not 256 <= settings.ollama_num_predict <= 8192:
            raise ValueError("OLLAMA_NUM_PREDICT must be between 256 and 8192.")
        if not settings.ollama_fast_model.strip() or not settings.ollama_quality_model.strip():
            raise ValueError("Local Fast and Quality model names must not be empty.")
        return settings
