"""close_client의 결과 코드·중복 종료·실패 격리를 검증한다."""
import pytest

from solo_leveling.infrastructure.storage.vector_store import ChromaCloseResult, close_client


class Spy:
    def __init__(self):
        self.closed = 0
        self.reset_called = False

    def close(self):
        self.closed += 1

    def reset(self):
        self.reset_called = True


def test_close_reports_closed_then_already_closed_without_calling_close_twice():
    client = Spy()
    assert close_client(client) is ChromaCloseResult.CLOSED
    assert close_client(client) is ChromaCloseResult.ALREADY_CLOSED
    assert client.closed == 1


def test_close_never_uses_reset():
    client = Spy()
    close_client(client)
    assert client.reset_called is False


def test_close_failure_is_a_code_not_an_exception_and_not_success():
    class Failing:
        def close(self):
            raise RuntimeError('SYNTHETIC-SECRET /private/path')

    result = close_client(Failing())
    assert result is ChromaCloseResult.FAILED
    assert 'SECRET' not in result.value and '/private' not in result.value


def test_client_without_close_is_unsupported_not_success():
    assert close_client(object()) is ChromaCloseResult.UNSUPPORTED


def test_none_is_already_closed():
    assert close_client(None) is ChromaCloseResult.ALREADY_CLOSED


def test_failed_close_can_be_retried():
    class Flaky:
        calls = 0

        def close(self):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise RuntimeError('once')

    client = Flaky()
    assert close_client(client) is ChromaCloseResult.FAILED
    assert close_client(client) is ChromaCloseResult.CLOSED
    assert close_client(client) is ChromaCloseResult.ALREADY_CLOSED


def test_object_that_rejects_marker_still_closes():
    class Slotted:
        __slots__ = ('count',)

        def __init__(self):
            self.count = 0

        def close(self):
            self.count += 1

    client = Slotted()
    assert close_client(client) is ChromaCloseResult.CLOSED
    assert client.count == 1


def test_real_chroma_client_closes_twice_safely(tmp_path):
    chromadb = pytest.importorskip('chromadb')
    client = chromadb.PersistentClient(path=str(tmp_path / 'chroma'))
    assert close_client(client) is ChromaCloseResult.CLOSED
    assert close_client(client) is ChromaCloseResult.ALREADY_CLOSED
