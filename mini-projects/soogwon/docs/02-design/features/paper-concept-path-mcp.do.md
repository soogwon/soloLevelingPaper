# paper-concept-path-mcp - 구현 기록

> 작성일: 2026-08-28 | PDCA 단계: Do
> 기준 설계: `docs/02-design/features/paper-concept-path-mcp.design.md`

## 1. 구현 개요

TypeScript와 MCP TypeScript SDK v2를 사용해 OpenAlex 기반 로컬 `stdio` MCP 서버를 구현했다. 논문 검색·확인·경로 생성을 서로 분리하고, OpenAlex 고유 형식은 제공자 어댑터에서 내부 모델로 정규화한다.

## 2. 구현 항목

### 프로젝트 기반

- [x] Node.js 20 이상, ESM, TypeScript strict 설정
- [x] MCP SDK·Zod·Vitest 정확한 버전 고정
- [x] package-lock.json 생성
- [x] 빌드·타입 검사·테스트 명령 구성
- [x] `node_modules`, `dist`, `.env`, coverage Git 제외

### 데이터 및 제공자

- [x] 정규화 논문·Topic·Keyword·경로·근거 모델
- [x] OpenAlex Work 런타임 응답 검증
- [x] DOI·OpenAlex ID·URL 정규화
- [x] 역색인 초록 복원
- [x] 논문 버전 그룹 키
- [x] Authorization 헤더 기반 API 키 전달
- [x] 타임아웃, 제한된 재시도, 429 처리
- [x] 요청 수·크레딧·예상 비용 추적
- [x] TTL/LRU 메모리 캐시

### 비즈니스 로직

- [x] 제목 검색과 모호성 처리
- [x] 인용·Topic·Keyword·목표·시간 가중치
- [x] 실제 인용과 추론 관계 구분
- [x] 최대 깊이·노드·경로·요청·시간 제한
- [x] 결정론적 경로 선택과 순환 방지
- [x] 1·2위 경로 점수 차이와 불확실성 경고
- [x] 부분 결과와 탐색 한계 경고
- [x] 조회 시각·점수 버전·제한값 재현성 정보

### MCP 통합

- [x] `search_papers`
- [x] `resolve_paper`
- [x] `trace_concept_path`
- [x] Zod 입력 검증
- [x] 사람이 읽는 `content`와 기계용 `structuredContent`
- [x] stdio 진입점과 안전한 stderr 로그

### 테스트와 문서

- [x] 점수 계산 단위 테스트
- [x] 경로 선택 단위 테스트
- [x] 캐시 단위 테스트
- [x] OpenAlex 어댑터 통합 테스트
- [x] MCP 인메모리 계약 테스트
- [x] README와 `.env.example`
- [ ] MCP Inspector 수동 확인
- [x] 키 없이 OpenAlex 실서비스 단일 논문 조회
- [ ] OpenAlex API 키를 사용한 라이브 경로 품질 평가

## 3. 자동 검증 결과

```text
npm run typecheck : 통과
npm test          : 5개 파일, 13개 테스트 통과
npm run build     : 통과
OpenAlex 라이브 단일 조회: W2741809807 정상 반환, 1회 요청·0크레딧
```

## 4. 구현 중 조정 사항

- MCP SDK v2의 `serveStdio`는 요청 컨텍스트를 받는 서버 팩토리를 요구하므로 `() => createServer()`로 연결했다.
- SDK v2의 잘못된 도구 입력은 클라이언트 예외가 아니라 `isError=true` 결과로 검증했다.
- 숨김 bkit 소스가 Vitest에 포함되지 않도록 테스트 범위를 `tests/**/*.test.ts`로 제한했다.

## 5. 다음 품질 단계

1. OpenAlex 라이브 단일 조회 점검
2. 설계 대비 구현 Gap 분석
3. 누락 항목 수정
4. 대표 분야 기준 데이터셋으로 검색 품질 평가
5. 로컬 MCP Inspector 확인
