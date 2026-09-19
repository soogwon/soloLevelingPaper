# 23. Evidence QA 설계

작성일: 2026-09-18
상태: B 담당 1차 구현 계약

## 1. 목적

이 문서는 B 담당 범위인 범위 검색, 한국어 답변 생성, claim·근거 연결, 검증, 요약과 가이드의 구현 경계를 정의한다.

첫 구현 목표는 샘플 청크를 대상으로 다음 흐름을 검증하는 것이다.

```text
질문
→ 검색 범위 확정
→ 관련 청크 검색
→ 근거 후보 구성
→ 한국어 claim 초안 생성
→ claim과 근거 연결
→ 구조 검증
→ 검증된 claim으로 answer_ko 조립
```

실제 Chroma 연결, 실제 생성 API, MCP 도구 노출, 번역 저장은 각 계약을 검증한 뒤 후속 작업으로 연결한다.

## 2. 구현 범위

### 포함

- 검색 범위를 나타내는 계약
- 청크별 검색 결과와 검색 실행 전체 결과의 분리
- claim, citation, 답변 결과 계약
- 검색기와 생성기의 애플리케이션 포트
- JSON 호환 직렬화 규칙
- claim·citation 구조 검증 규칙
- 샘플 청크와 가짜 생성기를 사용한 단위 테스트

### 첫 구현에서 제외

- 기존 PDF ingestion 변경
- 실제 Chroma 저장소 복구 또는 변경
- 실제 번역 파이프라인과 번역 리비전 저장
- 실제 외부 생성 API 호출
- MCP 서버 및 HTTP API 구현
- 선수 개념 그래프 기반 개인화
- claim·evidence·학습 컨텍스트 영속화

## 3. 계층과 의존 방향

```text
interfaces/mcp (C 담당)
        ↓
application/evidence_qa (B 담당 유스케이스와 포트)
        ↓
domain (공통 데이터 계약)
        ↑
infrastructure/retrieval, infrastructure/generation (B 담당 어댑터)
        ↓
SQLite, Chroma, 외부 생성 API
```

- MCP 계층은 B 서비스를 호출하지만 검색·생성 구현 세부사항을 알지 않는다.
- 애플리케이션 계층은 구체적인 Chroma 또는 모델 SDK 대신 포트에 의존한다.
- 생성 모델은 `chunk_id`, 버전 ID, 페이지 번호를 만들거나 수정하지 않는다.
- 실제 식별자와 페이지 정보는 서버가 검색 결과에서 가져와 citation을 조립한다.

## 4. 검색 데이터 책임 분리

검색 데이터는 수명과 변경 원인에 따라 세 단계로 분리한다.

### 4.1 `Chunk`: 청크 자체의 고정 정보

`original_text`, `text`, `pdf_page`처럼 질문과 관계없이 청크 자체에 속하는 정보는 기존 `Chunk`를 사용한다.

주요 필드:

| 필드 | 의미 |
|---|---|
| `chunk_id` | 청크 식별자 |
| `parse_revision_id` | 청크를 만든 파싱 리비전 |
| `chunk_index` | 논문 내 청크 순서 |
| `original_text` | PDF에서 추출한 원문 |
| `text` | 한국어 번역문. 번역 전 또는 실패 시 `null` |
| `translation_revision_id` | 번역 리비전 식별자 |
| `printed_page_label` | 확인된 인쇄 페이지 번호. 불확실하면 `null` |
| `pdf_page` | PDF 파일 내 1-based 물리 페이지 |
| `section_id` | 확인된 섹션 식별자. 모르면 `null` |

검색 점수나 순위는 `Chunk`에 추가하지 않는다. 동일한 청크라도 질문에 따라 점수와 순위가 달라지기 때문이다.

### 4.2 `RetrievedChunk`: 질문마다 달라지는 청크별 검색 정보

`RetrievedChunk`는 한 질문에 대한 개별 청크의 검색 결과다.

```python
@dataclass(frozen=True)
class RetrievedChunk:
    chunk: Chunk
    score: float
    rank: int
```

