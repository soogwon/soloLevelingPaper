"""인증된 고정 context의 검색 composition. 도구 등록·모델 선택·게시를 하지 않는다."""

from dataclasses import dataclass
import json
import math

from .remote_contract import RemoteRetrievalContractError, RemoteSearchBinding, RemoteSearchFilters, validate_remote_hits


def _fail(code):
    raise RemoteRetrievalContractError(code)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail('REMOTE_FINGERPRINT_INVALID')
        result[key] = value
    return result


def canonical_fingerprint(raw):
    try:
        value = json.loads(raw, object_pairs_hook=_unique,
                           parse_constant=lambda _: _fail('REMOTE_FINGERPRINT_INVALID'))
        required = ('model_id', 'model_revision', 'tokenizer_id', 'tokenizer_revision', 'pooling',
                    'normalization', 'max_length', 'dimension', 'distance', 'runtime', 'runtime_version',
                    'precision', 'artifact_sha256')
        if not isinstance(value, dict) or any(k not in value for k in required):
            _fail('REMOTE_FINGERPRINT_INVALID')
        if (any(not isinstance(value[k], str) or not value[k].strip() for k in required
                if k not in ('max_length', 'dimension'))
                or any(type(value[k]) is not int or value[k] <= 0 for k in ('max_length', 'dimension'))
                or value['dimension'] > 4096 or value['distance'] != 'cosine'
                or value['normalization'] not in ('l2', 'none')):
            _fail('REMOTE_FINGERPRINT_INVALID')
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError):
        _fail('REMOTE_FINGERPRINT_INVALID')


@dataclass(frozen=True, repr=False)
class QueryEmbedding:
    values: tuple[float, ...]
    model_fingerprint: str


class FingerprintCheckedEmbedder:
    """runtime.embed(question, binding)의 실제 설정 보고를 검사한다. 기본 모델로 대체하지 않는다."""
    def __init__(self, runtime):
        self.runtime = runtime

    def __call__(self, question, binding):
        expected = canonical_fingerprint(binding.model_fingerprint)
        try:
            result = self.runtime.embed(question, binding)
        except Exception:
            _fail('REMOTE_EMBEDDING_UNAVAILABLE')
        if not isinstance(result, QueryEmbedding) or canonical_fingerprint(result.model_fingerprint) != expected:
            _fail('REMOTE_MODEL_MISMATCH')
        settings = json.loads(expected)
        vector = result.values
        if (not isinstance(vector, tuple) or len(vector) != settings['dimension']
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)):
            _fail('REMOTE_EMBEDDING_INVALID')
        norm = math.hypot(*vector)
        if not math.isfinite(norm) or norm == 0 or settings['normalization'] == 'l2' and abs(norm - 1) > 1e-5:
            _fail('REMOTE_EMBEDDING_INVALID')
        return vector


@dataclass(frozen=True)
class RemoteSearchResponse:
    binding: RemoteSearchBinding
    filters: RemoteSearchFilters
    hits: tuple


class RemoteContextSearchService:
    """resolve_context는 SDK 인증 context와 owner 저장소를 통해 매 요청 binding을 해석한다."""
    def __init__(self, resolve_context, retriever):
        self.resolve_context, self.retriever = resolve_context, retriever

    def search(self, question, context_id, *, filters=RemoteSearchFilters(), top_k=5):
        if (not isinstance(question, str) or not question.strip()
                or not isinstance(context_id, str) or not context_id.strip()
                or type(top_k) is not int or not 1 <= top_k <= 20
                or not isinstance(filters, RemoteSearchFilters)):
            _fail('REMOTE_SEARCH_INPUT_INVALID')
        binding = self.resolve_context(context_id)
        if binding is None:
            _fail('REMOTE_CONTEXT_NOT_FOUND')
        if not isinstance(binding, RemoteSearchBinding) or binding.context_id != context_id:
            _fail('REMOTE_SCOPE_MISMATCH')
        canonical_fingerprint(binding.model_fingerprint)
        try:
            hits = self.retriever.search(question, binding, filters, top_k)
        except RemoteRetrievalContractError:
            raise
        except Exception:
            _fail('REMOTE_SEARCH_UNAVAILABLE')
        return RemoteSearchResponse(binding, filters, validate_remote_hits(binding, filters, hits, top_k))
