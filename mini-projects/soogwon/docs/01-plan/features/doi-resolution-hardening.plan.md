# doi-resolution-hardening - 계획 문서

> 버전: 1.0.0 | 작성일: 2026-08-31 | 상태: 완료
> 프로젝트 수준: Starter

---

## 1. 개요

### 1.1 목적

`resolve_paper`가 완전한 DOI를 정확한 OpenAlex Work로 연결하고, 불완전한 DOI나 제공자 메타데이터 충돌을 사용자가 오해하지 않도록 DOI 확인 흐름을 강화한다.

### 1.2 배경

OpenAlex의 최상위 `doi`는 하나의 대표값만 제공하지만 동일 Work의 `locations`에는 사전출판본·저장소·출판사 DOI가 추가로 존재할 수 있다. `Attention Is All You Need`의 arXiv DOI `10.48550/arXiv.1706.03762`는 Work `W2626778328`의 위치에는 존재하지만 단일 DOI 조회에서는 404가 반환된다. 또한 `10.65215`처럼 불완전한 DOI 접두사는 현재 일반 제목 검색으로 처리되어 관련 없는 후보를 `ambiguous`로 반환한다.

## 2. 목표

### 2.1 주요 목표

- [ ] DOI 직접 조회 실패 시 `locations.landing_page_url`의 정확 일치 필터로 한 번 fallback한다.
- [ ] DOI처럼 보이지만 완전하지 않은 입력은 제목 검색으로 넘기지 않고 검증 오류로 처리한다.
- [ ] 입력 DOI와 OpenAlex 대표 DOI가 다르면 입력 식별자와 충돌 사실을 응답에 보존한다.
- [ ] fallback도 기존 요청 수·크레딧·비용·deadline·캐시 정책을 따른다.
- [ ] 정상 DOI, 위치 DOI, 불완전 DOI, 다중·무결과와 식별자 충돌에 대한 회귀 테스트 기준을 충족한다.

### 2.2 목표가 아닌 항목

- OpenAlex의 잘못된 발행 연도나 대표 DOI를 임의로 수정하지 않는다.
- arXiv, Crossref 또는 DataCite를 두 번째 런타임 제공자로 추가하지 않는다.
- DOI 접두사에 속한 전체 논문 목록 검색 기능을 제공하지 않는다.
- 제목만으로 DOI를 추정하거나 첫 번째 검색 결과를 자동 확정하지 않는다.

## 3. 범위

### 3.1 포함 범위

- DOI와 DOI URL의 정규화 및 완전성 검사
- DOI 형태 입력과 일반 논문 제목의 명확한 분류
- OpenAlex 단일 DOI 조회의 404 처리
- 정규화 DOI URL에 대한 `locations.landing_page_url` 정확 일치 fallback
- fallback 결과 0건·1건·복수 건 처리 정책
- 요청 DOI와 제공자 대표 DOI의 불일치 경고
- 공개 응답에서 실제로 일치한 입력 식별자와 확인 경로 표시
- 단위·통합·MCP 계약 회귀 테스트와 README 사용 예시 갱신

### 3.2 제외 범위

- 외부 DOI 등록기관의 진위 또는 출판 윤리 판정
- 논문 버전의 공식성·우선순위를 자동으로 결정하는 기능
- OpenAlex 원본 데이터 수정 요청 자동화
- 여러 학술 데이터 제공자를 이용한 메타데이터 합의 알고리즘

## 4. 기능 요구사항

### FR-1: DOI 입력 분류

완전한 DOI, DOI URL, OpenAlex ID·URL, DOI처럼 보이지만 불완전한 문자열, 일반 제목을 서로 구분해야 한다.

### FR-2: 위치 DOI fallback

완전한 DOI의 단일 조회가 404일 때만 정규화된 DOI URL로 `locations.landing_page_url` 정확 일치 조회를 한 번 수행해야 한다.

### FR-3: 보수적인 결과 확정

