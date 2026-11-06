"""Explicit local PostgreSQL configuration and short, sanitized transactions."""

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import os
import logging

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from ..telegram.models import InteractionError


class PersistenceError(InteractionError):
    """Safe boundary: never reflect SQL, bound parameters or connection details."""


@dataclass(frozen=True, repr=False)
class DatabaseSettings:
    url: str
    scope: str = 'p5-local'

    def __post_init__(self):
        try:
            parsed = make_url(self.url)
            valid = (parsed.drivername == 'postgresql+psycopg'
                     and parsed.host in {'localhost', '127.0.0.1', '::1'}
                     and bool(parsed.database) and bool(parsed.username)
                     and not parsed.query and 0 < len(self.scope) <= 64
                     and all(c.isalnum() or c in '-_' for c in self.scope))
        except Exception:
            valid = False
        if not valid:
            raise PersistenceError('Изисква се валидна локална PostgreSQL конфигурация.') from None

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None):
        source = os.environ if environ is None else environ
        return cls(source.get('DATABASE_URL', ''), source.get('P5_DB_SCOPE', 'p5-local'))


class Database:
    def __init__(self, settings: DatabaseSettings):
        self.settings = settings
        # No I/O until a transaction/explicit migration. Parameters and SQL
        # errors stay behind this module's safe boundary, including on commit.
        self.engine = create_engine(settings.url, pool_pre_ping=True, echo=False,
                                    hide_parameters=True, logging_name='p5_profiles',
                                    connect_args={'connect_timeout': 5, 'options': '-c statement_timeout=10000 -c lock_timeout=5000'})
        # hide_parameters covers binds, not SQLAlchemy DEBUG result rows.
        # Keep this dedicated engine and driver quiet even if root logs DEBUG.
        logging.getLogger('sqlalchemy.engine.Engine.p5_profiles').setLevel(logging.WARNING)
        logging.getLogger('psycopg').setLevel(logging.WARNING)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def transaction(self):
        try:
            with self.sessions.begin() as session:
                yield session
        except SQLAlchemyError:
            raise PersistenceError('Операцията с базата данни не беше завършена. Опитайте отново.') from None

    def close(self):
        self.engine.dispose()
