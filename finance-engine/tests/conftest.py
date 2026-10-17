from __future__ import annotations

import os
from collections.abc import Iterator

os.environ.setdefault("FIN_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("FIN_AUTO_CREATE_SCHEMA", "false")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

import finance_engine.models  # noqa: F401
from finance_engine.api.deps import session_factory
from finance_engine.db.base import Base
from finance_engine.db.session import build_engine
from finance_engine.main import create_app


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine = build_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    engine.dispose()


@pytest.fixture
def session(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as s:
        yield s


@pytest.fixture
def client(factory: sessionmaker[Session]) -> TestClient:
    app = create_app()
    app.dependency_overrides[session_factory] = lambda: factory
    return TestClient(app)
