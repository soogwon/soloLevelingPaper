"""로컬 snapshot만 사용하는 #391 전용 PyTorch 캡처. 제품 모델 설정을 바꾸지 않는다."""

import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

from evaluation.embedding.comparison import (
    EvaluationError, compare, digest, load_json, require, validate_capture, validate_dataset,
)


def artifact_digest(folder):
    """가중치·토크나이저·설정 파일 내용에 결합한 산출물 지문. 경로는 출력하지 않는다."""
    files = sorted(p for p in folder.rglob('*') if p.is_file()
                   and p.suffix in ('.json', '.txt', '.model', '.safetensors', '.bin')
                   and not {'onnx', 'openvino'} & set(p.relative_to(folder).parts))
    require(bool(files), 'MODEL_FILES_MISSING')
    entries = []
    for path in files:
        h = hashlib.sha256()
        with path.open('rb') as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                h.update(block)
        entries.append((path.relative_to(folder).as_posix(), h.hexdigest()))
    return digest(entries)


def encode_vectors(model, data, batch_size):
    """원문·질의에 같은 인코딩 설정을 적용하고 잘림 관측값을 ID별로 기록한다."""
    rows = data['chunks'] + data['queries']
    texts = [r['text'] for r in rows]
    vectors = model.encode(texts, batch_size=batch_size, normalize_embeddings=True,
                           convert_to_numpy=True, show_progress_bar=False)
    tokens = model.tokenizer(texts, truncation=False, add_special_tokens=True)['input_ids']
    count = len(data['chunks'])
    return {
        'chunks': {r['id']: v.tolist() for r, v in zip(rows[:count], vectors[:count])},
        'queries': {r['id']: v.tolist() for r, v in zip(rows[count:], vectors[count:])},
        'token_observations': {r['id']: {'tokens_before_truncation': len(t),
                                       'truncated': len(t) > model.max_seq_length}
                               for r, t in zip(rows, tokens)},
    }


def worker(dataset, model_dir, output, started_ns, *, max_length, batch_size, threads, warm_repeats):
    # 부모는 중량 라이브러리 import 전 시각을 전달한다. 모델·질문은 로컬 파일만 사용한다.
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(threads)
    torch.set_num_interop_threads(threads)
    data = validate_dataset(load_json(dataset))
    model = SentenceTransformer(str(model_dir), device='cpu', local_files_only=True)
    model.max_seq_length = max_length
    vectors = encode_vectors(model, data, batch_size)
    cold_ms = (time.perf_counter_ns() - started_ns) / 1e6
    # cold의 첫 전체 인코딩이 준비 실행이다. warm에는 모델 로딩을 포함하지 않는다.
    warm_ms = []
    for _ in range(warm_repeats):
        started = time.perf_counter()
        encode_vectors(model, data, batch_size)
        warm_ms.append((time.perf_counter() - started) * 1000)
    settings = dict(model_id='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
                    model_revision=model_dir.name,
                    tokenizer_id='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
                    tokenizer_revision=model_dir.name,
                    pooling=json.dumps(model[1].get_config_dict(), sort_keys=True), normalization='l2',
                    max_length=max_length, dimension=len(next(iter(vectors['chunks'].values()))),
                    distance='cosine', runtime='pytorch',
                    runtime_version=';'.join(f'{p}={version(p)}' for p in
                                             ('torch', 'sentence-transformers', 'transformers')),
                    precision=str(next(model.parameters()).dtype), artifact_sha256='0' * 64)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(dict(settings=settings, cold_ms=cold_ms, warm_ms=warm_ms, **vectors), handle, allow_nan=False)


def sample_worker(command, env, *, timeout, popen=subprocess.Popen, rss_reader=None):
    """단독 worker 및 자식 RSS 합을 10ms 간격으로 관측한다. 원본 출력은 폐기한다."""
    import psutil

    started = time.monotonic()
    with popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as proc:
        peak = 0
        try:
            while proc.poll() is None:
                if time.monotonic() - started > timeout:
                    raise EvaluationError('CAPTURE_TIMEOUT')
                if rss_reader:
                    rss = rss_reader(proc.pid)
                else:
                    rss = 0
                    try:
                        process = psutil.Process(proc.pid)
                        for child in [process] + process.children(recursive=True):
                            try:
                                rss += child.memory_info().rss
                            except psutil.NoSuchProcess:
                                pass
                    except psutil.NoSuchProcess:
                        pass
                peak = max(peak, rss)
                time.sleep(0.01)
            require(proc.returncode == 0, 'CAPTURE_WORKER_FAILED')
            require(peak > 0, 'RSS_NOT_OBSERVED')
            return peak
        finally:
            if proc.poll() is None:
                # 이 명령이 만든 실행만 종료한다. 다른 모델·MCP 프로세스에는 손대지 않는다.
                process = psutil.Process(proc.pid)
                for child in process.children(recursive=True):
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                proc.kill()
                proc.wait(timeout=10)


