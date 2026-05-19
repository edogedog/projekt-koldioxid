from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine,StaticPool
from sqlalchemy.orm import Session, sessionmaker,declarative_base

# Default: local SQLite i projektroten (enkel start).
DATABASE_URL = "sqlite:///./app.db"


connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, echo=False, future=True, connect_args=connect_args,poolclass=StaticPool)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: ger en DB-session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
Base = declarative_base()