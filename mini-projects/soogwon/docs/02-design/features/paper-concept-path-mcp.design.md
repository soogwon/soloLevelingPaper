# paper-concept-path-mcp - 설계 문서

> 버전: 1.0.0 | 작성일: 2026-08-28 | 상태: 완료
> 프로젝트 수준: Starter | Plan: `docs/01-plan/features/paper-concept-path-mcp.plan.md`

---

## 1. 설계 개요

### 1.1 목적

사용자가 연구 주제 또는 시작 논문을 입력하면 OpenAlex에서 관련 논문을 조회하고, 인용 관계·발행 연도·주제 유사성을 조합해 짧고 근거를 확인할 수 있는 논문 경로를 반환하는 로컬 MCP 서버를 구현한다.

첫 버전은 다음 원칙을 따른다.

- 로컬 `stdio` MCP 서버를 먼저 완성한다.
- OpenAlex를 단일 데이터 제공자로 사용한다.
- 논문 전문이나 생성형 AI 없이 OpenAlex 메타데이터만 사용한다.
- 실제 인용 관계와 알고리즘이 추론한 유사 관계를 명확히 구분한다.
- 결과를 재현할 수 있도록 결정론적 경로 점수 방식을 사용한다.
- 서버 코어를 전송 계층에서 분리해 이후 Streamable HTTP 원격 배포를 지원한다.

### 1.2 기술 결정

| 항목 | 선택 | 이유 |
|------|------|------|
| 언어 | TypeScript | MCP SDK와 스키마 타입을 함께 관리하기 쉽다. |
| 런타임 | Node.js 20 이상 | 현재 MCP TypeScript SDK 시작 가이드의 기준이며 내장 `fetch`를 사용할 수 있다. |
| 모듈 방식 | ESM | MCP SDK가 ESM을 기준으로 제공된다. |
| MCP SDK | 공식 TypeScript SDK v2 안정 버전 | 2026-07-28 프로토콜 계열과 로컬·원격 전송을 지원한다. 구현 시 정확한 버전을 고정한다. |
| 입력 검증 | Zod v4 | MCP 도구 입력과 내부 설정을 같은 스키마로 검증한다. |
| 논문 데이터 | OpenAlex REST API | Works 검색, 인용·참고문헌, 관련 논문, Topic 및 Keyword를 제공한다. |
| 로컬 전송 | `stdio` | MCP 호스트가 자식 프로세스로 실행하기 가장 단순하다. |
| 원격 전송 | Streamable HTTP(후속) | 원격 MCP의 권장 전송 방식이다. 기존 HTTP+SSE는 사용하지 않는다. |
| 캐시 | 프로세스 내 TTL/LRU 캐시 | 첫 버전에는 데이터베이스가 필요 없고 테스트가 단순하다. |
| 테스트 | Vitest + MCP 인메모리 전송 또는 도구 핸들러 직접 호출 | 네트워크 없는 결정론적 테스트와 MCP 계약 테스트를 지원한다. |

### 1.3 설계에서 확정한 기본 사용자 흐름

1. 사용자는 연구 주제를 검색해 후보 논문을 확인하거나 DOI·OpenAlex ID·제목으로 시작 논문을 찾는다.
2. 사용자는 시작 논문과 선택적인 목표 주제를 이용해 개념 경로 생성을 요청한다.
3. 서버는 시작 논문의 인용·참고문헌·관련 논문 이웃을 제한된 범위에서 조회한다.
4. 서버는 인용 관계, 주제 겹침, 키워드 겹침, 시간 순서 및 검색 관련도를 점수화한다.
5. 서버는 3~5개 논문으로 구성된 최적 경로와 각 연결의 근거·신뢰도·추론 여부를 반환한다.

## 2. 시스템 구조

### 2.1 구성 요소

```text
MCP 클라이언트
    │ stdio (로컬)
    ▼
MCP 서버 / 도구 등록
    │
    ├── 입력 검증 및 오류 변환
    ├── 논문 검색·확인 서비스
    └── 개념 경로 서비스
          │
          ├── 후보 그래프 생성기
          ├── 경로 점수 계산기
          └── 관계·근거 포맷터
                 │
                 ▼
          OpenAlex 제공자 어댑터
                 │
          TTL/LRU 메모리 캐시
                 │ HTTPS
                 ▼
          OpenAlex REST API
```

### 2.2 계층별 책임

