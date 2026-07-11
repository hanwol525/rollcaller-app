"""Synchronous database engine and session management for SQLModel."""
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import QueuePool, StaticPool

from app.config import settings


def _build_engine():
    url = settings.database_url
    connect_args = {}
    poolclass = None
    # SQLite needs special handling for in-memory / file-based dev databases
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        # StaticPool keeps a single connection shared across threads so that
        # in-memory SQLite survives across sessions in tests.
        poolclass = StaticPool
    if poolclass is not None:
        return create_engine(
            url,
            echo=False,
            connect_args=connect_args,
            poolclass=poolclass,
        )
    # Postgres: configure pool for the Cloud SQL Auth Proxy, which drops idle
    # connections after ~10 min. pool_pre_ping checks liveness before reuse
    # (prevents 500 on stale connections); pool_recycle caps connection age
    # below the proxy's idle timeout.
    return create_engine(
        url,
        echo=False,
        connect_args=connect_args,
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=300,  # 5 min — well under the proxy's ~10 min idle timeout
    )


engine = _build_engine()


def init_db():
    """Create all tables. Called at app startup (after models are imported)."""
    # Import models so SQLModel.metadata is populated before create_all.
    # This import is here (not at top) to avoid circular imports.
    from app import models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    """FastAPI dependency that yields a synchronous SQLModel Session."""
    with Session(engine) as session:
        yield session