fallback 결과가 정확히 한 건일 때만 논문을 확정한다. 결과가 없으면 `not_found`, 여러 건이면 `ambiguous` 또는 식별자 충돌 오류를 반환하고 첫 결과를 임의 선택하지 않는다.

### FR-4: 식별자 출처 공개

입력 DOI가 위치에서 확인됐거나 OpenAlex 대표 DOI와 다르면 구조화된 경고에 요청 DOI, 제공자 DOI 및 확인 방식을 기록해야 한다.

### FR-5: 기존 보호 정책 유지

추가 요청은 도구의 10초 deadline, 요청 20회, 크레딧·예상 비용 제한과 캐시에 포함되어야 한다.

## 5. 성공 기준

- [ ] `10.48550/arXiv.1706.03762`가 위치 DOI fallback으로 `W2626778328`에 연결된다.
- [ ] `10.65215/2q58a426`처럼 직접 조회되는 DOI는 fallback 없이 처리된다.
- [ ] `10.65215`는 제목 검색 후보 대신 조치 가능한 입력 오류를 반환한다.
- [ ] 위치 DOI 결과가 여러 건이면 어느 논문도 임의로 확정하지 않는다.
- [ ] 요청 DOI와 OpenAlex 대표 DOI가 다르면 식별자 충돌 경고가 반환된다.
- [ ] fallback 요청이 deadline이나 비용 한도를 넘으면 기존 공개 오류 정책을 따른다.
- [ ] 기존 테스트와 신규 회귀 테스트, 타입 검사 및 빌드가 모두 통과한다.
- [ ] README에 DOI 직접 조회, 위치 fallback 및 불완전 DOI 예시가 반영된다.

## 6. 일정

| 단계 | 목표일 | 상태 |
|------|--------|------|
| Plan | 2026-08-31 | 완료 |
| Design | 다음 작업 | 대기 |
| 구현 | Design 승인 후 | 대기 |
| Check·Report | 구현·회귀 검증 후 | 대기 |

## 7. 위험 및 대응

| 위험 | 영향 | 가능성 | 대응 |
|------|------|--------|------|
| OpenAlex가 여러 위치 DOI를 한 Work에 잘못 병합 | 높음 | 중간 | exact filter와 단일 결과만 허용하고 충돌 경고를 반환한다. |
| 위치 DOI가 대표 DOI와 다름 | 높음 | 높음 | 요청 DOI와 대표 DOI를 모두 노출하고 공식성을 단정하지 않는다. |
| fallback이 요청 비용과 시간을 증가시킴 | 중간 | 중간 | 직접 조회 404에서만 최대 한 번 실행하고 기존 예산을 공유한다. |
| 불완전 DOI와 숫자가 포함된 논문 제목 혼동 | 중간 | 낮음 | DOI 접두사 패턴을 좁게 정의하고 일반 제목 테스트를 추가한다. |
| OpenAlex 필터 문법 변경 | 중간 | 낮음 | 제공자 어댑터에 격리하고 응답 스키마·회귀 테스트로 감지한다. |

## 8. 설계 단계 결정 사항

1. 공개 응답에 `matched_identifier`를 추가할지 경고 `details`만 사용할지 결정한다.
2. 복수 fallback 결과를 기존 `ambiguous` 후보 구조로 반환할지 공개 오류로 처리할지 결정한다.
3. DOI 불완전 입력을 Zod 입력 검증에서 거부할지 서비스 계층 오류로 변환할지 결정한다.
4. DOI alias를 내부 `PaperDetail`에 저장할지 요청 문맥에서만 유지할지 결정한다.

## 9. 참고자료

- 기존 설계: `docs/02-design/features/paper-concept-path-mcp.design.md`
- 기존 완료 보고서: `docs/04-report/paper-concept-path-mcp.report.md`
- OpenAlex Work 확인: `https://api.openalex.org/works/W2626778328`
- OpenAlex 위치 DOI 정확 일치 필터: `locations.landing_page_url`
- arXiv 원본: `https://arxiv.org/abs/1706.03762`
- Crossref 별도 DOI 기록: `https://api.crossref.org/works/10.65215%2F2q58a426`