| 계층 | 책임 | 금지 사항 |
|------|------|-----------|
| MCP 계층 | 도구 등록, 입출력 스키마, MCP 결과와 오류 생성 | OpenAlex 응답을 직접 처리하지 않는다. |
| 애플리케이션 서비스 | 사용 흐름 조정, 요청 제한 적용, 결과 조립 | HTTP URL이나 제공자 필드에 직접 의존하지 않는다. |
| 도메인 | 정규화 모델, 점수 계산, 경로 선택, 관계 분류 | 네트워크와 환경변수에 의존하지 않는다. |
| 제공자 어댑터 | OpenAlex 호출, 응답 검증, 정규화 | MCP 응답 형식을 만들지 않는다. |
| 기반 기능 | 설정, 캐시, 타임아웃, 로깅 | 비밀값이나 MCP stdout을 오염시키지 않는다. |

### 2.3 처리 순서

```text
도구 호출
  → Zod 입력 검증
  → 시작 논문 확인
  → 캐시 조회
  → OpenAlex 후보 조회(제한 및 타임아웃 적용)
  → 정규화
  → 후보 중복 제거 및 그래프 구성
  → 간선 점수 계산
  → 최적 경로 선택
  → 근거와 경고 조립
  → structuredContent + text 결과 반환
```

## 3. 디렉터리와 파일 설계

```text
paper-concept-path-mcp/
├── src/
│   ├── index.ts                    # stdio 실행 진입점
│   ├── server.ts                   # MCP 서버 팩토리와 도구 등록
│   ├── config.ts                   # 환경변수 검증 및 기본값
│   ├── tools/
│   │   ├── search-papers.ts
│   │   ├── resolve-paper.ts
│   │   └── trace-concept-path.ts
│   ├── services/
│   │   ├── paper-service.ts
│   │   └── concept-path-service.ts
│   ├── domain/
│   │   ├── models.ts
│   │   ├── scoring.ts
│   │   ├── path-finder.ts
│   │   └── relationship.ts
│   ├── providers/
│   │   ├── scholarly-provider.ts
│   │   └── openalex-provider.ts
│   └── infrastructure/
│       ├── cache.ts
│       ├── errors.ts
│       └── logger.ts
├── tests/
│   ├── fixtures/openalex/
│   ├── unit/
│   ├── integration/
│   └── contract/
├── .env.example
├── package.json
├── tsconfig.json
├── vitest.config.ts
└── README.md
```

## 4. MCP 도구 계약

모든 도구는 사람이 읽을 수 있는 `content`와 기계가 사용할 수 있는 `structuredContent`를 동일한 의미로 반환한다. JSON 속성명과 MCP 도구명은 호환성을 위해 영어 `snake_case`를 사용하고 사용자 설명과 오류 메시지는 한국어를 기본으로 한다.

### 4.1 `search_papers`

연구 주제로 시작 논문 후보를 검색한다.

#### 입력

```ts
type SearchPapersInput = {
  query: string;             // 공백 제거 후 3~300자
  query_en?: string;         // 선택적 영어 검색어, 3~300자
  from_year?: number;        // 1800~현재 연도
  to_year?: number;          // from_year 이상, 현재 연도 이하
  limit?: number;            // 기본 5, 최소 1, 최대 10
  semantic?: boolean;        // 기본 false
};
```

#### 출력

```ts
type SearchPapersOutput = {
  query: string;
  papers: PaperSummary[];
  total_candidates?: number;
  warnings: Warning[];
};
```

#### 동작

- 기본 검색은 OpenAlex `/works?search=`를 사용한다.
- `query_en`이 있으면 제공자 검색에는 영어 검색어를 우선 사용하고 원래 `query`는 결과 설명에 보존한다. 서버가 자동 번역하지는 않는다.
- `semantic=true`이면 지원 여부를 어댑터에서 확인한 뒤 의미 검색을 사용한다.
- 결과는 OpenAlex 관련도 순서를 우선하며, 동률일 때 발행 연도 내림차순과 ID 오름차순을 사용한다.
- 철회된 논문은 기본 결과에서 제외하고 제외 사실을 경고에 기록한다.

### 4.2 `resolve_paper`

DOI, OpenAlex Work ID, OpenAlex URL 또는 제목으로 논문 하나를 확인한다.

#### 입력

```ts
type ResolvePaperInput = {
  identifier: string;        // 3~500자
};
```

#### 출력

```ts
type ResolvePaperOutput = {
  status: "exact" | "ambiguous" | "not_found";
  paper?: PaperDetail;
  candidates?: PaperSummary[]; // 최대 5개
  warnings: Warning[];
};
```

#### 확인 순서

