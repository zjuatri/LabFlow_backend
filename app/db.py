from __future__ import annotations

import os
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session


def _default_sqlite_url() -> str:
    # Relative to current working directory (LabFlow_backend)
    return "sqlite:///./labflow.db"


DATABASE_URL = os.getenv("DATABASE_URL", _default_sqlite_url())

# SQLite needs this flag for multi-threaded web servers
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}
elif DATABASE_URL.startswith("mysql"):
    # Avoid long hangs during app startup if DB is unreachable.
    connect_args = {"connect_timeout": 5}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    future=True,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
