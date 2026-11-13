"""
Database engine, session management, and initialization.
"""
from contextlib import contextmanager
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.config.settings import settings
from app.database.entities import Base

# Create SQLite engine with thread safety and foreign keys enabled
connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args=connect_args
)

# Enable foreign keys for SQLite
if settings.DATABASE_URL.startswith("sqlite"):
    from sqlalchemy import event
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    """Initialize all database tables."""
    Base.metadata.create_all(bind=engine)


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """Provide a transactional database session."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
