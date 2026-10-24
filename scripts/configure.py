"""Create local deployment secrets without replacing existing provider credentials."""
import os
from pathlib import Path
import secrets
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def configure(path=ROOT / ".env"):
    if path.is_symlink():
        raise ValueError("Refusing to write a symlinked configuration file.")
    content = path.read_text() if path.exists() else (ROOT / ".env.example").read_text()
    lines = content.splitlines()
    present = {}
    for i, line in enumerate(lines):
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            present[key.strip()] = (i, value.strip())
    for key in ("POSTGRES_PASSWORD", "DATABASE_PASSWORD", "ANALYTICS_PASSWORD", "SETUP_TOKEN"):
        if key in present and present[key][1]:
            continue
        line = key + "=" + secrets.token_urlsafe(32)
        if key in present:
            lines[present[key][0]] = line
        else:
            lines.append(line)
    # Preserve the last usable configuration even if writing or replacement fails.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".market-lens-config-", delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write("\n".join(lines) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_symlink():
            raise ValueError("Refusing to replace a symlinked configuration file.")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print("Configuration saved to .env; existing values were preserved. Use SETUP_TOKEN from this file for first-time account setup.")


if __name__ == "__main__":
    configure()
