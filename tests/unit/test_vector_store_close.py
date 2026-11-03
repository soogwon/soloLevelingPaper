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


def test_failed_close_stays_failed_on_retry_and_does_not_call_close_again():
    class Flaky:
        calls = 0

        def close(self):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise RuntimeError('once')

    client = Flaky()
    assert close_client(client) is ChromaCloseResult.FAILED
    assert close_client(client) is ChromaCloseResult.FAILED  # 실제 해제를 확인할 수 없으니 성공으로 올리지 않는다
    assert close_client(client) is ChromaCloseResult.FAILED
    assert Flaky.calls == 1


def test_failure_is_remembered_per_client_not_globally():
    class Boom:
        def close(self):
            raise RuntimeError('x')

    failing, healthy = Boom(), Spy()
    assert close_client(failing) is ChromaCloseResult.FAILED
    assert close_client(healthy) is ChromaCloseResult.CLOSED
    assert close_client(failing) is ChromaCloseResult.FAILED


def test_failure_marker_is_not_set_on_success_or_unsupported():
    client = Spy()
    close_client(client)
    assert not hasattr(client, '_solo_leveling_close_failed')
    plain = object()
    assert close_client(plain) is ChromaCloseResult.UNSUPPORTED
    assert close_client(plain) is ChromaCloseResult.UNSUPPORTED


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


def test_real_chroma_close_failure_is_not_promoted_to_success_on_retry(tmp_path, monkeypatch):
    """#120: 해제가 실패한 뒤 재호출이 CLOSED로 보이던 문제. Chroma는 닫힘 표시를 먼저 하므로 재호출은 아무것도 하지 않는다."""
    chromadb = pytest.importorskip('chromadb')
    from chromadb.api.client import SharedSystemClient

    client = chromadb.PersistentClient(path=str(tmp_path / 'chroma'))
    identifier = client._identifier

    def failing_release(identifier_):
        raise RuntimeError('SYNTHETIC-RELEASE-FAILURE')

    try:
        monkeypatch.setattr(SharedSystemClient, '_release_system', staticmethod(failing_release))
        assert close_client(client) is ChromaCloseResult.FAILED
        monkeypatch.undo()  # 주입을 해제한 뒤에도
        assert close_client(client) is ChromaCloseResult.FAILED  # 실제 시스템이 남아 있으므로 성공으로 보이면 안 된다
        assert identifier in SharedSystemClient._identifier_to_system
        assert close_client(client) is ChromaCloseResult.FAILED
    finally:  # 테스트가 남긴 시스템만 정리한다(동작 코드는 이렇게 강제로 닫지 않는다)
        monkeypatch.undo()
        system = SharedSystemClient._identifier_to_system.pop(identifier, None)
        if system is not None:
            system.stop()