1. OpenAlex ID 또는 URL
2. DOI 및 DOI URL
3. 제목 검색

제목 검색은 다음 결정 규칙을 순서대로 적용한다.

1. 유효한 DOI 또는 OpenAlex ID 직접 조회 성공: `exact`
2. 정규화 제목 완전 일치이며 입력에 포함된 연도·첫 저자도 일치: `exact`
3. 정규화 제목 완전 일치지만 연도 또는 첫 저자가 충돌: `ambiguous`
4. 제목 유사도 0.95 이상이고 1위와 2위의 유사도 차이가 0.10 이상: 잠정 `exact`
5. 그 외: 최대 5개 후보와 함께 `ambiguous`

정규화는 Unicode NFKC, 소문자화, 연속 공백 축소 및 문장부호 제거를 적용한다. 제목 유사도는 구현 시 결정론적인 토큰 기반 유사도로 정의하고 테스트 픽스처로 임계값을 검증한다. 실제 평가에서 오탐이 발생하면 임계값을 높이며, 확실하지 않은 경우 자동 선택보다 `ambiguous`를 우선한다.

### 4.3 `trace_concept_path`

시작 논문에서 관련 논문으로 이어지는 설명 가능한 경로를 생성한다.

#### 입력

```ts
type TraceConceptPathInput = {
  seed: string;                   // DOI, OpenAlex ID, URL 또는 제목
  target_query?: string;          // 선택 목표 주제, 3~300자
  direction?: "backward" | "forward" | "both"; // 기본 both
  max_depth?: number;             // 기본 2, 최소 1, 최대 3
  max_path_length?: number;       // 기본 4, 최소 2, 최대 5
  candidates_per_node?: number;   // 기본 6, 최소 2, 최대 10
};
```

#### 출력

```ts
type TraceConceptPathOutput = {
  seed: PaperSummary;
  target_query?: string;
  path: {
    nodes: PathNode[];
    edges: PathEdge[];
    score: number;              // 0~1
    score_margin: number | null; // 1위와 2위 후보 경로 점수 차이
  } | null;
  explored: {
    node_count: number;
    edge_count: number;
    request_count: number;
    truncated: boolean;
  };
  warnings: Warning[];
  methodology: MethodologySummary;
};
```

`MethodologySummary`에는 최소한 다음 재현성 정보를 포함한다.

```ts
type MethodologySummary = {
  provider: "openalex";
  retrieved_at: string;          // ISO 8601 UTC
  scoring_version: string;       // 예: "1.0.0"
  schema_version: string;        // 예: "1.0.0"
  weights: Record<string, number>;
  limits: {
    max_depth: number;
    max_path_length: number;
    candidates_per_node: number;
    max_nodes: number;
    max_requests: number;
    max_credits: number;
    max_estimated_cost_usd: number;
  };
  query_parameters: Record<string, string | number | boolean>;
  limitations: string[];
};
```

API 키, Authorization 헤더 및 사용자의 전체 검색어는 재현성 메타데이터에 포함하지 않는다. 검색어는 결과 본문에 이미 필요한 범위에서만 반환한다.

#### 부분 결과

- 시작 논문은 확인됐지만 2개 이상의 노드로 구성된 경로를 만들 수 없으면 `path: null`과 원인 경고를 반환한다.
- 제한 시간에 도달했지만 유효한 경로가 있으면 `truncated: true` 및 경고와 함께 최선의 경로를 반환한다.

## 5. 데이터 모델

### 5.1 정규화 논문

```ts
type PaperSummary = {
  id: string;                    // 정규화된 OpenAlex Work ID(W...)
  title: string;
  publication_year: number | null;
  publication_date: string | null; // ISO 날짜, 제공되는 경우
  authors: string[];             // 최대 10명, 초과 시 응답 표시로 알림
  doi: string | null;            // https://doi.org/... 형식
  source_url: string;            // OpenAlex 웹 또는 API 식별 URL
  cited_by_count: number;
  primary_topic: Topic | null;
  keywords: WeightedTerm[];
  is_retracted: boolean;
  version_group_key: string;      // DOI 우선, 없으면 제목·첫 저자·연도 해시
};

type PaperDetail = PaperSummary & {
  abstract: string | null;       // 제공되는 역색인을 복원한 경우에만 값 존재
  topics: Topic[];               // 최대 3개
  referenced_work_ids: string[];
  related_work_ids: string[];
};
```

OpenAlex가 평문 초록을 직접 제공하지 않는 경우, 제공되는 `abstract_inverted_index`가 있고 사용이 허용될 때만 순서를 복원한다. 필드가 없으면 `null`을 반환하며 생성하지 않는다.