규칙:

- `chunk`는 기존 `Chunk`를 참조한다.
- `score`는 해당 검색 실행에서 계산된 관련도이며 유한한 숫자여야 한다. `NaN`과 무한대는 허용하지 않는다.
- `rank`는 1부터 시작한다.
- `score`와 `rank`를 SQLite의 청크 레코드에 저장하지 않는다.
- 같은 청크가 다른 질문에 검색되면 서로 다른 `RetrievedChunk`가 만들어질 수 있다.
- 같은 `SearchResult` 안에서는 높은 `score`가 더 높은 관련성을 뜻한다.
- 서로 다른 `retrieval_method`가 만든 score는 직접 비교하지 않는다.
- 최종 노출 순서는 score 자체가 아니라 명시적인 `rank`를 기준으로 한다.
- 동점 순서는 retriever가 `chunk_id` 등 안정적인 보조 기준을 사용해 결정적으로 정한다.

### 4.3 `SearchResult`: 검색 실행 전체의 정보

`embedding_set_id`, 검색 방식처럼 한 번의 검색 전체에 적용되는 정보는 `SearchResult`에 둔다.

```python
class RetrievalMethod(str, Enum):
    SAMPLE = "sample"
    VECTOR = "vector"
    KEYWORD = "keyword"
    HYBRID = "hybrid"


@dataclass(frozen=True)
class SearchResult:
    query: str
    scope: SearchScope
    retrieval_method: RetrievalMethod
    embedding_set_id: str | None
    items: tuple[RetrievedChunk, ...]
```

주요 규칙:

- `embedding_set_id`를 개별 `RetrievedChunk`에 반복하지 않는다.
- `retrieval_method`는 `RetrievalMethod` Enum으로 제한하고 실제 사용한 검색 방식을 기록한다.
- `query`는 검색에 실제 사용한 최종 독립 질문이다. 원래 사용자 질문은 애플리케이션 요청에서 별도로 보존한다.
- `items`는 `rank` 오름차순으로 정렬한다.
- 하나의 `SearchResult` 안에서 `rank`는 중복되지 않는다.
- 검색 결과가 없어도 실행 정보 보존을 위해 빈 `items`를 가진 `SearchResult`를 반환할 수 있다.

## 5. 검색 범위

검색기는 학습 컨텍스트가 고정한 버전 밖의 청크를 반환하면 안 된다.

```python
@dataclass(frozen=True)
class SearchScope:
    version_id: str
    parse_revision_id: str
    translation_revision_id: str | None
    pdf_pages: tuple[int, ...] = ()
    section_ids: tuple[str, ...] = ()
```

규칙:

- `version_id`와 `parse_revision_id`는 필수다.
- 번역문 검색에서는 `translation_revision_id`가 필요하다.
- `pdf_pages`와 `section_ids`가 비어 있으면 고정된 리비전 전체를 검색한다.
- `pdf_pages`와 `section_ids`를 함께 지정하면 두 조건을 모두 만족하는 청크만 반환하는 AND 조건으로 해석한다.
- `pdf_pages`의 모든 값은 1 이상이어야 하며 중복 값은 허용하지 않는다.
- `section_ids`에는 빈 문자열과 중복 값을 허용하지 않는다.
- `version_id`와 `parse_revision_id`는 공백이 아닌 문자열이어야 한다.
- 페이지 범위는 `pdf_page`를 사용한다.
- `printed_page_label`만으로 검색 범위를 확정하지 않는다. 중복되거나 불확실할 수 있기 때문이다.
- `context_id`를 `SearchScope`로 해석하는 책임은 애플리케이션 계층에 있다.

`embedding_set_id`는 검색 범위가 아니라 실제 검색 실행에서 사용한 색인 정보이므로 `SearchResult`에 기록한다.

### 5.1 샘플 검색과 실제 검색의 텍스트 정책

현재 ingestion이 만든 청크는 `original_text`만 있고 `text`가 `null`일 수 있다. 첫 계약 검증에서는 수동으로 한국어 `text`가 채워진 fixture를 기본으로 사용한다.

