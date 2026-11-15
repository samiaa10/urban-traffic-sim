"""The API's connection to PostgreSQL, checked at start-up."""

import logging
from contextlib import contextmanager
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from backend.db.engine import make_engine, make_session_factory


logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]


class SchemaOutOfDate(RuntimeError):
    pass


def head_revision():
    config = Config(str(ROOT / "alembic.ini"))
    return ScriptDirectory.from_config(config).get_current_head()


class Database:

    def __init__(self, url, **engine_options):
        self.engine = make_engine(url, **engine_options)
        self.sessions = make_session_factory(self.engine)

    def check_schema(self):
        """Refuse to start against a database the migrations have not
        brought up to date (the tables would not match the code)."""

        with self.engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()

        head = head_revision()

        if current != head:
            raise SchemaOutOfDate(
                f"The database is at migration {current or 'none'}, the code "
                f"needs {head}. Run: alembic upgrade head"
            )

        logger.info("Database schema is up to date (migration %s)", head)

    @contextmanager
    def transaction(self):
        """A session that commits if the block succeeds, else rolls back."""

        session = self.sessions()

        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self):
        self.engine.dispose()
