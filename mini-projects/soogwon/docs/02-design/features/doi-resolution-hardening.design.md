# doi-resolution-hardening - 설계 문서

> 버전: 1.0.0 | 작성일: 2026-08-31 | 상태: 완료
> 프로젝트 수준: Starter | Plan: `docs/01-plan/features/doi-resolution-hardening.plan.md`

---

## 1. 설계 개요

`resolve_paper`의 식별자 처리 과정을 명시적인 분류와 단계별 조회로 변경한다. 완전한 DOI는 OpenAlex 대표 DOI로 먼저 조회하고, 404일 때만 동일 Work의 보조 위치 DOI를 정확 일치로 조회한다. 불완전한 DOI는 제목 검색으로 넘어가지 않으며, 입력 DOI와 OpenAlex 대표 DOI의 차이를 공개 응답에 표시한다.

핵심 원칙은 다음과 같다.

- 정확 식별자 조회와 제목 검색을 분리한다.
- fallback은 직접 DOI 조회가 404인 경우에만 최대 한 번 수행한다.
- 정확 일치 결과가 하나일 때만 논문을 확정한다.
- OpenAlex 메타데이터를 수정하거나 DOI의 공식성을 자동 판정하지 않는다.
- 기존 요청 예산과 deadline을 모든 fallback 요청이 공유한다.

## 2. 처리 흐름

```text
resolve_paper(identifier)
        │
        ▼
식별자 정규화·분류
        │
        ├── OpenAlex ID ── getWork ── exact / not_found
        │
        ├── 완전한 DOI ── getWork
        │                    │
        │                    ├── 성공 ── exact, matched_via=primary_doi
        │                    │
        │                    └── 404 ── findWorksByLocationDoi(limit=2)
        │                                  │
        │                                  ├── 0건 ── not_found
        │                                  ├── 1건 ── exact, matched_via=location_doi
        │                                  └── 2건 ── ambiguous, 자동 확정 금지
        │
        ├── 불완전 DOI ── INVALID_INPUT
        │
        └── 일반 제목 ── 기존 제목 검색·유사도 판정
```

## 3. 계층별 책임

| 계층 | 변경 책임 | 하지 않는 일 |
|------|-----------|---------------|
| MCP 서버 | `resolution` 출력 스키마 공개, 공개 오류 변환 | DOI 조회 순서를 결정하지 않는다. |
| PaperService | 입력 분류, 직접 조회·fallback 조정, 결과 수 판정, 경고 구성 | OpenAlex URL과 필터 문법을 직접 만들지 않는다. |
| ScholarlyProvider | 위치 DOI 정확 일치 조회 계약 제공 | 첫 결과를 자동 확정하지 않는다. |
| OpenAlexProvider | 고정 호스트에 filter 요청, 정규화, 캐시·예산·deadline 적용 | DOI의 공식성이나 올바른 발행 연도를 판정하지 않는다. |
| 도메인 모델 | resolution 메타데이터 타입 정의 | 제공자별 원시 위치 배열을 외부에 노출하지 않는다. |

## 4. 입력 분류

### 4.1 분류 타입

```ts
type IdentifierClassification =
  | { kind: "openalex"; normalized: string }
  | { kind: "doi"; normalized: string }
  | { kind: "incomplete_doi" }
  | { kind: "title" };
```

### 4.2 판정 순서

1. 앞뒤 공백을 제거한다.
2. OpenAlex ID 또는 OpenAlex Work URL이면 `openalex`로 분류한다.
3. `doi:`, `https://doi.org/`, `http://dx.doi.org/` 접두사를 제거하고 완전한 DOI 정규식 `^10\.\d{4,9}/\S+$`를 만족하면 소문자 DOI URL로 정규화한다.
4. 정규화 전후 값이 `10.<4~9자리>`로 시작하지만 완전한 DOI가 아니면 `incomplete_doi`로 분류한다.
5. 나머지는 일반 제목으로 분류한다.

불완전 DOI는 서비스 계층에서 다음 `AppError`를 발생시킨다.

```json
{
  "code": "INVALID_INPUT",
  "message": "DOI가 완전하지 않습니다. '10.xxxx/suffix' 형식으로 입력하세요.",
  "retryable": false,
  "details": {
    "expected_format": "10.xxxx/suffix"
  }
}
```

입력 스키마는 제목도 허용해야 하므로 Zod 단계에서 DOI 형태를 강제하지 않고 서비스 분류 결과로 오류를 만든다.