- 실제 서비스의 기본 검색·생성 대상은 한국어 번역문인 `Chunk.text`다.
- `SampleRetriever`는 검색 계약 자체를 검증할 목적으로만 `original_text` 검색을 허용할 수 있다.
- `original_text`를 사용한 샘플 결과에서는 `quote_ko`를 반드시 `null`로 둔다.
- 원문을 `Chunk.text`에 복사하거나 한국어 번역으로 표시하지 않는다.
- 실제 번역 검색에서는 `translation_revision_id`가 있는 청크만 대상으로 한다.
- 번역 실패로 `text`가 `null`인 청크는 실제 번역문 검색과 한국어 인용 대상에서 제외한다.

## 6. 검색 포트

애플리케이션은 구체적인 검색 구현 대신 다음 형태의 포트에 의존한다.

```python
class ScopedRetriever(Protocol):
    def search(
        self,
        question: str,
        scope: SearchScope,
        top_k: int,
    ) -> SearchResult:
        ...
```

예상 구현체:

| 구현체 | 용도 |
|---|---|
| `SampleRetriever` | 샘플 청크 기반 계약·단위 테스트 |
| `ChromaRetriever` | 실제 벡터 검색 연결 |
| `HybridRetriever` | 키워드 검색과 벡터 검색 결합 |

모든 구현체는 동일한 `SearchResult`를 반환해야 한다.

## 7. Claim과 Citation

### 7.1 Claim

Claim은 최종 답변을 구성하는, 근거로 검증 가능한 최소 주장 단위다.

```python
@dataclass(frozen=True)
class Claim:
    claim_id: str
    text: str
    evidence_ids: tuple[str, ...]
```

규칙:

- `claim_id`는 한 답변 안에서 유일하다.
- `text`는 비어 있지 않아야 한다.
- 최종 답변에 포함되는 claim은 하나 이상의 유효한 `evidence_id`를 가져야 한다.
- 한 claim에 서로 독립적인 사실을 과도하게 결합하지 않는다.
- 근거가 지원하는 범위보다 강한 표현을 사용하지 않는다.

### 7.2 Evidence와 Citation의 구분

- `Evidence`는 claim을 뒷받침하는 내부 근거 단위이며 기존 `domain.models.Evidence`를 재사용한다.
- `Citation`은 그 근거를 사용자와 MCP 호출자에게 보여주기 위한 버전·페이지 포함 표현이다.
- 두 구조는 같은 `evidence_id`로 연결한다.

변환 흐름은 다음과 같다.

```text
Chunk
→ 기존 Evidence(evidence_id, chunk_id, quote_ko, quote_original)
→ Claim이 evidence_id 참조
→ Evidence와 SearchScope의 버전 정보 결합
→ Citation 조립
```

기존 `Evidence`와 동일한 역할의 새 클래스를 추가하지 않는다. `Citation`은 저장된 원문 정보를 대체하지 않으며 응답 시점에 서버가 실제 `Evidence`, `Chunk`, `SearchScope`를 대조해 조립한다.

```python
@dataclass(frozen=True)
class Citation:
    evidence_id: str
    chunk_id: str
    version_id: str
    parse_revision_id: str
    translation_revision_id: str | None
    printed_page_label: str | None
    pdf_page: int
    quote_ko: str | None
    quote_original: str
```

규칙:

- `pdf_page`는 1 이상이다.
- `quote_ko`는 해당 청크의 `text`에서 별도로 검사한다.
- `quote_original`은 해당 청크의 `original_text`에서 별도로 검사한다.
- 번역문과 원문의 문자 offset이 같다고 가정하지 않는다.
- 번역문이 없으면 `quote_ko`는 `null`이다.
- 한국어 발췌는 사용자 표시에서 한국어 번역임을 구분한다.
- `printed_page_label`이 없으면 `PDF 기준 N페이지`로 표시한다.

### 7.3 `get_evidence` 전용 응답 모델

