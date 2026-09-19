"""One-command live end-to-end check (dev utility, not part of pytest).

Starts uvicorn in-process on 127.0.0.1:8124 (E2E_PORT to override) backed by
DATABASE_URL from backend/.env, runs scripts/e2e_check.py against it (inspection -> product
-> analyze -> compliance -> violations -> verify -> report), then shuts
down. Writes clearly-labeled E2E rows to the database.

Usage (from backend/, venv active):
    python scripts/run_e2e_live.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get("E2E_PORT", "8124"))  # 8123 often taken; override here
BASE = f"http://127.0.0.1:{PORT}"

sys.path.insert(0, str(HERE.parent))  # backend/ holds the `app` package


def wait_healthy(timeout: int = 240) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE + "/healthz", timeout=5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(3)
    return False


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(HERE.parent / ".env")
    import uvicorn  # noqa: E402

    from app.main import app  # noqa: E402  (fails fast if DB unreachable)

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        if not wait_healthy():
            print("server did not become healthy")
            return 2
        print(f"server up at {BASE}")
        proc = subprocess.run(
            [sys.executable, str(HERE / "e2e_check.py"), BASE],
            cwd=str(HERE.parent))
        return proc.returncode
    finally:
        server.should_exit = True
        thread.join(timeout=30)


if __name__ == "__main__":
    raise SystemExit(main())