## 5. 제공자 계약

`ScholarlyProvider`에 다음 메서드를 추가한다.

```ts
findWorksByLocationDoi(
  normalizedDoiUrl: string,
  signal?: AbortSignal,
  deadlineAt?: number,
): Promise<PaperDetail[]>;
```

OpenAlex 구현은 다음 요청을 사용한다.

```text
GET /works
  ?filter=locations.landing_page_url:<normalized-doi-url>
  &per_page=2
  &select=<existing WORK_SELECT_FIELDS>
```

- `URLSearchParams`로 filter 값을 인코딩한다.
- `per_page=2`는 복수 결과 여부만 판정하기 위한 상한이다.
- 기존 `#request(..., "filter", signal, deadlineAt)`를 재사용한다.
- fallback 요청도 캐시 키, 요청 횟수, filter 크레딧, 비용 상한 및 deadline을 공유한다.
- 제공자 메서드는 배열만 반환하며 단일 결과 확정은 서비스가 수행한다.

## 6. 공개 응답 계약

기존 `ResolvePaperOutput`에 exact 결과용 선택 필드를 추가한다.

```ts
type PaperResolution = {
  requestedIdentifier: string;
  normalizedIdentifier: string;
  matchedVia: "openalex_id" | "primary_doi" | "location_doi" | "title";
  providerPrimaryDoi: string | null;
};

type ResolvePaperOutput = {
  status: "exact" | "ambiguous" | "not_found";
  paper?: PaperDetail;
  candidates?: PaperSummary[];
  resolution?: PaperResolution;
  warnings: Warning[];
};
```

MCP 공개 JSON은 기존 규칙대로 snake_case를 사용한다.

```json
{
  "status": "exact",
  "paper": {
    "id": "W2626778328",
    "doi": "https://doi.org/10.65215/2q58a426"
  },
  "resolution": {
    "requested_identifier": "10.48550/arXiv.1706.03762",
    "normalized_identifier": "https://doi.org/10.48550/arxiv.1706.03762",
    "matched_via": "location_doi",
    "provider_primary_doi": "https://doi.org/10.65215/2q58a426"
  },
  "warnings": []
}
```

`resolution`은 추가 선택 필드이므로 기존 MCP 소비자의 필수 필드를 깨지 않는다.

## 7. 경고 정책

### 7.1 위치 DOI 확인

위치 fallback으로 정확히 한 건을 찾으면 다음 경고를 추가한다.

```json
{
  "code": "DOI_RESOLVED_VIA_LOCATION",
  "message": "입력 DOI가 OpenAlex의 보조 위치에서 확인됐습니다."
}
```

### 7.2 대표 DOI 충돌

정규화된 입력 DOI와 `paper.doi`가 다르면 추가 경고를 반환한다.

```json
{
  "code": "IDENTIFIER_CONFLICT",
  "message": "입력 DOI와 OpenAlex 대표 DOI가 다릅니다. 두 식별자를 원문에서 확인하세요.",
  "details": {
    "requested_doi": "https://doi.org/10.48550/arxiv.1706.03762",
    "provider_primary_doi": "https://doi.org/10.65215/2q58a426"
  }
}
```

경고는 DOI의 진위나 공식성을 판정하지 않고 관찰된 차이만 설명한다.

### 7.3 복수 위치 결과

정확 일치 filter가 복수 Work를 반환하면 다음 결과를 사용한다.

```json
{
  "status": "ambiguous",
  "candidates": [],
  "warnings": [
    {
      "code": "AMBIGUOUS_DOI_LOCATION",
      "message": "동일한 위치 DOI가 여러 OpenAlex Work에 연결되어 논문을 확정할 수 없습니다."
    }
  ]
}
```

실제 candidates에는 최대 2개의 요약을 포함한다. 첫 결과를 자동 선택하지 않는다.

## 8. 오류·예산·캐시 정책

- 직접 DOI 조회의 404만 fallback 조건이다.
- 인증, 429, 5xx, 타임아웃, 잘못된 JSON은 fallback하지 않고 기존 오류를 그대로 반환한다.
- fallback 전 별도의 deadline을 만들지 않고 도구 호출의 기존 `deadlineAt`을 전달한다.
- 직접 조회와 fallback의 사용량은 같은 AsyncLocalStorage 컨텍스트에 누적된다.
- `#request`는 각 외부 요청 전에 `Date.now() + requestTimeoutMs`가 `deadlineAt`을 넘는지 확인하고, 넘으면 요청 수·크레딧을 차감하거나 fetch를 호출하기 전에 `PROVIDER_TIMEOUT`을 반환한다.
- fallback 결과는 기존 성공 캐시 TTL을 사용한다.
- fallback 0건은 별도 음성 캐시를 추가하지 않고 filter 응답 자체를 성공 캐시한다.
- 요청 URL은 설정된 OpenAlex 호스트에서만 만들고 사용자 URL을 직접 요청하지 않는다.

