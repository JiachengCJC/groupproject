"""Create a consistent PostgreSQL backup through Docker Compose (no secrets in arguments)."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("backups"))
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--project", default="")
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = args.directory / f"market-lens-{stamp}.dump"
    command = ["docker", "compose", "--env-file", args.env_file]
    if args.project:
        command += ["-p", args.project]
    command += ["exec", "-T", "postgres", "pg_dump", "-U", "postgres", "-d", "market_lens", "-Fc"]
    try:
        with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
            subprocess.run(command, stdout=output, check=True)
        digest = hashlib.sha256()
        with target.open("rb") as backup:
            for block in iter(lambda: backup.read(1024 * 1024), b""):
                digest.update(block)
        checksum = digest.hexdigest()
        manifest = {"created_at": stamp, "file": target.name, "sha256": checksum,
                    "scope": "PostgreSQL: source bytes, datasets, documents, accounts, jobs, reports, approvals, checkpoints and evaluations",
                    "excluded": [".env credentials", "derived Qdrant vectors", "MLflow server volume", "Ollama model weights"]}
        target.with_suffix(".json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        print(f"Backup saved: {target} (SHA256 {checksum})")
    except Exception:
        target.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    main()
