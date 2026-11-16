"""
Database engine, session management, and initialization.
"""
from contextlib import contextmanager
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.config.settings import settings
from app.database.entities import Base

def _sqlalchemy_database_url(value: str) -> str:
    """Normalize Render's postgres URL and select the installed psycopg 3 driver."""
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value[len("postgres://"):]
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value[len("postgresql://"):]
    return value


database_url = _sqlalchemy_database_url(settings.DATABASE_URL)
is_sqlite = database_url.startswith("sqlite")
connect_args = {"check_same_thread": False} if is_sqlite else {}
engine = create_engine(
    database_url,
    echo=False,
    connect_args=connect_args
)

# Enable foreign keys for SQLite
if is_sqlite:
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
