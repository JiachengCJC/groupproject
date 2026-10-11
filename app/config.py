from dataclasses import dataclass
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
    jev_key: str = ""
    jev_model: str = "jev-latest"
    api_timeout: float = 45.0
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = ""
    ollama_vision_model: str = ""
    ollama_timeout: float = 180.0
    ollama_context: int = 8192
    ocr_languages: str = "eng+chi_sim+chi_tra"
    whisper_model: str = ""

    @classmethod
    def from_env(cls):
        load_env()
        return cls(
            db_path=Path(os.getenv("DATABASE_PATH", str(ROOT / "data" / "market_lens.sqlite3"))),
            openai_key=os.getenv("OPENAI_API_KEY", ""),
            openai_model=os.getenv("OPENAI_MODEL", ""),
            jev_key=os.getenv("TYPESAFE_API_KEY", ""),
            jev_model=os.getenv("JEV_MODEL", "jev-latest"),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
            ollama_model=os.getenv("OLLAMA_MODEL", ""),
            ollama_vision_model=os.getenv("OLLAMA_VISION_MODEL", ""),
            ollama_timeout=float(os.getenv("OLLAMA_TIMEOUT", "180")),
            ollama_context=int(os.getenv("OLLAMA_CONTEXT", "8192")),
            ocr_languages=os.getenv("OCR_LANGUAGES", "eng+chi_sim+chi_tra"),
            whisper_model=os.getenv("WHISPER_MODEL", ""),
        )
