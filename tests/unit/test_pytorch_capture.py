"""모델·네트워크 없이 실제 캡처의 실행 경계와 집계를 검증한다."""

import json
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from evaluation.embedding import pytorch_capture as runner
from evaluation.embedding.comparison import EvaluationError, load_json, synthetic_capture

DATA = Path(__file__).resolve().parents[2] / 'evaluation/embedding/cases.json'


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('실제 네트워크 금지')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)


def test_artifact_digest_uses_contents_and_ignores_other_runtime(tmp_path):
    (tmp_path / 'model.safetensors').write_bytes(b'one')
    before = runner.artifact_digest(tmp_path)
    (tmp_path / 'onnx').mkdir()
    (tmp_path / 'onnx/model.bin').write_bytes(b'other-runtime')
    assert runner.artifact_digest(tmp_path) == before
    (tmp_path / 'model.safetensors').write_bytes(b'two')
    assert runner.artifact_digest(tmp_path) != before


@pytest.mark.parametrize('change', ['zero', 'snapshot', 'existing'])
def test_invalid_limits_snapshot_and_existing_output_fail_before_worker(tmp_path, monkeypatch, change):
    calls = []
    monkeypatch.setattr(runner, 'sample_worker', lambda *a, **k: calls.append(1))
    folder = tmp_path / 'models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2/snapshots' / ('a' * 40)
    folder.mkdir(parents=True)
    (folder / 'config.json').write_text('{}')
    output = tmp_path / 'output'
    options = {'repeats': 0} if change == 'zero' else {}
    if change == 'snapshot': folder = tmp_path
    if change == 'existing': output.mkdir()
    with pytest.raises((EvaluationError, FileExistsError)):
        runner.capture(DATA, folder, output, **options)
    assert calls == []


def test_capture_aggregates_fresh_workers_without_text_in_report(tmp_path, monkeypatch):
    folder = tmp_path / 'models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2/snapshots' / ('a' * 40)
    folder.mkdir(parents=True)
    (folder / 'config.json').write_text('{}')
    calls = []
    def fake(command, env, **kwargs):
        assert env['HF_HUB_OFFLINE'] == '1' and env['TRANSFORMERS_OFFLINE'] == '1'
        assert '--worker' in command
        row = synthetic_capture(load_json(DATA))
        row['settings']['runtime'] = 'pytorch'
        row.update(cold_ms=100 + len(calls), warm_ms=[10, 11, 12], token_observations={})
        Path(command[command.index('--output') + 1]).write_text(json.dumps(row), encoding='utf-8')
        calls.append(command)
        return 1024 + len(calls)
    monkeypatch.setattr(runner, 'sample_worker', fake)
    monkeypatch.setattr(runner, 'version', lambda _: 'test-version')
    output = tmp_path / 'capture'
    report = runner.capture(DATA, folder, output)
    result = load_json(output / 'pytorch.json')
    assert result['measurements']['cold_ms'] == [100, 101, 102]
    assert result['measurements']['warm_ms'] == [10, 11, 12] * 3
    assert result['measurements']['peak_rss_bytes'] == 1027
    assert len(report['repeat_vector_metrics']) == 3
    assert report['decision'] == 'pending_thresholds'
    assert 'Lantern relay needs' not in json.dumps(report)


def test_sampler_records_tree_rss_and_rejects_worker_failure(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, 'psutil', SimpleNamespace())
    class Process:
        pid = 1
        returncode = 0
        count = 0
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def poll(self):
            self.count += 1
            return None if self.count < 3 else self.returncode
    monkeypatch.setattr(runner.time, 'sleep', lambda _: None)
    assert runner.sample_worker([], {}, timeout=10, popen=lambda *a, **k: Process(),
                                rss_reader=lambda _: 2048) == 2048
    Process.returncode = 1
    with pytest.raises(EvaluationError, match='CAPTURE_WORKER_FAILED'):
        runner.sample_worker([], {}, timeout=10, popen=lambda *a, **k: Process(), rss_reader=lambda _: 2048)


def test_encode_uses_shared_settings_and_records_truncation():
    data = load_json(DATA)
    class Vector(list):
        def tolist(self): return list(self)
    class Model:
        max_seq_length = 128
        def encode(self, texts, **settings):
            assert settings['normalize_embeddings'] is True
            assert settings['batch_size'] == 8
            return [Vector([1.0, 0.0]) for _ in texts]
        def tokenizer(self, texts, **settings):
            assert settings == dict(truncation=False, add_special_tokens=True)
            return {'input_ids': [[0] * 200 if len(t) > 1000 else [0] * 10 for t in texts]}
    result = runner.encode_vectors(Model(), data, 8)
    assert set(result['chunks']) == {r['id'] for r in data['chunks']}
    assert set(result['queries']) == {r['id'] for r in data['queries']}
    assert result['token_observations']['long']['truncated'] is True


def test_timeout_kills_only_this_worker_tree(monkeypatch):
    import sys
    killed = []
    child = SimpleNamespace(kill=lambda: killed.append('child'))
    process = SimpleNamespace(children=lambda **_: [child])
    monkeypatch.setitem(sys.modules, 'psutil', SimpleNamespace(Process=lambda _: process,
                                                             NoSuchProcess=ProcessLookupError, Error=ProcessLookupError))
    ticks = iter([0, 2])
    monkeypatch.setattr(runner.time, 'monotonic', lambda: next(ticks))
    class Process:
        pid = 99
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def poll(self): return None
        def kill(self): killed.append('worker')
        def wait(self, **kwargs): killed.append('wait')
    with pytest.raises(EvaluationError, match='CAPTURE_TIMEOUT'):
        runner.sample_worker([], {}, timeout=1, popen=lambda *a, **k: Process())
    assert killed == ['child', 'worker', 'wait']


@pytest.mark.parametrize('failure', [PermissionError, ProcessLookupError])
def test_timeout_reaps_worker_even_when_child_lookup_fails(monkeypatch, failure):
    import sys
    reaped = []
    def lookup(_):
        raise failure('민감한 프로세스 정보')
    monkeypatch.setitem(sys.modules, 'psutil', SimpleNamespace(Process=lookup, Error=OSError))
    ticks = iter([0, 2])
    monkeypatch.setattr(runner.time, 'monotonic', lambda: next(ticks))
    class Process:
        pid = 99
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def poll(self): return None
        def kill(self): reaped.append('kill')
        def wait(self, **kwargs): reaped.append('wait')
    with pytest.raises(EvaluationError, match='CAPTURE_TIMEOUT'):
        runner.sample_worker([], {}, timeout=1, popen=lambda *a, **k: Process())
    assert reaped == ['kill', 'wait']
