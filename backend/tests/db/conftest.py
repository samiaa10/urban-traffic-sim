"""Fixtures for the database tests (backend/tests/db).

These run against a real PostgreSQL database named by TEST_DATABASE_URL,
for example

    TEST_DATABASE_URL=postgresql://norwich:norwich@localhost:5433/norwich_sim_test

and are skipped when it is not set (so the rest of the suite still runs
anywhere). SQLite is deliberately not used as a stand-in: the schema uses
PostgreSQL enums, arrays, UUIDs, partial unique indexes and check
constraints, and tests that passed on another database would prove little.

The database is wiped (its public schema dropped) at the start, so its
name must contain "test".
"""

import os
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("alembic")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from backend.api.settings import database_url  # noqa: E402
from backend.db.engine import make_engine  # noqa: E402


ROOT = Path(__file__).resolve().parents[3]


def alembic_config(connection):
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["connection"] = connection
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def database_url_for_tests():
    url = database_url(os.environ.get("TEST_DATABASE_URL"))

    if url is None:
        pytest.skip("TEST_DATABASE_URL is not set (database tests need PostgreSQL)")

    if "test" not in (make_url(url).database or ""):
        pytest.exit(
            "Refusing to run: the TEST_DATABASE_URL database name must contain "
            "'test', because the tests wipe it."
        )

    return url


def reset_schema(connection):
    connection.execute(text("DROP SCHEMA public CASCADE"))
    connection.execute(text("CREATE SCHEMA public"))


def migrate(engine, revision="head"):
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), revision)


def downgrade(engine, revision="base"):
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), revision)


@pytest.fixture(scope="session")
def engine(database_url_for_tests):
    """An engine on a freshly wiped and migrated test database."""

    engine = make_engine(database_url_for_tests)

    with engine.begin() as connection:
        reset_schema(connection)

    migrate(engine)

    yield engine

    engine.dispose()


@pytest.fixture
def session(engine):
    """A session whose work is all rolled back after the test.

    The test may commit (or hit an IntegrityError and roll back): those
    become savepoints inside one outer transaction that is thrown away.
    """

    connection = engine.connect()
    outer = connection.begin()
    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )

    yield session

    session.close()
    outer.rollback()
    connection.close()
