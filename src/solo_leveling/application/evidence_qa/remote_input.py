"""원격 질문의 versioned 입력 해시. 기존 로컬 v1 키를 변경하지 않는다."""

import hashlib
import json

from .remote_contract import RemoteSearchBinding, RemoteSearchFilters, RemoteRetrievalContractError

CONFIG_KEYS = frozenset({'generation_model', 'generation_prompt_version', 'semantic_model',
                         'semantic_prompt_version', 'budget_policy_version', 'provider_profile_id'})


def _reject_constant(_):
    raise ValueError('nonfinite')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate')
        result[key] = value
    return result


def question_input_hash(binding, question, filters, top_k, processing_config):
    """인증/고정 context와 실제 실행 설정에서 호출한다. payload owner를 신뢰하지 않는다."""
    try:
        if (not isinstance(binding, RemoteSearchBinding) or not isinstance(filters, RemoteSearchFilters)
                or not isinstance(question, str) or not question.strip()
                or type(top_k) is not int or not 1 <= top_k <= 20
                or not isinstance(processing_config, dict) or set(processing_config) != CONFIG_KEYS
                or any(not isinstance(v, str) or not v.strip() for v in processing_config.values())):
            raise ValueError('invalid')
        fp = json.loads(binding.model_fingerprint, object_pairs_hook=_unique,
                        parse_constant=_reject_constant)
        if not isinstance(fp, dict) or not fp:
            raise ValueError('invalid')
        payload = dict(schema_version=1, operation='ask_paper', owner_user_id=binding.owner_user_id,
                       context_id=binding.context_id, paper_id=binding.paper_id,
                       server_version_id=binding.server_version_id, publication_id=binding.publication_id,
                       parse_revision_id=binding.parse_revision_id, translation_revision_id=binding.translation_revision_id,
                       embedding_set_id=binding.embedding_set_id, model_fingerprint=fp,
                       publication_policy=binding.publication_policy, question=question,
                       focus=dict(pdf_pages=sorted(filters.pdf_pages), section_ids=sorted(filters.section_ids),
                                  strict_pages=filters.strict_pages), top_k=top_k, processing_config=processing_config)
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
        return 'remote-question-v1:' + hashlib.sha256(encoded.encode('ascii')).hexdigest()
    except (TypeError, ValueError):
        raise RemoteRetrievalContractError('REMOTE_INPUT_INVALID') from None