`get_evidence`는 citation보다 상세한 확인 정보를 반환하며 파일 표시 이름이 필요하므로 전용 응답 모델을 사용한다.

```python
@dataclass(frozen=True)
class EvidenceDetail:
    evidence_id: str
    chunk_id: str
    version_id: str
    parse_revision_id: str
    translation_revision_id: str | None
    file_display_name: str
    printed_page_label: str | None
    pdf_page: int
    quote_ko: str | None
    quote_original: str


@dataclass(frozen=True)
class GetEvidenceResult:
    evidence: tuple[EvidenceDetail, ...]
```

규칙:

- 요청한 `context_id`가 고정한 버전과 리비전에 속한 근거만 반환한다.
- `file_display_name`은 사용자 표시용 파일명이며 내부 절대 경로를 포함하지 않는다.
- 존재하지 않거나 현재 context 밖의 `evidence_id`는 반환하지 않고 `NOT_FOUND` 또는 계약된 오류로 처리한다.
- `EvidenceDetail`의 인용문과 페이지는 `Citation`과 같은 검증 규칙을 적용한다.
- `get_evidence` 전용 모델을 `Citation`에 합치지 않는다. 파일 확인 응답과 일반 답변 citation의 책임을 구분한다.

## 8. 답변 상태와 사유

```python
class AnswerStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NEEDS_CLARIFICATION = "needs_clarification"
```

```python
class ReasonCode(str, Enum):
    EVIDENCE_NOT_FOUND = "evidence_not_found"
    EXTRACTION_LIMITED = "extraction_limited"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    UNSUPPORTED_SCOPE = "unsupported_scope"
    VERIFICATION_FAILED = "verification_failed"
```

| status | 의미 | reason_code |
|---|---|---|
| `ok` | 최종 claim이 모두 검증됨 | `null` |
| `partial` | 일부 claim만 검증됨 | 필수 |
| `insufficient_evidence` | 검색 근거로 답할 수 없음 | 필수 |
| `needs_clarification` | 질문 또는 범위를 명확히 해야 함 | `null` |

외부 모델 API 장애는 근거 부족이 아니므로 `ReasonCode`로 숨기지 않고 `UPSTREAM_UNAVAILABLE` 애플리케이션 오류로 처리한다.

## 9. 최종 답변 계약

```python
@dataclass(frozen=True)
class AnswerResult:
    status: AnswerStatus
    answer_ko: str
    claims: tuple[Claim, ...]
    citations: tuple[Citation, ...]
    reason_code: ReasonCode | None
```

불변 조건:

- `ok`이면 claim이 하나 이상이고 `reason_code`는 `null`이다.
- `partial`이면 claim이 하나 이상이고 `reason_code`가 반드시 있어야 한다.
- `insufficient_evidence`이면 claims는 비어 있고 `reason_code`가 반드시 있어야 한다.
- `needs_clarification`이면 claims는 비어 있고 `reason_code`는 `null`이다. 사용자에게 필요한 확인 내용은 `answer_ko`에 간단히 표시한다.
- 모든 `Claim.evidence_ids`는 `citations`의 `evidence_id`에 존재한다.
- citation의 버전과 리비전은 요청에 사용한 `SearchScope`와 일치한다.
- `answer_ko`는 검증을 통과한 최종 claims만 반영한다.

JSON 예시:

```json
{
  "status": "ok",
  "answer_ko": "Self-attention은 토큰 관계를 병렬로 계산할 수 있습니다.",
  "claims": [
    {
      "claim_id": "claim-1",
      "text": "Self-attention은 토큰 관계를 병렬로 계산할 수 있다.",
      "evidence_ids": ["evidence-1"]
    }
  ],
  "citations": [
    {
      "evidence_id": "evidence-1",
      "chunk_id": "chunk-12",
      "version_id": "version-1",
      "parse_revision_id": "parse-1",
      "translation_revision_id": null,
      "printed_page_label": null,
      "pdf_page": 4,
      "quote_ko": null,
      "quote_original": "The attention layers allow parallel computation."
    }
  ],
  "reason_code": null
}
```