논문 중복은 OpenAlex Work ID로 먼저 제거하고, DOI가 같으면 같은 출판 결과로 묶는다. DOI가 없을 때는 정규화 제목·첫 저자·발행 연도를 이용한 `version_group_key`로 잠재 중복을 표시하되 자동 병합은 하지 않는다. 사전출판본, 출판본, 정정 및 철회 항목은 별도 노드로 유지할 수 있으며 관계와 경고로 차이를 설명한다.

### 5.2 경로 노드와 간선

```ts
type PathNode = {
  index: number;
  paper: PaperSummary;
  matched_terms: string[];
};

type RelationshipType =
  | "cites"
  | "cited_by"
  | "related"
  | "topic_similar";

type PathEdge = {
  from_id: string;
  to_id: string;
  relationship: RelationshipType;
  evidence: Evidence[];
  score: number;                 // 0~1
  inferred: boolean;
  rationale: string;
};

type Evidence = {
  kind: "citation" | "topic" | "keyword" | "chronology" | "provider_relation";
  value: string;
  source: "openalex" | "derived";
};
```

`cites`와 `cited_by`는 OpenAlex의 실제 `referenced_works` 관계에 근거하므로 `inferred=false`이다. `related`와 `topic_similar`는 제공자 알고리즘 또는 서버 점수에서 도출되므로 `inferred=true`이다. 메타데이터만으로 `introduces`, `extends`, `contrasts`, `applies`를 단정하지 않는다.

### 5.3 경고와 오류

```ts
type Warning = {
  code: string;
  message: string;
  details?: Record<string, unknown>;
};

type ErrorCode =
  | "INVALID_INPUT"
  | "PAPER_NOT_FOUND"
  | "AMBIGUOUS_PAPER"
  | "PROVIDER_AUTH"
  | "PROVIDER_RATE_LIMIT"
  | "PROVIDER_TIMEOUT"
  | "PROVIDER_RESPONSE_INVALID"
  | "PATH_NOT_FOUND"
  | "INTERNAL_ERROR";
```

입력 오류는 MCP invalid-params 오류로 반환한다. 제공자 장애와 경로 없음은 도구 실행 결과의 구조화된 오류 또는 부분 결과로 반환해 클라이언트가 원인을 설명할 수 있게 한다. 내부 스택과 비밀값은 반환하지 않는다.

## 6. OpenAlex 제공자 설계

### 6.1 제공자 인터페이스

```ts
interface ScholarlyProvider {
  searchWorks(input: ProviderSearchInput, signal: AbortSignal): Promise<SearchResult>;
  getWork(identifier: string, signal: AbortSignal): Promise<PaperDetail | null>;
  getWorksByIds(ids: string[], signal: AbortSignal): Promise<PaperDetail[]>;
  getCitingWorks(id: string, limit: number, signal: AbortSignal): Promise<PaperDetail[]>;
}
```

### 6.2 API 사용 방식

- 기본 URL: `https://api.openalex.org`
- 단일 논문: `/works/{id 또는 DOI}`
- 검색: `/works?search={query}` 또는 지원되는 의미 검색 매개변수
- 해당 논문을 인용한 논문: `/works?filter=cites:{workId}`
- 참고문헌: `referenced_works` ID를 최대 100개 단위 OR 필터로 일괄 조회
- 의미적으로 관련된 논문: `related_works` ID 또는 `related_to` 필터 사용
- 응답 크기 절감을 위해 구현 시 필요한 필드만 `select`한다.

### 6.3 요청 정책

