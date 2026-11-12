import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient on a temporary copy of the real database, so tests never touch user data."""
    db = tmp_path / "test.db"
    shutil.copy(ROOT / "data" / "pokemon_collection.db", db)
    monkeypatch.setenv("POKEMON_DB", str(db))
    from app.main import app

    with TestClient(app) as c:
        yield c
