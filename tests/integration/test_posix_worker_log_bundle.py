"""POSIX worker는 검증한 핸들만 사용하고 교체된 경로를 다시 열지 않는다."""

import multiprocessing
import os

import pytest

from solo_leveling.infrastructure.embeddings.worker import diagnostic_worker
from solo_leveling.infrastructure.log_retention.contracts import BundleId
from solo_leveling.infrastructure.log_retention.policy import log_name
from solo_leveling.infrastructure.log_retention.posix import PosixBundleStore


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX 파일 설명자 전달 검증')


def _writer(connection):
    from solo_leveling.infrastructure.embeddings import worker
    print('SYNTHETIC_RAW', flush=True)
    os.write(2, b'SYNTHETIC_NATIVE\n')
    if worker._stack_output is not None:
        worker._stack_output.write('SYNTHETIC_STACK\n')
        worker._stack_output.flush()
    connection.send({'ok': True})


@pytest.mark.parametrize('replace', ['safe', 'raw', 'stack', 'root'])
def test_replaced_path_never_receives_worker_output(tmp_path, replace):
    from multiprocessing.reduction import send_handle

    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe()
    receiver, sender = context.Pipe()
    worker_id = 'a' * 32
    raw = replace != 'safe'
    process = context.Process(target=diagnostic_worker,
        args=(child, _writer, tmp_path / 'logs', worker_id, 2, raw, None, receiver))
    process.start()
    child.close()
    receiver.close()

    store = PosixBundleStore()
    lease = store.try_acquire_writer(tmp_path / 'logs', BundleId('worker', worker_id)).lease
    assert lease is not None
    outputs = []
    kinds = ('raw', 'stack') if raw else ('safe',)
    for kind in kinds:
        _, output = lease.open_log(kind)
        outputs.append((kind, output))

    external = tmp_path / 'external'
    external.mkdir()
    outside = external / 'outside.log'
    outside.write_bytes(b'UNCHANGED')
    if replace == 'root':
        original = tmp_path / 'original-logs'
        (tmp_path / 'logs').rename(original)
        (tmp_path / 'logs').symlink_to(external, target_is_directory=True)
    else:
        path = lease.root / log_name(lease.bundle_id, replace)
        path.unlink()
        path.symlink_to(outside)

    try:
        sender.send((True, kinds))
        for _, output in outputs:
            send_handle(sender, output.fileno(), process.pid)
            output.close()
        assert parent.recv() == {'ok': True}
        process.join(10)
        assert process.exitcode == 0
        assert outside.read_bytes() == b'UNCHANGED'
        assert not list(tmp_path.rglob('*.closed.json'))
    finally:
        sender.close()
        parent.close()
        if process.is_alive():
            process.kill()
            process.join(5)
        process.close()
        lease.release()
