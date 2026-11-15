"""원격 동일 실행의 읽기 전용 복구. 접수·재생성·provider 의존성은 갖지 않는다."""

from dataclasses import dataclass
import hashlib
import json
from typing import Protocol

from solo_leveling.domain.evidence_qa import AnswerResult, AnswerStatus, ReasonCode, Claim, Citation
from solo_leveling.application.evidence_qa.validators import validate_answer_result


class ReplayError(RuntimeError):
    """본문·토큰·저장소 오류를 포함하지 않는 고정 내부 코드."""


@dataclass(frozen=True, repr=False)
class ExecutionLookup:
    owner_user_id: str
    operation: str
    request_key: str
    input_hash: str
    context_id: str

    def __post_init__(self):
        if any(not isinstance(v, str) or not v.strip() for v in self.__dict__.values()):
            raise ReplayError('REMOTE_LOOKUP_INVALID')


@dataclass(frozen=True, repr=False)
class StoredExecution:
    lookup: ExecutionLookup
    execution_id: str
    state: str
    budget_state: str
    access_allowed: bool = False
    result_available: bool = False
    payload_json: str | None = None
    payload_sha256: str | None = None


@dataclass(frozen=True, repr=False)
class ReplayView:
    execution_id: str
    state: str
    budget_state: str
    payload: dict | None


class ExecutionReader(Protocol):
    def lookup(self, owner_user_id: str, operation: str, request_key: str) -> StoredExecution | None:
        """해시는 유일 키가 아니다. 현재 조회 권한과 원자 저장된 결과를 같은 snapshot에서 읽는다."""
        ...


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate')
        value[key] = item
    return value


def _reject(_):
    raise ValueError('nonfinite')


def _saved_payload(row):
    try:
        if (not isinstance(row.payload_json, str) or not isinstance(row.payload_sha256, str)
                or hashlib.sha256(row.payload_json.encode('utf-8')).hexdigest() != row.payload_sha256):
            raise ValueError('hash')
        payload = json.loads(row.payload_json, object_pairs_hook=_unique, parse_constant=_reject)
        required = {'context_id', 'verification_level', 'status', 'answer_ko', 'claims', 'citations', 'reason_code'}
        if (not isinstance(payload, dict) or set(payload) - required - {'verification_history'}
                or not required <= set(payload) or payload['context_id'] != row.lookup.context_id
                or payload['verification_level'] not in ('structural_only', 'model_checked')
                or not isinstance(payload['claims'], list) or not isinstance(payload['citations'], list)):
            raise ValueError('shape')
        claims = []
        for claim in payload['claims']:
            if set(claim) != {'claim_id', 'text', 'evidence_ids'} or not isinstance(claim['evidence_ids'], list):
                raise ValueError('claim')
            claims.append(Claim(claim['claim_id'], claim['text'], tuple(claim['evidence_ids'])))
        answer = AnswerResult(AnswerStatus(payload['status']), payload['answer_ko'], tuple(claims),
                              tuple(Citation(**c) for c in payload['citations']),
                              ReasonCode(payload['reason_code']) if payload['reason_code'] is not None else None)
        validate_answer_result(answer)
        if 'verification_history' in payload:
            history = payload['verification_history']
            if (not isinstance(history, dict) or set(history) != {'status'}
                    or history['status'] not in ('not_used', 'saved', 'failed')):
                raise ValueError('history')
        return payload
    except Exception:
        raise ReplayError('REMOTE_SAVED_RESULT_INVALID') from None


def read_existing_execution(reader: ExecutionReader, lookup: ExecutionLookup) -> ReplayView | None:
    if not isinstance(lookup, ExecutionLookup):
        raise ReplayError('REMOTE_LOOKUP_INVALID')
    try:
        row = reader.lookup(lookup.owner_user_id, lookup.operation, lookup.request_key)
    except Exception:
        raise ReplayError('REMOTE_LOOKUP_UNAVAILABLE') from None
    if row is None:
        return None
    if not isinstance(row, StoredExecution) or not isinstance(row.lookup, ExecutionLookup):
        raise ReplayError('REMOTE_EXECUTION_INVALID')
    # 타인/접근 폐기와 미존재는 같은 None. 다른 해시의 새 실행은 만들지 않는다.
    actual = row.lookup
    if (row.access_allowed is not True or (actual.owner_user_id, actual.operation, actual.request_key)
            != (lookup.owner_user_id, lookup.operation, lookup.request_key)):
        return None
    if (actual.input_hash, actual.context_id) != (lookup.input_hash, lookup.context_id):
        raise ReplayError('REMOTE_EXECUTION_CONFLICT')
    if (not isinstance(row.execution_id, str) or not row.execution_id.strip()
            or row.state not in ('running', 'completed', 'failed', 'unknown', 'cancel_requested', 'cancelled')
            or row.budget_state not in ('available', 'limited', 'usage_unknown')
            or type(row.result_available) is not bool):
        raise ReplayError('REMOTE_EXECUTION_INVALID')
    if not row.result_available and (row.payload_json is not None or row.payload_sha256 is not None):
        raise ReplayError('REMOTE_EXECUTION_INVALID')
    return ReplayView(row.execution_id, row.state, row.budget_state,
                      _saved_payload(row) if row.result_available else None)
