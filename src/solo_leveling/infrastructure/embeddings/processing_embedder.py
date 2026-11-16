"""#517 전용 고정 로컬 PyTorch adapter. 기존 로컬 기본 모델·전역 캐시를 사용하지 않는다."""

from dataclasses import dataclass, field
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import sys
from typing import Callable

from solo_leveling.domain.remote_processing import (
    EmbeddingBatch, EmbeddingSpec, ProcessedTranslation, ProcessingError,
)

_SAFE_CODES = frozenset({
    'PROCESSING_EMBEDDING_CONFIG_INVALID', 'PROCESSING_EMBEDDING_ARTIFACT_MISMATCH',
    'PROCESSING_EMBEDDING_RUNTIME_MISMATCH', 'PROCESSING_EMBEDDING_INVALID',
    'PROCESSING_EMBEDDING_FAILED', 'PROCESSING_CHECK_UNAVAILABLE', 'PROCESSING_NOT_ALLOWED',
})


def _require(ok, code='PROCESSING_EMBEDDING_CONFIG_INVALID'):
    if not ok:
        raise ProcessingError(code)


def _directory_link(path: Path) -> bool:
    if not path.is_dir():
        return False
    if path.is_symlink():
        return True
    is_junction = getattr(path, 'is_junction', None)
    if callable(is_junction) and is_junction():
        return True
    # Python 3.11에는 Path.is_junction이 없다. Windows의 reparse directory는
    # rglob에서 빠진 모델 입력을 loader가 읽을 수 있어 lstat 속성으로도 거부한다.
    return bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)


def processing_artifact_sha256(folder: Path) -> str:
    """#391 artifact_digest와 같은 파일 집합·정렬 JSON 규칙. 모델을 import하지 않는다."""
    entries = []
    for path in sorted(folder.rglob('*')):
        if {'onnx', 'openvino'} & set(path.relative_to(folder).parts):
            continue
        # HF blob를 가리키는 파일 링크는 내용 해시로 묶지만, 디렉터리 링크는 rglob의
        # 미추적 영역을 loader가 읽을 수 있으므로 허용하지 않는다.
        _require(not _directory_link(path),
                 'PROCESSING_EMBEDDING_ARTIFACT_MISMATCH')
        if not path.is_file() or path.suffix not in ('.json', '.txt', '.model', '.safetensors', '.bin'):
            continue
        sha = hashlib.sha256()
        with path.open('rb') as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                sha.update(block)
        entries.append((path.relative_to(folder).as_posix(), sha.hexdigest()))
    _require(bool(entries), 'PROCESSING_EMBEDDING_ARTIFACT_MISMATCH')
    raw = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _load_model(path):
    # 실제 호출 시에만 import한다. 이번 합성 검증에서는 이 경로를 사용하지 않는다.
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(str(path), device='cpu', backend='torch',
                               local_files_only=True, trust_remote_code=False)


def _runtime_version():
    return ';'.join(f'{name}={version(name)}' for name in ('torch', 'sentence-transformers', 'transformers'))


def _loaded_runtime_version():
    """설치 metadata와 별개로 이미 로딩된 모듈 버전을 확인한다. 모델을 import하지 않는다."""
    versions = []
    for name in ('torch', 'sentence-transformers', 'transformers'):
        observed = getattr(sys.modules.get(name.replace('-', '_')), '__version__', None)
        _require(isinstance(observed, str) and bool(observed.strip()),
                 'PROCESSING_EMBEDDING_RUNTIME_MISMATCH')
        versions.append(f'{name}={observed}')
    return ';'.join(versions)


def _validate_modules(folder):
    # SentenceTransformer의 modules.json은 trust_remote_code=False만으로 사용자 정의
    # module/path 로딩이 모두 차단되는 경계가 아니다. 기준선의 두 module만 허용한다.
    def unique(pairs):
        value = {}
        for key, item in pairs:
            _require(key not in value, 'PROCESSING_EMBEDDING_ARTIFACT_MISMATCH')
            value[key] = item
        return value
    try:
        modules = json.loads((folder/'modules.json').read_text(encoding='utf-8'), object_pairs_hook=unique)
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise ProcessingError('PROCESSING_EMBEDDING_ARTIFACT_MISMATCH') from None
    _require(modules == [
        dict(idx=0, name='0', path='', type='sentence_transformers.models.Transformer'),
        dict(idx=1, name='1', path='1_Pooling', type='sentence_transformers.models.Pooling'),
    ], 'PROCESSING_EMBEDDING_ARTIFACT_MISMATCH')
    _require(all(type(m['idx']) is int for m in modules), 'PROCESSING_EMBEDDING_ARTIFACT_MISMATCH')


def _check(check_allowed):
    try:
        allowed = check_allowed()
    except Exception:
        raise ProcessingError('PROCESSING_CHECK_UNAVAILABLE') from None
    _require(allowed is True, 'PROCESSING_NOT_ALLOWED')


