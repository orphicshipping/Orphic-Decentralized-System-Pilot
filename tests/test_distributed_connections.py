"""Real PostgreSQL transaction state; run both direct and through PgBouncer."""
from uuid import uuid4

import pytest

from ciw.distributed_queue import _connection, install_schema
from test_distributed_coordinator import runtime, active_connection


def test_transaction_settings_and_no_named_prepared_state(runtime):
    with _connection(runtime.dsn) as db:
        for value in range(12):
            assert db.execute("SELECT %s::int AS n", (value,)).fetchone() == {"n": value}
        assert db.execute("SELECT count(*) AS n FROM pg_prepared_statements").fetchone()["n"] == 0
        assert db.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"] == "read committed"
        assert db.execute("SHOW synchronous_commit").fetchone()["synchronous_commit"] == "on"
        assert db.execute("SHOW statement_timeout").fetchone()["statement_timeout"] == "5s"
        assert db.execute("SHOW lock_timeout").fetchone()["lock_timeout"] == "2s"
        assert db.execute("SHOW idle_in_transaction_session_timeout").fetchone()["idle_in_transaction_session_timeout"] == "5s"
        assert db.execute("SHOW search_path").fetchone()["search_path"] == "pg_catalog"


def test_transaction_abort_rolls_back_effects_and_local_settings(runtime):
    identifier = "rollback-" + uuid4().hex
    with active_connection(runtime.dsn) as db:
        before = db.execute("SELECT current_setting('statement_timeout'),current_setting('lock_timeout'),"
                            "current_setting('search_path'),pg_backend_pid()").fetchone()
    with pytest.raises(ValueError, match="deliberate local abort"):
        with _connection(runtime.dsn) as db:
            pid = db.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
            db.execute("INSERT INTO public.qualification_effects VALUES(%s,1)", (identifier,))
            raise ValueError("deliberate local abort")
    with active_connection(runtime.dsn) as db:
        after = db.execute("SELECT current_setting('statement_timeout'),current_setting('lock_timeout'),"
                           "current_setting('search_path'),pg_backend_pid()").fetchone()
        assert db.execute("SELECT count(*) FROM public.qualification_effects WHERE job_id=%s",
                          (identifier,)).fetchone()[0] == 0
    assert before[:3] == after[:3]
    if runtime.pooler is not None:
        # Sequential clients really reused a server; a new session cannot hide a leak.
        assert before[3] == pid == after[3]


def test_committed_settings_do_not_leak_into_next_pooled_client(runtime):
    with active_connection(runtime.dsn) as db:
        before = db.execute("SELECT current_setting('statement_timeout'),current_setting('search_path'),"
                            "pg_backend_pid()").fetchone()
    with _connection(runtime.dsn) as db:
        pid = db.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
    with active_connection(runtime.dsn) as db:
        after = db.execute("SELECT current_setting('statement_timeout'),current_setting('search_path'),"
                           "pg_backend_pid()").fetchone()
    assert before[:2] == after[:2]
    if runtime.pooler is not None:
        assert before[2] == pid == after[2]


def test_schema_transaction_advisory_lock_released_on_commit_and_abort(runtime):
    # Administrative DDL is deliberately run on the direct connection.
    install_schema(runtime.direct_dsn)
    with active_connection(runtime.direct_dsn) as db:
        assert db.execute("SELECT pg_try_advisory_xact_lock(678044119137)").fetchone()[0] is True
    with pytest.raises(ValueError):
        with _connection(runtime.dsn) as locked:
            locked.execute("SELECT pg_advisory_xact_lock(678044119137)")
            with active_connection(runtime.direct_dsn) as other:
                assert other.execute("SELECT pg_try_advisory_xact_lock(678044119137)").fetchone()[0] is False
            raise ValueError("abort transaction-scoped migration lock")
    with active_connection(runtime.direct_dsn) as db:
        assert db.execute("SELECT pg_try_advisory_xact_lock(678044119137)").fetchone()[0] is True
