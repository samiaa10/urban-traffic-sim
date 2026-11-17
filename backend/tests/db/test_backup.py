"""scripts/backup_database.sh and scripts/restore_database.sh, on the test
database: a saved run survives a backup, a wiped database and a restore,
and a bad backup changes nothing.

Skipped unless pg_dump and pg_restore of the server's major version are
installed (a mismatched pg_dump either refuses or writes settings an older
server rejects)."""

import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.tests.db.conftest import ROOT, migrate, reset_schema


BACKUP = ROOT / "scripts" / "backup_database.sh"
RESTORE = ROOT / "scripts" / "restore_database.sh"


def major_version(program):
    output = subprocess.run([program, "--version"], capture_output=True, text=True).stdout
    return int(re.search(r"(\d+)", output).group(1))


@pytest.fixture
def tools(engine):
    if not (shutil.which("pg_dump") and shutil.which("pg_restore")):
        pytest.skip("pg_dump and pg_restore are not installed")
    with engine.connect() as connection:
        server = int(connection.execute(text("SHOW server_version_num")).scalar()) // 10000
    if major_version("pg_dump") != server:
        pytest.skip(f"pg_dump {major_version('pg_dump')} does not match PostgreSQL {server}")


def run(*arguments):
    return subprocess.run([str(a) for a in arguments], capture_output=True, text=True)


def test_a_backup_restores_a_wiped_database(tools, make_app, engine, database_url_for_tests, tmp_path):
    client = TestClient(make_app())
    simulation_id = client.post("/simulation/start", params={"start": 0, "destination": 15}).json()["simulation_id"]
    client.post("/incident/block", params={"start": 5, "destination": 6})
    client.post("/simulation/step")
    before = client.get(f"/simulations/{simulation_id}/history/events")
    assert before.status_code == 200
    before = before.json()
    assert "road_closed" in str(before)

    backup = tmp_path / "norwich.dump"
    made = run(BACKUP, database_url_for_tests, backup)
    assert made.returncode == 0, made.stderr
    assert backup.stat().st_size > 0 and not (tmp_path / "norwich.dump.partial").exists()

    # Lose everything
    client.app.state.context.database.dispose()
    with engine.begin() as connection:
        reset_schema(connection)

    refused = run(RESTORE, backup, database_url_for_tests)     # no --yes
    assert refused.returncode == 2

    restored = run(RESTORE, backup, database_url_for_tests, "--yes")
    assert restored.returncode == 0, restored.stderr
    migrate(engine)     # nothing to do: the backup holds alembic_version

    after = TestClient(make_app())
    restored_events = after.get(f"/simulations/{simulation_id}/history/events")
    assert restored_events.status_code == 200
    # Everything saved before the backup, then the restarted server marks
    # the run interrupted (as after any restart)
    events = restored_events.json()["events"]
    assert events[: len(before["events"])] == before["events"]
    assert [event["type"] for event in events[len(before["events"]):]] == ["run_ended"]
    assert after.get(f"/simulations/{simulation_id}").json()["status"] == "interrupted"


def test_a_damaged_backup_is_refused_and_changes_nothing(tools, engine, database_url_for_tests, tmp_path):
    with engine.connect() as connection:
        tables = connection.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")).scalar()

    damaged = tmp_path / "damaged.dump"
    damaged.write_bytes(b"PGDMP not really a backup")

    assert run(RESTORE, damaged, database_url_for_tests, "--yes").returncode != 0
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")).scalar() == tables


def test_a_failed_backup_leaves_no_file(tools, tmp_path):
    output = tmp_path / "never.dump"

    failed = run(BACKUP, "postgresql://nobody:x@127.0.0.1:1/nothing", output)

    assert failed.returncode != 0
    assert list(tmp_path.iterdir()) == []