- `OPENALEX_API_KEY`는 선택 환경변수로 시작하되, 키가 없을 때의 현재 허용 범위를 문서화한다.
- API 키는 `Authorization: Bearer ...` 헤더로만 전송한다. URL 쿼리, 캐시 키, 로그, 오류 및 재현성 메타데이터에 포함하지 않는다.
- `OPENALEX_BASE_URL`은 테스트용 모의 서버를 위해 설정 가능하게 한다.
- 요청마다 5초 타임아웃을 적용한다.
- `search_papers`와 `resolve_paper`에는 10초, `trace_concept_path`에는 8초의 전체 도구 실행 deadline을 적용한다.
- 429와 일시적인 5xx는 `Retry-After`를 존중하며 최대 2회 지수 백오프로 재시도한다. `Retry-After`는 숫자 초와 HTTP 날짜를 모두 지원한다.
- 임의의 짧은 대기 상한을 두지 않는다. `Retry-After` 대기와 다음 요청의 5초 타임아웃을 모두 전체 deadline 안에 완료할 수 있을 때만 재시도한다. 불가능하면 조기 재시도하지 않고 부분 결과 또는 재시도 가능한 오류를 반환한다.
- `X-RateLimit-Remaining=0`인 429는 일일 예산 소진으로 보고 호출 내부에서 재시도하지 않으며, `X-RateLimit-Reset`을 재시도 가능 시각 정보로 사용한다.
- 400·403·404는 자동 재시도하지 않는다.
- 한 번의 `trace_concept_path` 호출에서 외부 요청은 기본 20회, 최대 40회로 제한한다.
- 요청 수와 별도로 기본 `max_credits=100` 및 예상 비용 `max_estimated_cost_usd=0.01`을 적용한다. 둘 중 하나라도 초과할 것으로 예상되면 추가 탐색을 중단하고 부분 결과를 반환한다.
- 동시 OpenAlex 요청은 기본 3개로 제한한다.
- `X-RateLimit-Credits-Used`, `X-RateLimit-Remaining`, 비용 메타데이터가 있으면 요청 통계에 반영하고 추정치와 실제값의 차이를 기록한다.
- 개인 API 키는 OpenAlex 로그인 자격 증명으로도 사용될 수 있으므로 README에 노출 시 회전 절차를 안내한다.

## 7. 경로 생성 알고리즘

### 7.1 후보 그래프 구성

1. 시작 논문을 확인하고 첫 노드로 추가한다.
2. 방향이 `backward` 또는 `both`이면 참고문헌 후보를 조회한다.
3. 방향이 `forward` 또는 `both`이면 시작 논문을 인용한 후보를 조회한다.
4. 인용 후보가 부족하면 `related_works`를 보조 후보로 추가한다.
5. 중복 ID, 철회 논문, 제목 없는 항목 및 연도 조건에 맞지 않는 후보를 제거한다.
6. 각 깊이에서 점수 상위 `candidates_per_node`개만 다음 탐색 대상으로 유지한다.
7. 요청 수, 전체 노드 수 50개, 깊이 또는 8초 내부 작업 제한 중 하나에 도달하면 탐색을 중단한다.

발행 순서는 `publication_date`, `publication_year`, 알 수 없음 순으로 판단한다. 날짜가 같거나 없으면 시간 방향 요소를 중립값 0.5로 두고 선후 관계를 단정하지 않는다.

### 7.2 간선 점수

각 후보 간선의 원시 점수는 다음 가중합으로 계산하고 0~1로 정규화한다.

| 요소 | 가중치 | 계산 |
|------|--------|------|
| 직접 인용 관계 | 0.40 | 실제 `referenced_works` 관계이면 1, 아니면 0 |
| Topic 유사도 | 0.25 | Topic ID의 가중 Jaccard 유사도 |
| Keyword 유사도 | 0.15 | 정규화 키워드의 가중 겹침 |
| 목표 주제 관련도 | 0.15 | `target_query`가 있을 때 검색 관련도 또는 일치 용어 비율 |
| 시간 방향 일관성 | 0.05 | 선택 방향과 발행 연도가 일치하면 1, 연도 누락 시 0.5 |

`target_query`가 없으면 목표 주제 가중치 0.15를 Topic과 Keyword에 2:1로 재분배한다. 실제 인용 간선은 최소 점수 0.40을 보장하지만, 관계 유형 자체가 연구 내용의 확장이나 동의를 의미하지는 않는다.

### 7.3 경로 선택

- 시작 노드부터 길이 2~`max_path_length`의 단순 경로를 탐색한다.
- 경로 점수는 간선 점수의 기하평균에서 길이 페널티와 반복 주제 페널티를 뺀 값이다.
- `target_query`가 있으면 마지막 노드의 목표 관련도를 추가 보너스로 반영한다.
- 최소 간선 점수 0.25 미만의 관계는 경로에서 제외한다.
- 최고 점수가 같은 경우 직접 인용 간선 수, 목표 관련도, 최신 또는 방향상 적합한 연도, ID 오름차순으로 결정한다.
- 최종 점수가 0.35 미만이면 근거가 부족한 것으로 보고 `path: null`을 반환한다.
- 단계별 가지치기를 사용하므로 전체 OpenAlex 그래프의 전역 최적 경로를 보장하지 않는다. 이 한계를 `methodology.limitations`와 사용자용 경고에 포함한다.
- 첫 버전은 최고 점수 경로 하나만 반환하되 1위와 2위 후보 경로의 점수 차이인 `score_margin`을 결과와 디버그 통계에 기록한다. 비교할 두 번째 경로가 없으면 `null`이다. 점수 차이가 0.05 미만이면 대안 경로가 비슷한 신뢰도를 가진다는 경고를 추가한다.

