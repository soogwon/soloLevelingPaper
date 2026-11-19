"""원격 요청형 요약의 판본·범위·전송 동의·근거 결과 계약.

실행이나 자동 생성을 하지 않는다. 서버가 조회한 고정 근거로 준비하고,
별도 동의·예산·lease 검사를 통과한 실행기가 이 계약을 사용한다.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json

from solo_leveling.application.evidence_qa.remote_contract import RemoteSearchBinding
from solo_leveling.domain.paper_summary import SUMMARY_KINDS

NOTICE_VERSION = 'remote-summary-v1'
OPERATION = 'generate_paper_summary'


class RemoteSummaryError(ValueError):
    """원문이나 외부 오류를 포함하지 않는 고정 오류 코드."""


def _require(condition):
    if not condition:
        raise RemoteSummaryError('REMOTE_SUMMARY_INVALID')


def _text(value, limit=512):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(',', ':'))


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RemoteSummaryError('REMOTE_SUMMARY_INVALID')
        result[key] = value
    return result


def _load(value):
    try:
        return json.loads(value, object_pairs_hook=_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (TypeError, ValueError):
        raise RemoteSummaryError('REMOTE_SUMMARY_INVALID') from None


@dataclass(frozen=True, repr=False)
class SummarySource:
    evidence_id: str
    chunk_id: str
    pdf_page: int
    section_id: str | None
    original_text: str
    translated_text: str | None

    def __post_init__(self):
        _require(_text(self.evidence_id) and _text(self.chunk_id)
                 and type(self.pdf_page) is int and self.pdf_page > 0
                 and (self.section_id is None or _text(self.section_id))
                 and _text(self.original_text, 65536)
                 and (self.translated_text is None or _text(self.translated_text, 65536)))


@dataclass(frozen=True, repr=False)
class RemoteSummaryInput:
    encoded: str

    @classmethod
    def create(cls, binding, sources, *, scope, section_id, total_scope_chunks,
               provider, model, prompt_version, language, budget_policy_version):
        _require(type(binding) is RemoteSearchBinding and type(sources) is tuple
                 and all(type(source) is SummarySource for source in sources))
        payload = dict(schema_version=1, operation=OPERATION, notice_version=NOTICE_VERSION,
            binding=asdict(binding), sources=[asdict(source) for source in sources],
            scope=scope, section_id=section_id, total_scope_chunks=total_scope_chunks,
            provider=provider, model=model, prompt_version=prompt_version,
            language=language, budget_policy_version=budget_policy_version)
        try:
            return cls(_encode(payload))
        except (TypeError, ValueError):
            raise RemoteSummaryError('REMOTE_SUMMARY_INVALID') from None

    def __post_init__(self):
        self.unpack()

    def unpack(self):
        _require(isinstance(self.encoded, str) and len(self.encoded.encode('utf-8')) <= 1048576)
        value = _load(self.encoded)
        _require(type(value) is dict and set(value) == {
            'schema_version', 'operation', 'notice_version', 'binding', 'sources',
            'scope', 'section_id', 'total_scope_chunks', 'provider', 'model',
            'prompt_version', 'language', 'budget_policy_version'})
        _require(type(value['schema_version']) is int and value['schema_version'] == 1
                 and value['operation'] == OPERATION and value['notice_version'] == NOTICE_VERSION)
        try:
            binding = RemoteSearchBinding(**value['binding'])
            _require(all(_text(v, 16384) for v in asdict(binding).values()))
            fingerprint = _load(binding.model_fingerprint)
            _require(type(fingerprint) is dict and bool(fingerprint))
            _require(type(value['sources']) is list and 1 <= len(value['sources']) <= 1000)
            sources = tuple(SummarySource(**s) for s in value['sources'])
        except (TypeError, ValueError):
            raise RemoteSummaryError('REMOTE_SUMMARY_INVALID') from None
        _require(value['scope'] in ('paper', 'section')
                 and ((value['scope'] == 'paper' and value['section_id'] is None)
                      or (value['scope'] == 'section' and _text(value['section_id'])
                          and all(s.section_id == value['section_id'] for s in sources))))
        # 선두 일부만 읽고 논문 전체 요약으로 표시하지 않는다. 범위 개수는 서버가 계산한다.
        _require(type(value['total_scope_chunks']) is int
                 and value['total_scope_chunks'] == len(sources)
                 and len({s.chunk_id for s in sources}) == len(sources)
                 and len({s.evidence_id for s in sources}) == len(sources))
        _require(all(_text(value[k]) for k in (
            'provider', 'model', 'prompt_version', 'language', 'budget_policy_version')))
        return binding, sources, value

    @property
    def input_hash(self):
        _, _, value = self.unpack()
        # JSON 필드 순서만 달라진 복원은 동일 입력이다. 원문·근거 순서는 보존한다.
        return NOTICE_VERSION + ':' + hashlib.sha256(_encode(value).encode('ascii')).hexdigest()

    def transfer_notice(self):
        binding, sources, value = self.unpack()
        return dict(notice_version=NOTICE_VERSION, operation=OPERATION,
            input_hash=self.input_hash, context_id=binding.context_id,
            paper_id=binding.paper_id, server_version_id=binding.server_version_id,
            publication_id=binding.publication_id, scope=value['scope'],
            section_id=value['section_id'], source_count=len(sources),
            provider=value['provider'], model=value['model'],
            storage_notice='요약 요청·결과·근거 연결을 현재 계정에 저장합니다.',
            ai_notice='선택 범위의 원문·번역·근거 ID와 요약 지시를 지정한 AI 제공자에 전송합니다. 별도 비용·호출 한도 확인이 필요합니다.',
            storage_consent_required=True, ai_consent_required=True,
            automatic_generation=False)


@dataclass(frozen=True, repr=False)
class SummaryConsent:
    owner_user_id: str
    context_id: str
    input_hash: str
    purpose: str
    notice_version: str
    revoked: bool
    expires_at: datetime


def require_summary_consents(request, consents, *, now):
    """다른 목적의 동의와 철회·만료 동의로 요약 실행을 허용하지 않는다."""
    _require(type(request) is RemoteSummaryInput and type(consents) is tuple
             and len(consents) == 2 and isinstance(now, datetime)
             and now.tzinfo is not None and now.utcoffset() is not None)
    binding, _, _ = request.unpack()
    for consent in consents:
        _require(type(consent) is SummaryConsent
                 and consent.owner_user_id == binding.owner_user_id
                 and consent.context_id == binding.context_id
                 and consent.input_hash == request.input_hash
                 and consent.notice_version == NOTICE_VERSION
                 and type(consent.purpose) is str
                 and consent.purpose in ('summary_storage', 'summary_ai')
                 and consent.revoked is False
                 and isinstance(consent.expires_at, datetime)
                 and consent.expires_at.tzinfo is not None
                 and consent.expires_at.utcoffset() is not None
                 and consent.expires_at > now)
    _require({c.purpose for c in consents} == {'summary_storage', 'summary_ai'})


def validated_summary_payload(request, items, *, generated_at):
    """저장 전에 모든 항목의 근거를 검증한다. 불완전한 결과의 부분 덮어쓰기는 금지한다."""
    _require(type(request) is RemoteSummaryInput and type(items) is list and 1 <= len(items) <= 100
             and isinstance(generated_at, datetime) and generated_at.tzinfo is not None
             and generated_at.utcoffset() is not None)
    binding, sources, settings = request.unpack()
    known = {s.evidence_id for s in sources}
    for item in items:
        _require(type(item) is dict and set(item) == {'kind', 'text', 'evidence_ids'})
        _require(item['kind'] in SUMMARY_KINDS and _text(item['text'], 8192)
                 and type(item['evidence_ids']) is list and bool(item['evidence_ids'])
                 and all(type(e) is str and e in known for e in item['evidence_ids'])
                 and len(set(item['evidence_ids'])) == len(item['evidence_ids']))
    result = dict(schema_version=1, operation=OPERATION, input_hash=request.input_hash,
        binding=asdict(binding), scope=settings['scope'], section_id=settings['section_id'],
        provider=settings['provider'], model=settings['model'], prompt_version=settings['prompt_version'],
        language=settings['language'], generated_at=generated_at.astimezone(timezone.utc).isoformat(),
        verification_level='structural_only', items=items)
    # 호출자가 원본 목록을 바꿔 저장 결과를 변조하지 못하도록 독립 복사한다.
    return _load(_encode(result))