## 10. 생성 포트

생성 모델에는 검색 결과 전체의 저장소 객체를 직접 넘기지 않고 필요한 텍스트와 근거 식별자만 제공한다.

```python
@dataclass(frozen=True)
class EvidenceInput:
    evidence_id: str
    chunk_id: str
    text_ko: str | None
    original_text: str
    printed_page_label: str | None
    pdf_page: int
```

```python
@dataclass(frozen=True)
class GeneratedClaim:
    text: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class GeneratedAnswerDraft:
    claims: tuple[GeneratedClaim, ...]
```

```python
class ClaimGenerator(Protocol):
    def generate_claims(
        self,
        question: str,
        evidence: Sequence[EvidenceInput],
    ) -> GeneratedAnswerDraft:
        ...
```

- 생성 모델의 출력은 검증 전 초안이다.
- 생성 모델은 citation의 버전, 리비전, 페이지 정보를 생성하지 않는다.
- 생성된 `evidence_id`가 입력 목록에 없으면 구조 검증에서 실패시킨다.
- 실제 provider와 테스트용 fake provider는 같은 포트를 구현한다.

## 11. 검증 단계

`frozen=True`는 객체 변경만 막으며 값의 유효성을 자동으로 보장하지 않는다. 검증 책임은 단일 객체 규칙과 객체 사이 관계 규칙으로 나눈다.

### 단일 객체 검증

각 dataclass의 `__post_init__`에서 해당 객체만으로 판정할 수 있는 규칙을 검사한다.

- ID와 필수 문자열이 공백인지 확인
- `RetrievedChunk.rank >= 1` 확인
- `RetrievedChunk.score`가 유한한 숫자인지 확인
- `Citation.pdf_page >= 1` 확인
- `EvidenceDetail.pdf_page >= 1` 확인
- `SearchScope.pdf_pages`와 `section_ids`의 값·중복 확인
- `Claim.text`와 `Claim.evidence_ids`가 비어 있지 않은지 확인

### 관계 검증

여러 객체를 함께 봐야 하는 규칙은 별도의 validator에서 검사한다. dataclass가 DB나 다른 객체를 조회하지 않게 한다.

- `SearchResult`의 rank 순서와 중복 확인
- claim ID 중복 확인
- claim과 citation의 evidence ID 대조
- citation과 검색 결과의 chunk ID 대조
- citation과 `SearchScope`의 버전·리비전 대조
- 답변 status와 claims·reason code 조합 확인
- 인용문과 실제 청크 텍스트의 일치 확인

### 구조 검증

모든 claim에 적용한다.

- claim ID와 evidence ID가 비어 있지 않은지 확인
- 한 답변 안의 claim ID 중복 확인
- claim이 참조한 evidence ID의 존재 확인
- citation의 chunk가 검색 결과에 존재하는지 확인
- citation의 버전과 리비전이 검색 범위와 일치하는지 확인
- `pdf_page`와 청크 메타데이터 일치 확인
- `quote_ko`와 `quote_original`의 개별 발췌 일치 확인
- 번역문이 없는데 `quote_ko`가 만들어지지 않았는지 확인

### 의미 검증

다음 유형은 원문을 함께 제공해 별도로 검증한다.

- 수치
- 수식
- 비교
- 인과관계
- 강한 일반화

검증 실패 claim은 제한된 수정 후 다시 검사하거나 제거·보류한다. 최종 `answer_ko`는 검증을 통과한 claim으로만 조립한다. 모델 검증은 정확성을 보장하는 수단이 아니라 추가 방어선으로 취급한다.

## 12. 처리 순서

```text
1. context_id를 고정된 SearchScope로 해석
2. ScopedRetriever로 SearchResult 획득
3. SearchResult.items의 Chunk에서 EvidenceInput 구성
4. ClaimGenerator로 GeneratedAnswerDraft 생성
5. 서버가 실제 Chunk와 SearchScope에서 Citation 조립
6. 모든 claim과 citation 구조 검증
7. 수치·비교·인과 claim 의미 검증
8. 실패 claim 수정·제거 또는 보류
9. 검증된 claims로 answer_ko 조립
10. AnswerResult 반환
```