### 7.4 설명 생성

설명은 템플릿 기반으로 생성하며 LLM을 호출하지 않는다.

예시:

- 직접 인용: “2022년 논문 B가 2019년 논문 A를 참고문헌으로 인용합니다. 공통 Topic은 X입니다.”
- 관련 논문: “OpenAlex가 두 논문을 관련 연구로 연결하며, 공통 Topic X와 키워드 Y가 있습니다. 이 관계는 추론입니다.”
- 연도 없음: “발행 연도가 없어 시간 순서를 확인할 수 없습니다.”

## 8. 캐시, 설정 및 로깅

### 8.1 환경변수

| 변수 | 필수 | 기본값 | 설명 |
|------|------|--------|------|
| `OPENALEX_API_KEY` | 아니요 | 없음 | OpenAlex API 키 |
| `OPENALEX_BASE_URL` | 아니요 | `https://api.openalex.org` | 제공자 기본 URL |
| `REQUEST_TIMEOUT_MS` | 아니요 | `5000` | 외부 요청 타임아웃 |
| `CACHE_TTL_SECONDS` | 아니요 | `3600` | 성공 응답 캐시 수명 |
| `CACHE_MAX_ENTRIES` | 아니요 | `500` | 최대 캐시 항목 수 |
| `LOG_LEVEL` | 아니요 | `info` | stderr 로그 수준 |
| `MAX_OPENALEX_CREDITS_PER_TOOL` | 아니요 | `100` | 도구 호출당 최대 크레딧 |
| `MAX_ESTIMATED_COST_USD` | 아니요 | `0.01` | 도구 호출당 예상 비용 상한 |
| `SCORING_VERSION` | 아니요 | `1.0.0` | 점수 알고리즘·캐시 버전 |

### 8.2 캐시 규칙

- 정규화 URL과 정렬된 검색 매개변수를 캐시 키로 사용한다.
- 캐시 키에 `schema_version`, `scoring_version`, 제공자 이름을 포함한다.
- 성공 응답은 기본 1시간, 찾을 수 없음은 5분 캐시한다.
- 인증 오류, 요청 한도 오류, 타임아웃 및 5xx는 캐시하지 않는다.
- 캐시는 프로세스가 종료되면 사라진다.
- API 키는 캐시 키, 로그 및 오류에 포함하지 않는다.

### 8.3 로깅 규칙

- `stdio`의 stdout은 MCP 프로토콜 전용이므로 모든 로그는 stderr에 기록한다.
- 기록 항목: 요청 ID, 도구명, 처리 시간, 캐시 적중 여부, 외부 요청 수, 결과 상태.
- 기록 금지: API 키, 전체 환경변수, 전체 초록, 내부 스택의 외부 응답 포함 부분.
- 사용자의 검색어는 기본 로그에 기록하지 않는다. 디버그 진단이 필요하면 길이와 비가역 해시만 기록한다.

## 9. 보안 및 신뢰성

- 모든 문자열 길이와 숫자 범위를 Zod로 검증한다.
- OpenAlex 기본 URL은 시작 시 HTTPS인지 검증한다. 테스트 환경에서만 명시적으로 HTTP를 허용한다.
- 사용자가 제공한 URL을 임의로 요청하지 않고, 지원되는 DOI·OpenAlex 식별자로 정규화한 후 고정된 제공자 API만 호출한다.
- 응답은 런타임 스키마로 검증해 예기치 않은 제공자 변경을 감지한다.
- `AbortController`로 타임아웃과 상위 요청 취소를 전파한다.
- 원격 배포 단계에서는 Origin·Host 검증, 인증, 요청 크기 제한, 속도 제한을 별도 적용한다.
- 논문이 철회된 경우 결과에 명확한 경고를 포함한다.
- 학술적 사실과 관계는 원문에서 확인해야 한다는 안내를 README와 도구 설명에 포함한다.
- 사용자의 논문 식별자와 검색어가 OpenAlex로 전송된다는 사실을 README에 명시하고, 미공개 연구 주제처럼 민감한 검색어를 보내지 않도록 안내한다.
- 로컬 서버는 검색어를 영구 저장하지 않는다. 원격 배포 시에는 접근 로그와 관찰 도구의 본문 수집을 기본 비활성화한다.

