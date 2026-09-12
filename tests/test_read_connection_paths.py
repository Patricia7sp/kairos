"""Filesystem names cannot become SQLite URI options; readers include active WAL."""

import sqlite3

import pytest

from kairos_state import connect, initialize_schema
from kairos_state.connection import read_connection
from kairos_state.repositories import SessionRepository
from kairos_state.repositories.usage import BillingRoute, TokenDelta, UsageRepository
from kairos_state.usage_summary import read_usage_summary


@pytest.mark.parametrize("name", ["unexpected.db?mode=rwc&ignored=", "name#part", "name%3fpart"])
def test_missing_special_path_does_not_create_a_database(tmp_path, name):
    home = tmp_path / name
    assert read_usage_summary(home)["availability"] == "unavailable"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", ["dir?mode=rwc&ignored=", "dir#fragment", "dir%3fspace á"])
def test_special_directory_names_open_the_literal_database_read_only(tmp_path, name):
    home = tmp_path / name
    home.mkdir()
    path = home / "state.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE marker (value TEXT)")
        db.execute("INSERT INTO marker VALUES ('literal')")
    before = path.read_bytes()
    with read_connection(path) as reader:
        assert reader.execute("SELECT value FROM marker").fetchone()[0] == "literal"
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.execute("INSERT INTO marker VALUES ('changed')")
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [home]


def test_relative_path_uses_the_literal_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with sqlite3.connect("relative?#%.db") as db:
        db.execute("CREATE TABLE marker (value INTEGER)")
    with read_connection("relative?#%.db") as reader:
        assert reader.execute("SELECT COUNT(*) FROM marker").fetchone()[0] == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["relative?#%.db"]


def test_summary_reads_committed_usage_from_active_wal(tmp_path):
    db = connect(tmp_path / "state.db")
    try:
        initialize_schema(db)
        db.execute("PRAGMA wal_autocheckpoint=0")
        SessionRepository(db).create("private", "web")
        usage = UsageRepository(db)
        usage.queue(
            BillingRoute("private", "private", "private", "https://private", "api"),
            TokenDelta(api_call_count=1, input_tokens=17, output_tokens=4),
        )
        usage.flush(now=100)
        assert (tmp_path / "state.db-wal").stat().st_size > 0
        before = list(db.execute("SELECT * FROM session_model_usage"))
        report = read_usage_summary(tmp_path)
        assert report["availability"] == "available"
        assert report["totals"]["tokens"] == 21
        assert report["totals"]["requests"] == 1
        assert list(db.execute("SELECT * FROM session_model_usage")) == before
        assert {p.name for p in tmp_path.iterdir()} <= {"state.db", "state.db-wal", "state.db-shm"}
    finally:
        db.close()