## 13. 직렬화

도메인 모델을 MCP의 JSON 표현과 분리하기 위해 직렬화는 `application/evidence_qa/serialization.py`에서 담당한다.

```text
domain dataclass / Enum
→ serialization.py
→ JSON 호환 dict
→ C 담당 MCP structuredContent
```

규칙:

- 필드명은 영어 `snake_case`를 유지한다.
- Enum은 문자열 값으로 변환한다.
- tuple은 JSON 배열로 변환한다.
- `None`은 JSON `null`로 변환한다.
- 내부 절대 경로, API 키, 예외 스택은 직렬화 결과에 포함하지 않는다.
- 사람이 읽는 MCP `text` 표현은 C 계층이 만들며 B의 structured data 의미를 바꾸지 않는다.
- `AnswerResult`, `SearchResult`, `GetEvidenceResult` 각각의 직렬화 함수를 제공한다.

## 14. 요약과 가이드의 후속 적용

요약과 가이드도 별도 근거 체계를 만들지 않고 같은 `SearchResult`, `Claim`, `Citation` 계약을 재사용한다.

- 요약 문장은 검증 가능한 claim으로 구성한다.
- 섹션 요약은 `SearchScope.section_ids`로 범위를 제한한다.
- 페이지 요약은 `SearchScope.pdf_pages`로 범위를 제한한다.
- 가이드 단계에는 근거 citation과 현재 제한사항을 연결한다.
- 검수된 선수 개념 그래프가 없으면 일반 섹션 읽기 가이드로 제한한다.

## 15. 첫 구현 파일과 테스트 범위

기존 A·C 담당 파일을 변경하지 않고 신규 파일로 시작한다.

```text
src/solo_leveling/
├─ domain/
│  └─ evidence_qa.py
└─ application/
   └─ evidence_qa/
      ├─ __init__.py
      ├─ ports.py
      └─ serialization.py

tests/unit/evidence_qa/
├─ test_contracts.py
└─ test_ports.py
```

첫 단위 테스트는 다음을 검증한다.

1. `RetrievedChunk`의 `rank`가 1 이상인지 확인
2. `SearchResult.items`의 rank 순서와 중복 확인
3. `score`가 유한한 숫자인지 확인
4. `embedding_set_id`가 `SearchResult`에만 존재하는지 확인
5. `RetrievalMethod` Enum 이외의 검색 방식 거부
6. `SearchScope`의 페이지·섹션 필터 검증
7. 원문 검색 결과에서 `quote_ko`가 `null`인지 확인
8. `Citation.pdf_page`와 `EvidenceDetail.pdf_page`가 1 이상인지 확인
9. 빈 claim 텍스트 거부
10. 근거 없는 최종 claim 거부
11. claim의 evidence ID가 citations에 존재하는지 확인
12. status와 claims·reason code 조합 확인
13. `GetEvidenceResult`가 파일 표시 이름을 반환하고 절대 경로를 노출하지 않는지 확인
14. JSON 호환 직렬화와 `snake_case` 필드명 확인
15. fake retriever와 fake generator가 포트 계약을 만족하는지 확인

## 16. 후속 통합 조건

샘플 기반 계약과 테스트가 통과한 뒤 다음을 별도 작업으로 진행한다.

1. A 담당과 누락된 Chroma `vector_store` 계약 확인
2. `ChromaRetriever` 구현
3. 번역 리비전과 embedding set의 실제 저장·조회 연결
4. 실제 생성 provider 하나 선택 및 비용·토큰 제한 설정
5. 구조 검증 후 의미 검증 추가
6. 요약과 일반 섹션 가이드 구현
7. C 담당의 `ask_paper`, `get_evidence`, `get_learning_guide` MCP 도구와 연결