### 9.1 응답 크기 제한

- 저자 최대 10명
- Topic 최대 3개
- Keyword 최대 10개
- 간선당 Evidence 최대 5개
- 경고 최대 20개
- 사람이 읽는 `content`는 기본 20,000자 이하
- 제한으로 생략된 항목 수를 `warnings`에 기록

## 10. 테스트 설계

### 10.1 단위 테스트

| 대상 | 핵심 사례 |
|------|-----------|
| 식별자 정규화 | OpenAlex ID·URL, DOI·URL, 공백, 잘못된 URL |
| OpenAlex 정규화 | 누락 필드, 저자 10명 초과, 초록 역색인, 철회 논문 |
| 유사도 | 동일·부분·무관 Topic과 Keyword, 빈 집합 |
| 점수 계산 | 인용 관계, 목표 검색어 유무, 연도 누락, 동률 |
| 경로 선택 | 순환 제거, 최대 길이, 최저 점수, 결정론적 동률 처리 |
| 관계 분류 | 실제 인용과 추론 관계의 `inferred` 값 |
| 캐시 | 적중, 만료, LRU 제거, 오류 미저장 |
| 설정 | 기본값, 범위 오류, 비밀값 비노출 |
| 논문 중복·버전 | 동일 DOI, DOI 없는 잠재 중복, 사전출판본·출판본 |
| 날짜 | 정확한 날짜, 연도만 존재, 동일 날짜, 날짜 누락 |
| 응답 제한 | 저자·키워드·근거·문자 수 잘림과 경고 |

### 10.2 통합 테스트

모의 OpenAlex 서버 또는 `fetch` 모킹을 사용하며 실제 네트워크에 의존하지 않는다.

- 제목 검색 → 후보 반환
- 정확한 DOI → 단일 논문 확인
- 모호한 제목 → `ambiguous`
- 인용·참고문헌·관련 논문 → 3개 이상 노드 경로
- 429 → `Retry-After` 적용 후 성공 및 재시도 소진
- 타임아웃 → 부분 경로 또는 `PROVIDER_TIMEOUT`
- 잘못된 JSON → `PROVIDER_RESPONSE_INVALID`
- 요청 예산 초과 → `truncated=true`
- 크레딧·비용 예산 초과 → 추가 요청 중단 및 부분 결과
- 한국어 `query`와 명시적 `query_en` → 영어 검색어 우선 사용

### 10.3 MCP 계약 테스트

- 도구 목록에 세 도구와 설명이 노출된다.
- 잘못된 입력이 핸들러 실행 전에 거부된다.
- 각 도구가 `content`와 `structuredContent`를 반환한다.
- MCP 인메모리 또는 stdio 테스트 클라이언트가 도구를 호출하고 종료할 수 있다.
- stdout에 로그가 섞이지 않는다.

### 10.4 선택적 라이브 점검

환경변수로 명시적으로 활성화할 때만 OpenAlex 실서비스를 호출한다.

- 알려진 DOI 1건 확인
- 일반 연구 주제 1건 검색
- 시작 논문 1건의 짧은 경로 생성

라이브 점검은 데이터 변동 때문에 정확한 결과 순서를 단언하지 않고 스키마, 응답 존재 및 출처 링크만 확인한다.

### 10.5 품질 평가 데이터셋

- 최소 5개 연구 분야를 선택한다.
- 분야별로 시작 논문, 목표 주제, 기대 논문과 선정 근거를 기록한다.
- 기대 논문은 알고리즘 결과를 보기 전에 고정한다.
- 가능하면 두 평가자가 경로 관련성·설명 가능성을 독립적으로 평가하고 불일치를 기록한다.
- 깊이 1·2·3, 후보 4·6·8·10, 경로 길이 3·4·5를 비교한다.
- 관련 논문 포함률, 직접 인용 간선 비율, p50·p95 응답시간, 비용, `truncated` 비율을 보고한다.

## 11. 요구사항 추적표

