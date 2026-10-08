"""SQLAlchemy engine/session. Import `get_db` as a FastAPI dependency."""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from api.core.config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine() -> Engine:
    s = get_settings()
    url = s.sqlalchemy_url
    kwargs: dict = {}
    if not url.startswith("sqlite"):
        kwargs.update(
            pool_size=s.db_pool_size,
            max_overflow=s.db_max_overflow,
            pool_recycle=s.db_pool_recycle,
            pool_pre_ping=True,
        )
        if s.mysql_ssl_ca:
            kwargs["connect_args"] = {"ssl": {"ca": s.mysql_ssl_ca}}
    return create_engine(url, **kwargs)


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    with _session_factory()() as db:
        yield db
