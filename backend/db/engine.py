"""Connecting to PostgreSQL."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def make_engine(url, **options):
    """An engine for `url` (already normalised by settings.database_url).

    pool_pre_ping replaces connections the database has closed (after a
    database restart, for example) instead of failing the next request.
    """

    options.setdefault("pool_pre_ping", True)

    if "poolclass" not in options:
        options.setdefault("pool_size", 5)
        options.setdefault("max_overflow", 5)

    return create_engine(url, **options)


def make_session_factory(engine):
    # expire_on_commit=False: rows stay readable after commit, so results
    # can be turned into JSON once the transaction is over
    return sessionmaker(engine, expire_on_commit=False)
