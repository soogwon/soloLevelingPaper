from contextlib import contextmanager

import pytest

from solo_leveling.interfaces.http.remote_operation_controls import PostgresRemoteOperationControls


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class Conn:
    def __init__(self, rows):
        self.rows, self.calls = list(rows), []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return Result(self.rows.pop(0))


class Pool:
    def __init__(self, *rows):
        self.conn = Conn(rows)

    @contextmanager
    def connection(self):
        yield self.conn


def test_reads_two_independent_controls():
    value = PostgresRemoteOperationControls(
        Pool({"generation": 3, "intake_enabled": False, "dispatch_enabled": True})
    ).get()
    assert value.generation == 3 and not value.intake_enabled and value.dispatch_enabled


def test_update_uses_generation_compare_and_swap():
    pool = Pool({"generation": 4, "intake_enabled": False, "dispatch_enabled": False})
    value = PostgresRemoteOperationControls(pool).update(3, intake_enabled=False, dispatch_enabled=False)
    assert value.generation == 4
    assert pool.conn.calls[0][1] == (False, False, 3)
    assert "generation=generation+1" in pool.conn.calls[0][0]


def test_stale_generation_returns_none_without_retry():
    assert PostgresRemoteOperationControls(Pool(None)).update(3, intake_enabled=True, dispatch_enabled=False) is None


@pytest.mark.parametrize("generation,intake,dispatch", [(0, True, True), (True, True, True), (1, 1, True)])
def test_invalid_update_is_rejected(generation, intake, dispatch):
    pool = Pool()
    with pytest.raises(ValueError, match="operation_control_input_invalid"):
        PostgresRemoteOperationControls(pool).update(generation, intake_enabled=intake, dispatch_enabled=dispatch)
    assert pool.conn.calls == []
