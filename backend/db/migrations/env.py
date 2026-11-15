"""Alembic environment: where the database is and what it should contain.

The URL comes from DATABASE_URL. Tests can instead hand over an open
connection in config.attributes["connection"].
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool

from backend.api.settings import database_url
from backend.db.engine import make_engine
from backend.db.models import Base


config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connection = config.attributes.get("connection")

    if connection is not None:
        run_migrations(connection)
        return

    url = database_url(os.environ.get("DATABASE_URL"))

    if url is None:
        raise RuntimeError("Set DATABASE_URL to run migrations")

    engine = make_engine(url, poolclass=pool.NullPool)

    with engine.connect() as connection:
        run_migrations(connection)


def run_migrations_offline():
    url = database_url(os.environ.get("DATABASE_URL")) or "postgresql+psycopg://"
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
