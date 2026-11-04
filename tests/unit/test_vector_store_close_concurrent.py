"""#135: 같은 클라이언트에 대한 겹치는 close_client 호출의 직렬화를 검증한다. 대기는 모두 Event로 제어하고 sleep을 쓰지 않는다."""
import threading

import pytest

from solo_leveling.infrastructure.storage.vector_store import ChromaCloseResult, close_client

WAIT = 5  # 교착 방지용 상한. 정상 경로에서는 쓰이지 않는다.


class Gate:
    """close() 안에서 멈췄다가 놓아 주는 클라이언트. fail=True면 놓은 뒤 예외를 낸다."""

    def __init__(self, fail=False):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.fail = fail
        self.calls = 0

    def close(self):
        self.calls += 1
        self.entered.set()
        assert self.release.wait(WAIT)
        if self.fail:
            raise RuntimeError('SYNTHETIC')


def _run(results, key, client):
    t = threading.Thread(target=lambda: results.__setitem__(key, close_client(client)))
    t.start()
    return t


def _second_is_waiting(t):
    """두 번째 스레드가 첫 호출이 끝나기 전에는 돌아오지 못함을 확인한다(짧게 기다려도 살아 있어야 한다)."""
    t.join(0.2)
    return t.is_alive()


def test_overlapping_close_waits_and_reports_already_closed_on_success():
    client, results = Gate(), {}
    t1 = _run(results, 1, client)
    assert client.entered.wait(WAIT)
    t2 = _run(results, 2, client)
    assert _second_is_waiting(t2)
    assert 2 not in results  # 해제 도중에는 어떤 결과도 확정되지 않는다
    client.release.set()
    t1.join(WAIT)
    t2.join(WAIT)
    assert results == {1: ChromaCloseResult.CLOSED, 2: ChromaCloseResult.ALREADY_CLOSED}
    assert client.calls == 1


def test_overlapping_close_sees_failure_and_never_success():
    client, results = Gate(fail=True), {}
    t1 = _run(results, 1, client)
    assert client.entered.wait(WAIT)
    t2 = _run(results, 2, client)
    assert _second_is_waiting(t2)
    client.release.set()
    t1.join(WAIT)
    t2.join(WAIT)
    assert results == {1: ChromaCloseResult.FAILED, 2: ChromaCloseResult.FAILED}
    assert close_client(client) is ChromaCloseResult.FAILED
    assert client.calls == 1


def test_many_overlapping_calls_close_exactly_once():
    client, results = Gate(), {}
    first = _run(results, 0, client)
    assert client.entered.wait(WAIT)
    others = [_run(results, i, client) for i in range(1, 6)]
    client.release.set()
    for t in [first, *others]:
        t.join(WAIT)
    assert client.calls == 1
    assert sorted(v.value for v in results.values()).count(ChromaCloseResult.CLOSED.value) == 1
    assert all(v in (ChromaCloseResult.CLOSED, ChromaCloseResult.ALREADY_CLOSED) for v in results.values())


def test_other_client_is_not_blocked_by_a_stuck_close():
    stuck, other, results = Gate(), Gate(), {}
    t1 = _run(results, 'stuck', stuck)
    assert stuck.entered.wait(WAIT)
    other.release.set()
    t2 = _run(results, 'other', other)
    t2.join(WAIT)
    assert not t2.is_alive()
    assert results['other'] is ChromaCloseResult.CLOSED
    stuck.release.set()
    t1.join(WAIT)
    assert results['stuck'] is ChromaCloseResult.CLOSED


def test_real_chroma_overlapping_close_with_release_failure(tmp_path, monkeypatch):
    """실제 Chroma: 공유 시스템 해제 직전에 첫 호출을 멈추고 두 번째 호출을 다른 스레드에서 시작한다."""
    chromadb = pytest.importorskip('chromadb')
    from chromadb.api.client import SharedSystemClient

    client = chromadb.PersistentClient(path=str(tmp_path / 'chroma'))
    identifier = client._identifier
    entered, release = threading.Event(), threading.Event()

    def blocked_failing_release(identifier_):
        entered.set()
        assert release.wait(WAIT)
        raise RuntimeError('SYNTHETIC-RELEASE-FAILURE')

    results = {}
    try:
        monkeypatch.setattr(SharedSystemClient, '_release_system', staticmethod(blocked_failing_release))
        t1 = _run(results, 1, client)
        assert entered.wait(WAIT)
        t2 = _run(results, 2, client)
        assert _second_is_waiting(t2)
        release.set()
        t1.join(WAIT)
        t2.join(WAIT)
        monkeypatch.undo()
        assert results == {1: ChromaCloseResult.FAILED, 2: ChromaCloseResult.FAILED}
        assert close_client(client) is ChromaCloseResult.FAILED
        assert identifier in SharedSystemClient._identifier_to_system  # 실제로 해제되지 않았다
    finally:
        monkeypatch.undo()
        release.set()
        _cleanup_identifier(SharedSystemClient, identifier)
    assert identifier not in SharedSystemClient._identifier_to_system
    assert identifier not in getattr(SharedSystemClient, '_identifier_to_refcount', {})


def _cleanup_identifier(shared, identifier):
    """이 테스트가 만든 식별자만 정리한다. 전역 캐시 전체를 비우지 않는다."""
    system = shared._identifier_to_system.pop(identifier, None)
    getattr(shared, '_identifier_to_refcount', {}).pop(identifier, None)
    if system is not None:
        system.stop()


class _Slotted:
    """속성을 붙일 수 없는 객체(__slots__). 종료 표시도 객체별 잠금도 붙지 않는다."""
    __slots__ = ('gate',)

    def __init__(self, gate):
        self.gate = gate

    def close(self):
        self.gate.close()


def test_objects_that_cannot_take_attributes_are_serialized_by_the_shared_lock():
    """문서화한 예외(B 리뷰): 속성을 붙일 수 없는 서로 다른 두 객체는 공통 잠금 때문에 종료가 직렬화된다."""
    first_gate, second_gate = Gate(), Gate()
    results = {}
    t1 = _run(results, 'first', _Slotted(first_gate))
    assert first_gate.entered.wait(WAIT)
    t2 = _run(results, 'second', _Slotted(second_gate))
    assert _second_is_waiting(t2)
    assert not second_gate.entered.is_set()          # 첫 객체의 close()가 끝나기 전에는 진입하지 못한다
    first_gate.release.set()
    t1.join(WAIT)
    assert second_gate.entered.wait(WAIT)
    second_gate.release.set()
    t2.join(WAIT)
    assert results == {'first': ChromaCloseResult.CLOSED, 'second': ChromaCloseResult.CLOSED}
