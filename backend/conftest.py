import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.factory import create_app  # noqa: E402
from app.repositories.memory import MemoryRepo  # noqa: E402


@pytest.fixture(autouse=True)
def _test_identity_mode(monkeypatch):
    """Hermetic auth for unit tests: the local backend/.env (dotenv file)
    must not leak Clerk/dev identity into the suite. Tests use OS
    environment only; individual tests opt into Clerk mode explicitly."""
    from pydantic_settings import SettingsConfigDict

    from app.core import config as config_mod

    class TestSettings(config_mod.Settings):
        model_config = SettingsConfigDict(env_file=None, extra="ignore")

    for var in ("CLERK_JWKS_URL", "CLERK_AUDIENCE", "DEV_AUTH_ROLE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config_mod, "Settings", TestSettings)
    config_mod.get_settings.cache_clear()
    yield
    config_mod.get_settings.cache_clear()


def make_repo() -> MemoryRepo:
    return MemoryRepo()


def make_client(repo: MemoryRepo) -> TestClient:
    return TestClient(create_app(repo))
