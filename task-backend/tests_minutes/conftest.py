import os
import sys
from pathlib import Path

# Tests must NEVER reach a real database. main.py loads task-backend/.env when it is imported, and that file holds the real
# Atlas URL; an existing (even empty) environment variable wins over .env, so blank it before anything imports main.
os.environ["MONGODB_URL"] = ""
# Same for experiment tracking: .env now holds the real MLflow/DagsHub settings, and tests must never upload anything.
os.environ["MLFLOW_TRACKING_URI"] = ""
os.environ["MLFLOW_DISABLED"] = "true"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from types import SimpleNamespace
from unittest.mock import AsyncMock
import mongomock
import pytest

class Cursor:
    def __init__(self, cursor): self.cursor = cursor
    def sort(self, *args): self.cursor = self.cursor.sort(*args); return self
    def skip(self, count): self.cursor = self.cursor.skip(count); return self
    def limit(self, count): self.cursor = self.cursor.limit(count); return self
    def __aiter__(self):
        async def iterator():
            for doc in self.cursor: yield doc
        return iterator()

class Collection:
    def __init__(self, collection): self.collection = collection
    def find(self, *args, **kwargs): return Cursor(self.collection.find(*args, **kwargs))
    def __getattr__(self, name):
        async def call(*args, **kwargs): return getattr(self.collection, name)(*args, **kwargs)
        return call

class MockDatabase:
    def __init__(self): self.db = mongomock.MongoClient().meeting_db
    def __getitem__(self, name): return Collection(self.db[name])

@pytest.fixture(autouse=True)
def fake_database(monkeypatch):
    from database import database
    db = MockDatabase()
    monkeypatch.setattr(database, "db", db)
    monkeypatch.setattr(database, "client", SimpleNamespace(admin=SimpleNamespace(command=AsyncMock(return_value={"ok":1}))))
    monkeypatch.setattr(database, "connect", AsyncMock())
    monkeypatch.setattr(database, "close", lambda: None)
    yield db


@pytest.fixture(autouse=True)
def no_real_dependency_audit(monkeypatch):
    """The dependency audit is a real LLM call; tests must never make one. Tests of the audit patch this themselves."""
    import main
    from models import DependencyAudit
    monkeypatch.setattr(main, "run_dependency_audit", AsyncMock(return_value=DependencyAudit(edges=[])))
