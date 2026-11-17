"""게시 청크와 같은 고정 PyTorch 설정으로 질문을 로컬 임베딩한다."""
from dataclasses import asdict
import json

from solo_leveling.application.evidence_qa.remote_contract import RemoteRetrievalContractError
from solo_leveling.application.evidence_qa.remote_search import QueryEmbedding, canonical_fingerprint
from solo_leveling.domain.remote_processing import EmbeddingSpec, ProcessedTranslation


class PinnedQueryEmbeddingRuntime:
    def __init__(self, embedder, spec: EmbeddingSpec):
        self._embedder = embedder
        self._spec = spec
        self._fingerprint = canonical_fingerprint(json.dumps(asdict(spec)))

    def embed(self, question, binding):
        if not isinstance(question, str) or not question.strip():
            raise RemoteRetrievalContractError('REMOTE_SEARCH_INPUT_INVALID')
        if canonical_fingerprint(binding.model_fingerprint) != self._fingerprint:
            raise RemoteRetrievalContractError('REMOTE_MODEL_MISMATCH')
        # 처리 adapter의 artifact·실제 런타임·정규화 검사를 질문에도 동일하게 적용한다.
        batch = self._embedder.embed((ProcessedTranslation('query', question, None),), self._spec)
        if batch.spec != self._spec or batch.chunk_ids != ('query',) or len(batch.vectors) != 1:
            raise RemoteRetrievalContractError('REMOTE_EMBEDDING_INVALID')
        return QueryEmbedding(batch.vectors[0], self._fingerprint)
