"""질문 모델이 게시 벡터와 다르면 로딩 전에 거부한다."""
from dataclasses import asdict, replace
import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from solo_leveling.domain.remote_processing import EmbeddingSpec, EmbeddingBatch
from solo_leveling.infrastructure.embeddings.remote_query import PinnedQueryEmbeddingRuntime
from solo_leveling.application.evidence_qa.remote_contract import RemoteRetrievalContractError
from solo_leveling.application.evidence_qa.remote_search import FingerprintCheckedEmbedder


def test_query_runtime_checks_fingerprint_before_model_and_preserves_question():
    spec = EmbeddingSpec('model','rev','model','rev','mean','l2',128,2,'cosine','float32','pytorch','test','a'*64)
    engine = Mock()
    engine.embed.return_value = EmbeddingBatch(spec, ('query',), ((1.,0.),))
    runtime = PinnedQueryEmbeddingRuntime(engine, spec)
    binding = SimpleNamespace(model_fingerprint=json.dumps(asdict(spec)))
    assert FingerprintCheckedEmbedder(runtime)('합성 질문', binding) == (1.,0.)
    assert engine.embed.call_args.args[0][0].text == '합성 질문'
    engine.reset_mock()
    binding.model_fingerprint=json.dumps(asdict(replace(spec, model_revision='other')))
    with pytest.raises(RemoteRetrievalContractError):
        runtime.embed('합성 질문', binding)
    engine.embed.assert_not_called()
