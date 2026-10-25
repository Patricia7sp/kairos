"""Doctor aguarda a migração que disputa o bootstrap com outro processo."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

from kairos_cli.main import main
from kairos_state import connect, read_schema_version


def test_doctor_retries_initial_migration_held_by_another_writer(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    locked = threading.Event()
    attempted = threading.Event()
    release = threading.Event()

    def writer():
        with closing(connect(tmp_path / "state.db")) as connection:
            connection.execute("BEGIN IMMEDIATE")
            locked.set()
            assert attempted.wait(5), "doctor não tentou iniciar a migração"
            release.wait(1.3)
            connection.rollback()

    def observed_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)

        def trace(statement):
            if statement == "BEGIN IMMEDIATE":
                attempted.set()

        connection.set_trace_callback(trace)
        return connection

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(writer)
        try:
            assert locked.wait(5), "escritor não adquiriu a posse"
            monkeypatch.setattr("kairos_state.connect", observed_connect)
            code = main(["doctor", "--json"])
        finally:
            attempted.set()
            release.set()
            pending.result(timeout=5)

    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["integrity_check"] == "ok"
    with closing(connect(tmp_path / "state.db")) as connection:
        assert report["schema_version"] == read_schema_version(connection)
        assert not connection.in_transaction
