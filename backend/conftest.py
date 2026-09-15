import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.factory import create_app  # noqa: E402
from app.repositories.memory import MemoryRepo  # noqa: E402


def make_repo() -> MemoryRepo:
    return MemoryRepo()


def make_client(repo: MemoryRepo) -> TestClient:
    return TestClient(create_app(repo))
