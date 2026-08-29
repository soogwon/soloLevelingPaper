# Paper Concept Path MCP

OpenAlex 메타데이터를 이용해 연구 논문을 검색하고, 인용 관계·Topic·Keyword·발행 시점을 근거로 제한된 논문 경로를 생성하는 로컬 MCP 서버입니다.

> 이 서버의 결과는 문헌 탐색을 돕기 위한 후보입니다. 학술적 사실, 인용의 의미 및 논문 간 관계는 반드시 연결된 원문에서 확인하세요.

## 주요 기능

- 연구 주제에 맞는 논문 후보 검색
- DOI, OpenAlex ID·URL 또는 제목으로 논문 확인
- 실제 인용과 추론 관계를 구분한 논문 경로 생성
- 각 경로 간선의 점수, 근거, 추론 여부 제공
- 요청 수·OpenAlex 크레딧·예상 비용·응답시간 제한
- 메모리 TTL/LRU 캐시
- 조회 시각, 점수 버전, 설정값 등 재현성 정보

## 제공 도구

| 도구 | 설명 |
|------|------|
| `search_papers` | 주제 검색으로 시작 논문 후보를 찾습니다. |
| `resolve_paper` | DOI, OpenAlex ID·URL 또는 제목을 논문 레코드로 확인합니다. |
| `trace_concept_path` | 시작 논문에서 제한된 설명 가능 경로를 생성합니다. |

## 요구사항

- Node.js 20 이상
- npm
- 선택 사항: 무료 OpenAlex API 키

OpenAlex는 키 없이도 소규모 요청을 허용하지만 사용 예산이 작습니다. 실제 사용에는 무료 API 키를 권장합니다.

## 설치 및 검증

```powershell
npm ci
npm run check
```

`npm run check`는 타입 검사, 13개 자동 테스트 및 빌드를 순서대로 실행합니다.

개발 모드:

```powershell
npm run dev
```

빌드 결과 실행:

```powershell
npm run build
npm start
```

`stdio` 서버는 MCP 클라이언트가 입력을 보낼 때까지 기다립니다. stdout은 MCP 프로토콜 전용이고 로그는 stderr에 기록됩니다.

## MCP Inspector로 확인

프로젝트 루트에서 다음 명령을 실행합니다.

```powershell
npx @modelcontextprotocol/inspector node dist/index.js
```

브라우저에서 연결한 뒤 Tools 탭에서 `search_papers`, `resolve_paper`, `trace_concept_path`를 호출할 수 있습니다.

## MCP 호스트 등록 예시

다음은 `command`, `args`, `env` 형식을 지원하는 MCP 호스트의 일반적인 설정 예시입니다. 경로는 실제 절대 경로로 변경하세요.

```json
{
  "mcpServers": {
    "paper-concept-path": {
      "command": "node",
      "args": [
        "D:\\fd\\soloLeveling\\mini-projects\\soogwon\\dist\\index.js"
      ],
      "env": {
        "OPENALEX_API_KEY": "여기에-키를-입력"
      }
    }
  }
}
```

API 키는 설정 파일 대신 호스트의 비밀값 저장 기능이나 운영체제 환경변수를 사용하는 것이 더 안전합니다. 개인 OpenAlex 키는 로그인 자격 증명 역할도 할 수 있으므로 노출되면 즉시 회전하세요.

## 도구 사용 예시

### 논문 검색

```json
{
  "query": "설명 가능한 인공지능 의료",
  "query_en": "explainable artificial intelligence healthcare",
  "from_year": 2018,
  "limit": 5,
  "semantic": false
}
```

서버는 자동 번역하지 않습니다. 한국어 검색 품질을 높이려면 선택적으로 `query_en`을 함께 제공하세요.

### 논문 확인

```json
{
  "identifier": "10.1038/s42256-019-0048-x"
}
```

제목 검색이 모호하면 서버는 임의로 하나를 선택하지 않고 최대 5개 후보를 반환합니다.

### 개념 경로 생성

```json
{
  "seed": "10.1038/s42256-019-0048-x",
  "target_query": "clinical decision support",
  "direction": "both",
  "max_depth": 2,
  "max_path_length": 4,
  "candidates_per_node": 6
}
```

기본 탐색은 전체 OpenAlex 그래프를 모두 확인하지 않습니다. 단계별 후보를 제한하므로 반환 경로는 탐색된 범위에서의 최선이며 전역 최적 경로가 아닙니다.