def capture(dataset, model_dir, output_dir, *, repeats=3, warm_repeats=3, max_length=128,
            batch_size=8, threads=1, timeout=240):
    require(all(type(v) is int and v > 0 for v in
                (repeats, warm_repeats, max_length, batch_size, threads, timeout)), 'CAPTURE_LIMIT_INVALID')
    data = validate_dataset(load_json(dataset))
    model_dir = Path(model_dir).absolute()
    # 일반 모델 이름이나 변경 가능한 branch를 받아 다운로드하지 않는다.
    require(model_dir.is_dir() and model_dir.parent.name == 'snapshots'
            and len(model_dir.name) == 40 and all(c in '0123456789abcdef' for c in model_dir.name)
            and model_dir.parent.parent.name ==
            'models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2', 'MODEL_SNAPSHOT_INVALID')
    fingerprint = artifact_digest(model_dir)
    # 기존 결과를 덮어쓰지 않는다. 중단 시 부분 결과는 이 전용 디렉터리에만 남는다.
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
               TOKENIZERS_PARALLELISM='false', OMP_NUM_THREADS=str(threads), MKL_NUM_THREADS=str(threads),
               PYTHONIOENCODING='utf-8')
    script = Path(__file__).resolve().parents[2] / 'scripts/capture_pytorch_embeddings.py'
    captures = []
    for index in range(repeats):
        result_path = output_dir / f'worker-{index}.json'
        started_ns = time.perf_counter_ns()
        command = [sys.executable, str(script), '--worker', '--dataset', str(Path(dataset).absolute()),
                   '--model-dir', str(model_dir), '--output', str(result_path.absolute()),
                   '--started-ns', str(started_ns), '--warm-repeats', str(warm_repeats),
                   '--max-length', str(max_length), '--batch-size', str(batch_size), '--threads', str(threads)]
        peak = sample_worker(command, env, timeout=timeout)
        row = load_json(result_path)
        row['settings']['artifact_sha256'] = fingerprint
        result = dict(schema_version=1, dataset_sha256=digest(data), synthetic=False,
                      settings=row['settings'], embedding_fingerprint=digest(row['settings']),
                      chunks=row['chunks'], queries=row['queries'], token_observations=row['token_observations'],
                      measurements=dict(cold_ms=[row['cold_ms']], warm_ms=row['warm_ms'], peak_rss_bytes=peak,
                                        environment=f'{platform.system()} {platform.release()};Python {platform.python_version()};'
                                                    f'{platform.machine()};{platform.processor()}',
                                        procedure='pytorch-local-snapshot-v1;imports+load+encode+token-count;cold-and-warm-rss',
                                        rss_method=f'psutil {version("psutil")};10ms sampled sum RSS process tree',
                                        batch_policy=f'chunks-then-queries;batch={batch_size}',
                                        threads=str(threads), device='cpu'))
        validate_capture(result, data)
        captures.append(result)
    require(artifact_digest(model_dir) == fingerprint, 'MODEL_CHANGED_DURING_CAPTURE')
    require(all(c['settings'] == captures[0]['settings'] for c in captures), 'SETTINGS_CHANGED_DURING_CAPTURE')
    baseline = captures[0]
    baseline['measurements']['cold_ms'] = [c['measurements']['cold_ms'][0] for c in captures]
    baseline['measurements']['warm_ms'] = [t for c in captures for t in c['measurements']['warm_ms']]
    baseline['measurements']['peak_rss_bytes'] = max(c['measurements']['peak_rss_bytes'] for c in captures)
    report = compare(data, baseline, baseline)
    report['repeat_vector_metrics'] = [compare(data, baseline, c)['vector_metrics'] for c in captures]
    for name, value in [('pytorch.json', baseline), ('baseline-report.json', report)]:
        with (output_dir / name).open('x', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False, indent=2)
    return report
