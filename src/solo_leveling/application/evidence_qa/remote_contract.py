"""#390 원격 어댑터 연결 전 B의 내부 검색 경계. 기존 로컬 포트는 변경하지 않는다."""

from dataclasses import dataclass, fields
import math
from typing import Protocol


class RemoteRetrievalContractError(ValueError):
    """본문·식별자를 포함하지 않는 고정 오류 코드만 전달한다."""


def _require(value, code):
    if not value:
        raise RemoteRetrievalContractError(code)


@dataclass(frozen=True)
class VerifiedRemoteIdentity:
    """신뢰하는 인증 adapter가 생성한다. 이 객체의 생성만으로 인증이 증명되지는 않는다."""
    issuer: str
    subject: str

    def __post_init__(self):
        _require(all(isinstance(v, str) and v.strip() for v in (self.issuer, self.subject)),
                 'REMOTE_IDENTITY_INVALID')


class RemoteOwnerResolver(Protocol):
    def resolve_owner(self, identity: VerifiedRemoteIdentity) -> str | None:
        """검증된 identity를 기존 서버 owner로 매핑. subject를 분해하거나 계정을 자동 생성하지 않는다."""
        ...


@dataclass(frozen=True)
class RemoteSearchBinding:
    """인증 owner의 저장 context에서 해석한다. 클라이언트가 만든 값은 신뢰하지 않는다."""
    owner_user_id: str
    context_id: str
    paper_id: str
    server_version_id: str
    publication_id: str
    parse_revision_id: str
    translation_revision_id: str
    embedding_set_id: str
    model_fingerprint: str
    publication_policy: str = 'complete'

    def __post_init__(self):
        _require(all(isinstance(getattr(self, f.name), str) and getattr(self, f.name).strip()
                     for f in fields(self)), 'REMOTE_BINDING_INVALID')
        _require(self.publication_policy in ('complete', 'partial'), 'REMOTE_BINDING_INVALID')


@dataclass(frozen=True)
class RemoteSearchFilters:
    pdf_pages: tuple[int, ...] = ()
    section_ids: tuple[str, ...] = ()
    strict_pages: bool = False

    def __post_init__(self):
        _require(isinstance(self.pdf_pages, tuple)
                 and all(type(p) is int and p > 0 for p in self.pdf_pages), 'REMOTE_FILTER_INVALID')
        _require(isinstance(self.section_ids, tuple)
                 and all(isinstance(s, str) and s.strip() for s in self.section_ids), 'REMOTE_FILTER_INVALID')
        _require(len(self.pdf_pages) == len(set(self.pdf_pages))
                 and len(self.section_ids) == len(set(self.section_ids))
                 and type(self.strict_pages) is bool, 'REMOTE_FILTER_INVALID')


@dataclass(frozen=True)
class RemoteSearchHit:
    binding: RemoteSearchBinding
    chunk_id: str
    pdf_page: int
    section_id: str | None
    cosine_distance: float


@dataclass(frozen=True)
class RemoteRankedHit:
    hit: RemoteSearchHit
    score: float
    rank: int


def cosine_distance_score(distance):
    """높을수록 관련된다. 로컬 vector-only와 같은 단조 변환이며 RRF와 직접 비교하지 않는다."""
    _require(type(distance) in (int, float) and 0 <= distance <= 2 and math.isfinite(distance),
             'REMOTE_DISTANCE_INVALID')
    return 1.0 / (1.0 + distance)


class RemoteContextResolver(Protocol):
    def resolve(self, owner_user_id: str, context_id: str) -> RemoteSearchBinding | None:
        """다른 사용자 context와 없음은 같은 None. context의 과거 publication을 유지한다."""
        ...


class RemoteScopedSearch(Protocol):
    def search(self, question: str, binding: RemoteSearchBinding, filters: RemoteSearchFilters,
               top_k: int) -> tuple[RemoteSearchHit, ...]:
        """owner·revision·manifest·필터로 후보를 먼저 제한한 뒤 top_k를 적용한다."""
        ...


def resolve_remote_binding(identity, context_id, owners: RemoteOwnerResolver, contexts: RemoteContextResolver):
    """인증→명시적 owner 매핑→고정 context 조회. 매핑 미확정/실패 시 대체 조회하지 않는다."""
    _require(isinstance(identity, VerifiedRemoteIdentity), 'REMOTE_IDENTITY_INVALID')
    _require(isinstance(context_id, str) and context_id.strip(), 'REMOTE_CONTEXT_INVALID')
    try:
        owner = owners.resolve_owner(identity)
    except Exception:
        raise RemoteRetrievalContractError('REMOTE_OWNER_MAPPING_UNAVAILABLE') from None
    if owner is None:
        return None
    _require(isinstance(owner, str) and owner.strip(), 'REMOTE_OWNER_MAPPING_INVALID')
    try:
        binding = contexts.resolve(owner, context_id)
    except Exception:
        raise RemoteRetrievalContractError('REMOTE_CONTEXT_LOOKUP_UNAVAILABLE') from None
    if binding is None:
        return None
    _require(isinstance(binding, RemoteSearchBinding) and binding.owner_user_id == owner
             and binding.context_id == context_id, 'REMOTE_SCOPE_MISMATCH')
    return binding


def validate_remote_hits(binding, filters, hits, top_k):
    """검색 후 혼입을 검출한다. 이 검사만으로 DB 사전 필터나 게시 상태를 증명하지 않는다."""
    _require(isinstance(binding, RemoteSearchBinding) and isinstance(filters, RemoteSearchFilters),
             'REMOTE_BINDING_INVALID')
    _require(type(top_k) is int and top_k > 0, 'REMOTE_TOP_K_INVALID')
    _require(isinstance(hits, tuple) and len(hits) <= top_k, 'REMOTE_RESULT_INVALID')
    seen, results = set(), []
    for hit in hits:
        _require(isinstance(hit, RemoteSearchHit), 'REMOTE_RESULT_INVALID')
        _require(isinstance(hit.binding, RemoteSearchBinding) and hit.binding == binding, 'REMOTE_SCOPE_MISMATCH')
        _require(isinstance(hit.chunk_id, str) and hit.chunk_id.strip() and hit.chunk_id not in seen,
                 'REMOTE_CHUNK_INVALID')
        _require(type(hit.pdf_page) is int and hit.pdf_page > 0
                 and (hit.section_id is None or isinstance(hit.section_id, str) and hit.section_id.strip()),
                 'REMOTE_CHUNK_INVALID')
        # 일반 검색은 strict_pages=false여도 범위 밖 후보를 반환하지 않는다.
        _require((not filters.pdf_pages or hit.pdf_page in filters.pdf_pages)
                 and (not filters.section_ids or hit.section_id in filters.section_ids), 'REMOTE_FILTER_MISMATCH')
        seen.add(hit.chunk_id)
        results.append(RemoteRankedHit(hit, cosine_distance_score(hit.cosine_distance), len(results) + 1))
    _require(results == sorted(results, key=lambda r: (-r.score, r.hit.chunk_id)), 'REMOTE_ORDER_INVALID')
    return tuple(results)