## 관계 유형

| 관계 | 의미 | 추론 여부 |
|------|------|-----------|
| `cites` | 현재 논문이 다음 논문을 참고문헌으로 인용 | 확인된 인용 관계 |
| `cited_by` | 다음 논문이 현재 논문을 참고문헌으로 인용 | 확인된 인용 관계 |
| `related` | OpenAlex가 관련 논문으로 연결 | 추론 |
| `topic_similar` | Topic 또는 Keyword가 유사 | 추론 |

인용 사실만으로 논문의 동의, 확장, 반박 또는 적용을 단정하지 않습니다.

## 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `OPENALEX_API_KEY` | 없음 | Authorization 헤더로 전송할 OpenAlex 키 |
| `OPENALEX_BASE_URL` | `https://api.openalex.org` | OpenAlex API 기본 URL |
| `REQUEST_TIMEOUT_MS` | `5000` | 외부 요청 타임아웃 |
| `CACHE_TTL_SECONDS` | `3600` | 성공 응답 캐시 수명 |
| `CACHE_MAX_ENTRIES` | `500` | 최대 캐시 항목 수 |
| `LOG_LEVEL` | `info` | 로그 수준 |
| `MAX_OPENALEX_CREDITS_PER_TOOL` | `100` | 도구 호출당 최대 OpenAlex 크레딧 |
| `MAX_ESTIMATED_COST_USD` | `0.01` | 도구 호출당 예상 비용 상한 |
| `SCORING_VERSION` | `1.0.0` | 점수 알고리즘과 캐시 버전 |

`.env.example`은 설정 참고용입니다. 서버가 `.env` 파일을 자동으로 읽지는 않으므로 운영체제 또는 MCP 호스트 환경 설정으로 값을 전달하세요.

## 개인정보와 보안

- 논문 식별자와 검색어는 OpenAlex로 전송됩니다.
- 서버는 검색어를 영구 저장하지 않습니다.
- 검색어 전체와 API 키를 로그에 기록하지 않습니다.
- API 키는 URL이 아니라 `Authorization: Bearer` 헤더로만 전송합니다.
- 미공개 연구 주제처럼 민감한 검색어는 보내지 마세요.
- 사용자 입력 URL을 임의로 요청하지 않고 고정된 OpenAlex API만 호출합니다.

## 비용과 제한

OpenAlex의 비용과 정책은 변경될 수 있습니다. 현재 공식 자료는 다음에서 확인하세요.

- [비용 예시](https://help.openalex.org/access/example-costs/)
- [요금 개요](https://help.openalex.org/access/pricing/)
- [인증과 요청 제한](https://help.openalex.org/api/authentication/)

서버는 요청 횟수뿐 아니라 크레딧과 예상 비용을 제한합니다. 제한에 도달하면 가능한 경우 `truncated=true`인 부분 결과를 반환합니다.

## 테스트

```powershell
npm test
```

테스트 범위:

- 인용·유사 관계 점수와 근거
- 결정론적 경로 선택과 순환 방지
- TTL/LRU 캐시
- OpenAlex 응답 정규화
- API 키의 Authorization 헤더 전송
- MCP 도구 목록·호출·입력 검증 계약

자동 테스트는 실제 OpenAlex 네트워크에 의존하지 않습니다. 실서비스 데이터는 변경될 수 있으므로 라이브 점검에서는 정확한 검색 순서를 고정하지 않습니다.

## 알려진 한계

- OpenAlex의 인용·Topic·Keyword 데이터가 불완전할 수 있습니다.
- 평문 초록이 없는 논문이 많습니다.
- DOI 논문, 사전출판본, 출판본이 별도 Work로 존재할 수 있습니다.
- 한국어 검색어의 품질은 영어 검색어보다 낮을 수 있습니다.
- 단계별 후보 가지치기로 전체 그래프의 최적 경로를 보장하지 않습니다.
- 첫 버전은 경로 하나만 반환하며 그래프 UI와 PDF 전문 분석은 제공하지 않습니다.

## 관련 설계 문서

- [Plan](docs/01-plan/features/paper-concept-path-mcp.plan.md)
- [Design](docs/02-design/features/paper-concept-path-mcp.design.md)
- [탐색 기본값 근거](docs/02-design/research/search-boundary-rationale.md)
- [OpenAlex 선정 근거](docs/02-design/research/openalex-selection-rationale.md)
