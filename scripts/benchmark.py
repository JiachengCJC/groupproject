"""Headless regression suite using a temporary database; leaves your workspace unchanged."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import Settings
from app.database import Database
from app.engine import Engine
from app.seed import seed

with tempfile.TemporaryDirectory() as tmp:
    settings = Settings(db_path=Path(tmp)/"benchmark.sqlite3")
    db = Database(settings.db_path)
    db.initialize()
    seed(db)
    result = Engine(db, settings).benchmark()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["pass_rate"] == 1 else 1)
