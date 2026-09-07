"""Database backend abstraction for Increment 5A.

SQLite remains the only implemented runtime backend. PostgreSQL selection is
parsed and validated, but intentionally fails before any connection attempt.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterator
import sqlite3

class DatabaseErrorCategory(str, Enum):
    BUSINESS_CONFLICT = "BusinessConflict"
    AUTHORIZATION_FAILURE = "AuthorizationFailure"
    VALIDATION_FAILURE = "ValidationFailure"
    TRANSIENT_DATABASE_FAILURE = "TransientDatabaseFailure"
    CONNECTION_FAILURE = "ConnectionFailure"
    INTEGRITY_FAILURE = "IntegrityFailure"
    UNEXPECTED_DATABASE_FAILURE = "UnexpectedDatabaseFailure"

# Stable aliases for callers that prefer the category names directly.
BusinessConflict = DatabaseErrorCategory.BUSINESS_CONFLICT
AuthorizationFailure = DatabaseErrorCategory.AUTHORIZATION_FAILURE
ValidationFailure = DatabaseErrorCategory.VALIDATION_FAILURE
TransientDatabaseFailure = DatabaseErrorCategory.TRANSIENT_DATABASE_FAILURE
ConnectionFailure = DatabaseErrorCategory.CONNECTION_FAILURE
IntegrityFailure = DatabaseErrorCategory.INTEGRITY_FAILURE
UnexpectedDatabaseFailure = DatabaseErrorCategory.UNEXPECTED_DATABASE_FAILURE

class BackendNotImplementedError(RuntimeError):
    """Raised when a valid but not-yet-supported backend is selected."""

class DatabaseBackend:
    name: str
    def connect(self): raise NotImplementedError
    def begin(self, connection): raise NotImplementedError
    def commit(self, connection): connection.commit()
    def rollback(self, connection): connection.rollback()
    @contextmanager
    def transaction(self, connection) -> Iterator[Any]:
        self.begin(connection)
        try:
            yield connection
        except Exception:
            self.rollback(connection)
            raise
        else:
            self.commit(connection)
    def classify_error(self, error: BaseException) -> DatabaseErrorCategory:
        return classify_database_error(error)

@dataclass(frozen=True)
class SQLiteBackend(DatabaseBackend):
    database_path: Path | str
    name: str = "sqlite"

    def connect(self):
        path = str(self.database_path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def begin(self, connection):
        connection.execute("BEGIN IMMEDIATE")

class PostgreSQLBackend(DatabaseBackend):
    name = "postgresql"
    def __init__(self, database_url: str):
        self.database_url = database_url
    def connect(self):
        raise BackendNotImplementedError(
            "PostgreSQL runtime support is not implemented in Increment 5A"
        )

@dataclass(frozen=True)
class ParsedDatabaseTarget:
    backend: str
    database_path: Path | None = None
    database_url: str = ""

def classify_database_error(error: BaseException) -> DatabaseErrorCategory:
    text = str(error).lower()
    if isinstance(error, sqlite3.IntegrityError):
        return IntegrityFailure
    if isinstance(error, sqlite3.OperationalError):
        if any(word in text for word in ("locked", "busy", "timeout")):
            return TransientDatabaseFailure
        if any(word in text for word in ("unable to open", "cannot open", "disk i/o")):
            return ConnectionFailure
        return UnexpectedDatabaseFailure
    if isinstance(error, (sqlite3.InterfaceError, sqlite3.DatabaseError)):
        return ConnectionFailure
    if isinstance(error, (ValueError, TypeError)): return ValidationFailure
    if isinstance(error, PermissionError): return AuthorizationFailure
    return UnexpectedDatabaseFailure

def backend_from_settings(settings) -> DatabaseBackend:
    backend = (getattr(settings, "database_backend", "") or "").lower()
    if not backend:
        from urllib.parse import urlparse
        backend = "postgresql" if urlparse(getattr(settings, "database_url", "") or "").scheme in {"postgres", "postgresql"} else "sqlite"
    if backend == "sqlite": return SQLiteBackend(settings.database_path)
    if backend in {"postgres", "postgresql"}:
        return PostgreSQLBackend(settings.database_url)
    raise ValueError("DATABASE_BACKEND is invalid")

def parse_database_target(*, backend: str, database_path=None, database_url: str = "") -> ParsedDatabaseTarget:
    backend = (backend or "").strip().lower()
    if backend == "sqlite":
        if not database_path: raise ValueError("DATABASE_PATH is required for SQLite")
        return ParsedDatabaseTarget("sqlite", Path(database_path), "")
    if backend in {"postgres", "postgresql"}:
        from urllib.parse import urlparse
        parsed=urlparse(database_url or "")
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.netloc:
            raise ValueError("DATABASE_URL is invalid")
        return ParsedDatabaseTarget("postgresql", None, database_url)
    raise ValueError("DATABASE_BACKEND is invalid")
