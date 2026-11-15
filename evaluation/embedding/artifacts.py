"""#391 캡처 공통 절차와 로컬 산출물 지문. 모델을 실행하거나 다운로드하지 않는다."""

import hashlib
from pathlib import Path

from evaluation.embedding.comparison import digest, require

PROCEDURE = 'local-capture-v2;imports+load+encode+token-count;cold-and-warm-rss'


def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def input_artifact_digest(folder):
    files = sorted(p for p in folder.rglob('*') if p.is_file()
                   and p.suffix in ('.json', '.txt', '.model')
                   and not {'onnx', 'openvino', '.git'} & set(p.relative_to(folder).parts))
    require(bool(files), 'INPUT_ARTIFACTS_MISSING')
    return digest([(p.relative_to(folder).as_posix(), file_hash(p)) for p in files])


def _load_graph(path):
    import onnx
    return onnx.load_model(str(path), load_external_data=False)


def _messages(message):
    yield message
    for field, value in message.ListFields():
        if field.message_type is not None:
            for child in value if field.is_repeated else (value,):
                yield from _messages(child)


def onnx_manifest(folder, file_name):
    """graph와 external tensor 파일·입력 설정을 결합한다. export는 수행하지 않는다."""
    folder = Path(folder).absolute()
    target = (folder / file_name).resolve()
    require(target.is_file() and target.is_relative_to(folder.resolve()), 'ONNX_ARTIFACT_INVALID')
    graph = _load_graph(target)
    require(bool(graph.producer_name) and bool(graph.producer_version) and bool(graph.opset_import),
            'ONNX_EXPORT_METADATA_MISSING')
    files = {target}
    types, ops = set(), set()
    for message in _messages(graph):
        if hasattr(message, 'op_type'):
            ops.add(message.op_type)
        if hasattr(message, 'data_type'):
            types.add(int(message.data_type))
        for entry in getattr(message, 'external_data', ()):
            if entry.key == 'location':
                location = Path(entry.value)
                require(bool(entry.value) and not location.is_absolute() and '..' not in location.parts,
                        'ONNX_EXTERNAL_DATA_INVALID')
                path = (target.parent / location).resolve()
                require(path.is_file() and path.is_relative_to(folder.resolve()), 'ONNX_EXTERNAL_DATA_INVALID')
                files.add(path)
    return dict(schema_version=1, producer=graph.producer_name, producer_version=graph.producer_version,
                opsets=sorted((x.domain or 'ai.onnx', int(x.version)) for x in graph.opset_import),
                files=sorted((p.relative_to(folder.resolve()).as_posix(), file_hash(p)) for p in files),
                input_artifacts_sha256=input_artifact_digest(folder), initializer_types=sorted(types),
                quantization_ops=sorted(op for op in ops if 'Quantiz' in op or op.startswith('QLinear')))


def ort_session_options(ort, threads):
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = threads
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return options


def observe_ort_session(session, threads):
    options = session.get_session_options()
    require(session.get_providers() == ['CPUExecutionProvider']
            and options.intra_op_num_threads == threads and options.inter_op_num_threads == threads
            and getattr(options.execution_mode, 'name', '') == 'ORT_SEQUENTIAL', 'ORT_SESSION_MISMATCH')
    return dict(provider='CPUExecutionProvider', intra_op_num_threads=options.intra_op_num_threads,
                inter_op_num_threads=options.inter_op_num_threads, execution_mode='ORT_SEQUENTIAL')