| Plan 요구사항 | 설계 대응 | 검증 |
|---------------|-----------|------|
| FR-1 상태 및 기능 탐색 | `server.ts`, 세 MCP 도구, stdio | MCP 도구 목록 계약 테스트 |
| FR-2 논문 확인 | `resolve_paper`, 식별자 정규화 | 정확·모호·없음 통합 테스트 |
| FR-3 개념 검색 | `search_papers` | 키워드·의미 검색 통합 테스트 |
| FR-4 경로 생성 | `trace_concept_path`, 제한된 그래프 탐색 | 3개 이상 노드 경로 테스트 |
| FR-5 설명 가능한 관계 | `PathEdge`, 템플릿 설명 | 관계·근거·추론 단위 테스트 |
| FR-6 출처 근거 | `PaperSummary`, `Evidence` | 누락 필드 및 출처 계약 테스트 |
| FR-7 오류 및 부분 결과 | 오류 코드, `warnings`, `truncated` | 429·타임아웃·빈 결과 테스트 |
| FR-8 설정 | `config.ts`, `.env.example` | 설정 검증 및 비밀값 검사 |

## 12. 구현 순서

1. `package.json`, TypeScript, Vitest, 포맷·린트 기본 설정
2. 도메인 모델과 Zod 스키마
3. OpenAlex 제공자 인터페이스와 응답 픽스처
4. OpenAlex 어댑터, 타임아웃, 재시도 및 캐시
5. 논문 검색·확인 서비스
6. 유사도, 간선 점수 및 경로 선택 알고리즘
7. 세 MCP 도구와 stdio 진입점
8. 단위·통합·MCP 계약 테스트
9. README, `.env.example`, MCP 클라이언트 설정 예시
10. 로컬 Inspector 검증 및 Design 대비 Gap 분석

의존성은 정확한 버전과 lockfile로 고정한다. CI 또는 로컬 검증에서 타입 검사, 테스트, `npm audit` 및 라이선스 확인을 실행한다. 자동 업데이트 도구 도입 여부는 첫 구현 완료 후 결정한다.

## 13. 배포 설계

### 13.1 첫 번째 완료 기준: 로컬 배포

- `npm ci && npm run build && npm test`가 성공한다.
- MCP 클라이언트가 빌드된 stdio 진입점을 실행한다.
- OpenAlex API 키는 클라이언트의 MCP 서버 환경 설정으로 전달한다.
- 운영 로그는 stderr만 사용한다.

### 13.2 후속 목표: 원격 배포

- `createServer()` 팩토리와 애플리케이션 서비스를 재사용한다.
- Streamable HTTP `/mcp` 엔드포인트를 추가한다.
- 첫 원격 버전은 상태 비저장 방식으로 구성한다.
- Bearer 인증, Origin·Host 검증, 사용자별 속도 제한 및 배포 플랫폼 비밀값을 적용한다.
- 원격 배포는 로컬 버전의 Check 단계 통과 후 별도 작업으로 진행한다.

## 14. 제외 및 향후 확장

첫 버전에서는 다음을 구현하지 않는다.

- PDF 전문 다운로드, OCR 및 전문 기반 관계 판정
- LLM을 이용한 관계 분류 또는 요약
- 사용자 계정과 영구 데이터베이스
- 그래프 UI
- 여러 학술 제공자 결합

향후 품질 평가 후 Crossref·Semantic Scholar 같은 보조 제공자, 전문 기반 근거, 그래프 시각화 및 원격 다중 사용자 운영을 별도 기능으로 계획할 수 있다.

## 15. 학습 포인트

- MCP 도구 계약과 실제 비즈니스 로직을 분리하면 전송 방식이 바뀌어도 핵심 기능을 재사용할 수 있다.
- 외부 API 응답을 정규화하면 제공자 고유 필드가 전체 코드로 퍼지는 것을 막을 수 있다.
- 인용 관계는 사실 관계지만 논문의 동의·확장·반박을 자동으로 증명하지는 않는다.
- 제한된 그래프 탐색은 성능과 비용뿐 아니라 결과 설명 가능성에도 중요하다.
- 실제 네트워크 테스트와 결정론적인 모의 테스트는 목적이 다르므로 분리해야 한다.

## 16. 공식 참고자료

- 탐색 기본값 근거: `docs/02-design/research/search-boundary-rationale.md`
- OpenAlex 선정 근거: `docs/02-design/research/openalex-selection-rationale.md`
- MCP TypeScript SDK v2: https://ts.sdk.modelcontextprotocol.io/v2/
- MCP 첫 서버 가이드: https://ts.sdk.modelcontextprotocol.io/v2/get-started/first-server
- OpenAlex API 개요: https://developers.openalex.org/api-reference/introduction
- OpenAlex Works 스키마: https://developers.openalex.org/api-reference/works
- OpenAlex Works 단일 조회: https://developers.openalex.org/api-reference/works/get-a-single-work
- OpenAlex 검색 가이드: https://developers.openalex.org/guides/searching
- OpenAlex API 인증: https://help.openalex.org/api/authentication/
