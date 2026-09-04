"""SQLModel storage on SQLite.

Planograms and variants are stored as JSON documents keyed by their id - we
never shred the planogram into relational tables, it is a document contract
and resolve() works on the dict (CLAUDE.md). Sessions, events and
experiments get their own small tables, also holding a JSON document per
row.

DATABASE_URL is read from the environment, defaulting to
sqlite:///./shoppertwin.db.
"""
import json
import os
from pathlib import Path
from typing import Iterator, Optional

from jsonschema import Draft7Validator
from sqlmodel import Field, Session, SQLModel, create_engine

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = ROOT / "schemas"
PLANOGRAMS_DIR = ROOT / "data" / "planograms"
VARIANTS_DIR = ROOT / "data" / "variants"

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./shoppertwin.db")

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)


class PlanogramRecord(SQLModel, table=True):
    __tablename__ = "planograms"
    planogram_id: str = Field(primary_key=True)
    data: str


class VariantRecord(SQLModel, table=True):
    __tablename__ = "variants"
    variant_id: str = Field(primary_key=True)
    data: str


class SessionRecord(SQLModel, table=True):
    __tablename__ = "sessions"
    session_id: str = Field(primary_key=True)
    data: str


class EventRecord(SQLModel, table=True):
    __tablename__ = "events"
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    data: str


class ExperimentRecord(SQLModel, table=True):
    __tablename__ = "experiments"
    experiment_id: str = Field(primary_key=True)
    data: str


def init_db() -> None:
    """Create all tables on the current `engine` if they don't exist yet.

    Reads the module-global `engine` at call time (not a value captured at
    import time), so tests can redirect table creation - along with every
    other DB access - by monkeypatching api.app.db.engine before the app
    starts.
    """
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: yields a DB session bound to the current `engine`."""
    with Session(engine) as session:
        yield session


def seed_from_disk(session: Session) -> None:
    """Load data/planograms/*.json and data/variants/*.json into the DB if
    they are not already present, keyed by their id. This is what makes
    GET /variants/{id}/resolved work from a fresh clone with no manual setup.
    """
    for path in sorted(PLANOGRAMS_DIR.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        planogram_id = doc["planogram_id"]
        if session.get(PlanogramRecord, planogram_id) is None:
            session.add(PlanogramRecord(planogram_id=planogram_id, data=json.dumps(doc)))

    for path in sorted(VARIANTS_DIR.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        variant_id = doc["variant_id"]
        if session.get(VariantRecord, variant_id) is None:
            session.add(VariantRecord(variant_id=variant_id, data=json.dumps(doc)))

    session.commit()


def seed_all() -> None:
    """seed_from_disk() using a fresh session bound to the current `engine`."""
    with Session(engine) as session:
        seed_from_disk(session)


_validator_cache: dict = {}


def get_validator(schema_filename: str) -> Draft7Validator:
    """Return a cached jsonschema.Draft7Validator for schemas/<schema_filename>."""
    if schema_filename not in _validator_cache:
        schema = json.loads((SCHEMAS_DIR / schema_filename).read_text(encoding="utf-8"))
        _validator_cache[schema_filename] = Draft7Validator(schema)
    return _validator_cache[schema_filename]