@dataclass(frozen=True, repr=False, kw_only=True)
class PyTorchProcessingEmbedder:
    model_dir: Path
    check_allowed: Callable[[], bool]
    batch_size: int = 8
    model_factory: object = field(default=_load_model, repr=False)

    def __post_init__(self):
        _require(isinstance(self.model_dir, Path) and type(self.batch_size) is int
                 and self.batch_size > 0 and callable(self.model_factory) and callable(self.check_allowed))

    def _observe(self, model, spec):
        dimension = model.get_embedding_dimension()
        _require(len(model) == 2 and model.get_backend() == 'torch' and str(model.device) == 'cpu'
                 and type(model.max_seq_length) is int and model.max_seq_length == spec.max_length
                 and type(dimension) is int and dimension == spec.dimension,
                 'PROCESSING_EMBEDDING_RUNTIME_MISMATCH')
        parameters = iter(model.parameters())
        first = next(parameters, None)
        _require(first is not None and all(str(p.dtype) == 'torch.float32' and str(p.device) == 'cpu'
                 for p in (first, *parameters)), 'PROCESSING_EMBEDDING_RUNTIME_MISMATCH')
        pooling = model[1].get_config_dict()
        _require(type(pooling) is dict and all(type(k) is str for k in pooling)
                 and type(pooling.get('word_embedding_dimension')) is int
                 and pooling.get('word_embedding_dimension') == spec.dimension
                 and pooling.get('pooling_mode_mean_tokens') is True
                 and all(v is False for k, v in pooling.items()
                         if k.startswith('pooling_mode_') and k != 'pooling_mode_mean_tokens'),
                 'PROCESSING_EMBEDDING_RUNTIME_MISMATCH')
        actual = json.dumps(pooling, sort_keys=True)
        _require(spec.pooling in ('mean', actual), 'PROCESSING_EMBEDDING_RUNTIME_MISMATCH')
        # 숨은 기본 prompt가 입력을 변경하지 않도록 확인한다.
        _require(not getattr(model, 'default_prompt_name', None), 'PROCESSING_EMBEDDING_RUNTIME_MISMATCH')

    def embed(self, items, spec):
        try:
            folder, factory, batch_size = self.model_dir.resolve(), self.model_factory, self.batch_size
            check_allowed = self.check_allowed
            _require(type(spec) is EmbeddingSpec and folder.is_dir()
                     and len(folder.name) == 40 and all(c in '0123456789abcdef' for c in folder.name)
                     and spec.model_revision == spec.tokenizer_revision == folder.name
                     and spec.model_id == spec.tokenizer_id
                     and type(spec.model_id) is str and bool(spec.model_id.strip())
                     and spec.runtime == 'pytorch' and spec.precision == 'float32'
                     and spec.normalization == 'l2' and spec.distance == 'cosine'
                     and type(spec.max_length) is int and spec.max_length > 0
                     and type(spec.dimension) is int and spec.dimension > 0)
            _require(all((folder / name).is_file() for name in
                     ('config.json', 'modules.json', 'tokenizer.json', '1_Pooling/config.json'))
                     and any(p.is_file() for p in folder.glob('*.safetensors')),
                     'PROCESSING_EMBEDDING_ARTIFACT_MISMATCH')
            _require(type(items) is tuple and bool(items)
                     and all(type(item) is ProcessedTranslation and item.failure_code is None
                             and type(item.chunk_id) is str and bool(item.chunk_id.strip())
                             and type(item.text) is str and bool(item.text.strip()) for item in items))
            ids = tuple(item.chunk_id for item in items)
            _require(len(set(ids)) == len(ids))
            _check(check_allowed)
            _validate_modules(folder)
            _require(processing_artifact_sha256(folder) == spec.artifact_sha256,
                     'PROCESSING_EMBEDDING_ARTIFACT_MISMATCH')
            _require(_runtime_version() == spec.runtime_version, 'PROCESSING_EMBEDDING_RUNTIME_MISMATCH')
            _check(check_allowed)
            model = factory(folder)
            model.max_seq_length = spec.max_length
            _check(check_allowed)
            self._observe(model, spec)
            _require(_loaded_runtime_version() == spec.runtime_version
                     and _runtime_version() == spec.runtime_version, 'PROCESSING_EMBEDDING_RUNTIME_MISMATCH')
            _validate_modules(folder)
            _require(processing_artifact_sha256(folder) == spec.artifact_sha256,
                     'PROCESSING_EMBEDDING_ARTIFACT_MISMATCH')
            _check(check_allowed)
            values = model.encode([item.text for item in items], batch_size=batch_size,
                normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False, prompt='')
            _check(check_allowed)
            _require(getattr(values.dtype, 'name', None) == 'float32', 'PROCESSING_EMBEDDING_INVALID')
            rows = values.tolist()
            _require(type(rows) is list and len(rows) == len(items), 'PROCESSING_EMBEDDING_INVALID')
            for row in rows:
                _require(type(row) is list and len(row) == spec.dimension
                         and all(type(v) in (int, float) and math.isfinite(v) for v in row),
                         'PROCESSING_EMBEDDING_INVALID')
                norm = math.hypot(*row)
                _require(math.isfinite(norm) and norm > 0 and abs(norm - 1) <= 1e-5,
                         'PROCESSING_EMBEDDING_INVALID')
            self._observe(model, spec)
            _require(_runtime_version() == spec.runtime_version
                     and _loaded_runtime_version() == spec.runtime_version, 'PROCESSING_EMBEDDING_RUNTIME_MISMATCH')
            _require(processing_artifact_sha256(folder) == spec.artifact_sha256,
                     'PROCESSING_EMBEDDING_ARTIFACT_MISMATCH')
            _check(check_allowed)
            return EmbeddingBatch(spec, ids, tuple(tuple(row) for row in rows))
        except ProcessingError as error:
            if (type(error) is ProcessingError and len(error.args) == 1
                    and type(error.args[0]) is str and error.args[0] in _SAFE_CODES):
                raise
            raise ProcessingError('PROCESSING_EMBEDDING_FAILED') from None
        except Exception:
            raise ProcessingError('PROCESSING_EMBEDDING_FAILED') from None
