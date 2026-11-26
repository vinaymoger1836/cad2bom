"""Create .env from .env.example with random local secrets. Never overwrites an existing .env.

.env is git-ignored. Usage: uv run python scripts/init_env.py   (or `make env`)
"""

import contextlib
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    env = ROOT / ".env"
    example = ROOT / ".env.example"
    (ROOT / "data").mkdir(exist_ok=True)
    if env.exists():
        print(".env already exists; leaving it alone")
        return 0
    text = example.read_text(encoding="utf-8")
    text = text.replace("POSTGRES_PASSWORD=change-me", f"POSTGRES_PASSWORD={secrets.token_hex(24)}")
    text = text.replace("C2B_ADMIN_SECRET=change-me", f"C2B_ADMIN_SECRET={secrets.token_hex(32)}")
    if hasattr(os, "getuid"):  # Linux/macOS: keep data/ writable by the host user
        text = text.replace("C2B_UID=1000", f"C2B_UID={os.getuid()}")
        text = text.replace("C2B_GID=1000", f"C2B_GID={os.getgid()}")
    env.write_text(text, encoding="utf-8")
    with contextlib.suppress(OSError):
        env.chmod(0o600)
    print("created .env with random database and admin secrets (git-ignored)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