## 9. 테스트 설계

### 9.1 단위 테스트

| 대상 | 사례 |
|------|------|
| 입력 분류 | OpenAlex ID·URL, DOI·DOI URL, 대소문자, 불완전 `10.65215`, 빈 suffix, 일반 제목 |
| DOI 정규화 | `doi:`, `dx.doi.org`, 공백, 소문자 변환 |
| 서비스 결과 | 직접 DOI 성공, fallback 0·1·2건, 대표 DOI 일치·불일치 |
| 오류 | 불완전 DOI가 provider 호출 없이 `INVALID_INPUT` 반환 |

### 9.2 제공자 통합 테스트

- 단일 DOI 404 뒤 `locations.landing_page_url` filter URL이 정확히 만들어진다.
- 위치 조회가 최대 `per_page=2`와 기존 `select`를 사용한다.
- fallback 요청이 사용량·크레딧에 포함된다.
- deadline이 부족하면 추가 요청 전에 `PROVIDER_TIMEOUT`을 반환한다.
- 401·429·5xx에서는 위치 fallback을 실행하지 않는다.

### 9.3 MCP 계약 테스트

- exact 결과의 `resolution`이 snake_case로 반환된다.
- outputSchema에 `resolution`과 `matched_via` enum이 공개된다.
- `IDENTIFIER_CONFLICT` details는 명시적 JSON 스키마를 유지한다.
- 불완전 DOI가 공개 `INVALID_INPUT` 오류로 변환된다.

### 9.4 핵심 회귀 픽스처

```text
입력 DOI: 10.48550/arXiv.1706.03762
직접 조회: 404
위치 조회: W2626778328 한 건
대표 DOI: 10.65215/2q58a426
예상 결과: exact + location_doi + DOI_RESOLVED_VIA_LOCATION + IDENTIFIER_CONFLICT
```

테스트는 실제 OpenAlex 상태에 의존하지 않고 모의 fetch 응답으로 고정한다.

## 10. 구현 순서

1. 도메인에 `PaperResolution`과 `resolution` 선택 필드 추가
2. 식별자 분류·불완전 DOI 검증 함수 작성
3. `ScholarlyProvider.findWorksByLocationDoi()` 계약 추가
4. OpenAlex 위치 DOI filter 구현
5. `PaperService.resolve()`에 직접 조회·fallback·복수 결과 흐름 연결
6. MCP 출력 스키마에 `resolution` 추가
7. 단위·통합·계약 회귀 테스트 추가
8. README DOI 사용법과 충돌 경고 설명 갱신
9. 타입 검사, 전체 테스트, 빌드, audit 및 Inspector 스키마 확인

## 11. 요구사항 추적표

| Plan 요구사항 | 설계 대응 | 검증 |
|---------------|-----------|------|
| FR-1 DOI 입력 분류 | 4장 분류 타입·판정 순서 | 분류 단위 테스트 |
| FR-2 위치 DOI fallback | 2장 흐름, 5장 제공자 계약 | 제공자·서비스 통합 테스트 |
| FR-3 보수적 결과 확정 | 2장 0·1·2건, 7.3 복수 정책 | fallback 결과 수 테스트 |
| FR-4 식별자 출처 공개 | 6장 resolution, 7장 경고 | MCP 계약 테스트 |
| FR-5 기존 보호 정책 | 8장 예산·deadline·캐시 | 사용량·deadline 회귀 테스트 |

## 12. 학습 포인트

- 외부 제공자의 대표 식별자와 사용자가 입력한 식별자는 같은 논문에서도 다를 수 있다.
- fallback은 실패를 숨기는 기능이 아니라 확인 경로와 불확실성을 더 명확히 보여주는 기능이어야 한다.
- 제목 검색과 식별자 확인을 분리하면 잘못된 자동 선택을 줄일 수 있다.
- API 결과가 하나라는 사실만으로 DOI의 공식성이나 메타데이터 정확성을 보장할 수는 없다.
