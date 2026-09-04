"""Shared fixtures for api/tests.

Every test gets its own isolated in-memory SQLite database, so this suite
never touches the developer's real shoppertwin.db file. We redirect *every*
DB access - including the app's startup-time table creation and seeding -
by monkeypatching api.app.db.engine before the app's lifespan runs (db.py's
functions read the module-global `engine` fresh on every call, so this
covers them regardless of how they were imported elsewhere), and we also
wire up FastAPI's dependency_overrides for get_session for request-time
access, per the S3 task brief.

The same isolation applies to prediction locks (S14). POST /sessions writes
`predictions/{session_id}.json`, and those files are committed evidence, so
`predictions_dir` is autouse: every test in this package gets its own tmp
directory and the repository's real `predictions/` folder is never written
to by the suite.
"""
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine
from sqlmodel.pool import StaticPool

from api.app import db as db_module
from api.app import prediction as prediction_module
from api.app.main import app


@pytest.fixture(name="predictions_dir", autouse=True)
def predictions_dir_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the prediction-lock directory at a per-test tmp folder.

    prediction.py reads the module-global PREDICTIONS_DIR fresh on every call
    (the same pattern db.py uses for `engine`), so patching it here covers the
    router, the websocket ingest and any direct call.
    """
    directory = tmp_path / "predictions"
    directory.mkdir()
    monkeypatch.setattr(prediction_module, "PREDICTIONS_DIR", directory)
    return directory


@pytest.fixture(name="test_engine")
def test_engine_fixture(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(db_module, "engine", engine)
    yield engine


@pytest.fixture(name="client")
def client_fixture(test_engine) -> Iterator[TestClient]:
    def get_session_override() -> Iterator[Session]:
        with Session(test_engine) as session:
            yield session

    app.dependency_overrides[db_module.get_session] = get_session_override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
