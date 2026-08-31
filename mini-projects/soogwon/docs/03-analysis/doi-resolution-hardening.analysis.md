# Gap Analysis: doi-resolution-hardening

> 작성일: 2026-08-31 | 반복: 2 | Design: `docs/02-design/features/doi-resolution-hardening.design.md`

---

## Match Rate: 100.0%

완전 일치 1점, 부분 일치 0.5점, 미구현 0점으로 계산했다.

```text
(완전 일치 30 + 부분 일치 0 × 0.5) / 전체 30 × 100 = 100.0%
```

1차 분석의 부분 테스트 4개 영역을 보강했다. DOI 입력 분류, 위치 DOI fallback, 보수적인 결과 확정, resolution 응답, 식별자 충돌 경고와 deadline 선검사뿐 아니라 설계한 오류·캐시·크레딧·스키마 경계 조건까지 모두 구현·검증됐다.

## 검증 결과

- `npm run typecheck`: 통과
- 전체 테스트: 7개 파일, 52개 테스트 통과
- `npm run build`: 통과
- `npm audit --audit-level=high`: 취약점 0건
- `git diff --check`: 통과
- 실제 OpenAlex 사전 조사에서 `locations.landing_page_url` 정확 일치 filter가 `W2626778328`을 반환함을 확인
- 자동 테스트는 실제 네트워크 대신 모의 fetch를 사용해 결정론적으로 실행

## 영역별 결과

| 영역 | 전체 | 일치 | 부분 | 미구현 | 환산 점수 |
|------|-----:|-----:|-----:|-------:|----------:|
| 입력 분류·검증 | 5 | 5 | 0 | 0 | 5.0 |
| 제공자 fallback·보호 정책 | 8 | 8 | 0 | 0 | 8.0 |
| 서비스 결과·경고 | 8 | 8 | 0 | 0 | 8.0 |
| MCP 계약·문서 | 4 | 4 | 0 | 0 | 4.0 |
| 테스트 완결성 | 5 | 5 | 0 | 0 | 5.0 |
| **합계** | **30** | **30** | **0** | **0** | **30.0 / 30** |

## Implemented Items

### 입력 분류·검증 — 5/5

- [x] OpenAlex ID와 Work URL을 정확 식별자로 분류한다.
- [x] DOI와 DOI URL을 소문자 `https://doi.org/...` 형태로 정규화한다.
- [x] `10.65215` 및 불완전 DOI URL을 `incomplete_doi`로 분류한다.
- [x] 불완전 DOI는 provider를 호출하지 않고 `INVALID_INPUT`을 반환한다.
- [x] `doi:`와 `dx.doi.org` 접두사, 대소문자 및 불완전 DOI URL을 회귀 테스트로 검증한다.

### 제공자 fallback·보호 정책 — 8/8

- [x] `ScholarlyProvider.findWorksByLocationDoi()` 계약을 추가했다.
- [x] `locations.landing_page_url:<normalized-doi-url>` exact filter를 사용한다.
- [x] 복수 결과 판정을 위해 `per_page=2`로 제한한다.
- [x] 기존 Work `select` 필드 목록을 재사용한다.
- [x] 기존 filter 크레딧·비용·요청 수 및 캐시 경로를 재사용한다.
- [x] 도구 호출의 기존 signal과 `deadlineAt`을 fallback에 전달한다.
- [x] fetch와 사용량 차감 전에 다음 요청 시간이 deadline 안에 있는지 검사한다.
- [x] 위치 조회의 filter 크레딧, 요청 수와 성공 캐시 적중을 직접 검증한다.

### 서비스 결과·경고 — 8/8

- [x] 직접 DOI 조회가 성공하면 fallback을 실행하지 않는다.
- [x] 직접 DOI 조회가 404로 `null`을 반환할 때만 위치 fallback을 실행한다.
- [x] 위치 결과 0건은 `not_found`로 반환한다.
- [x] 위치 결과 1건만 `exact`로 확정한다.
- [x] 위치 결과 2건은 `ambiguous`와 최대 2개 후보로 반환한다.
- [x] exact 결과에 `PaperResolution`을 추가한다.
- [x] 위치 확인 시 `DOI_RESOLVED_VIA_LOCATION` 경고를 추가한다.
- [x] 입력 DOI와 대표 DOI가 다르거나 대표 DOI가 없으면 `IDENTIFIER_CONFLICT`와 두 값을 제공한다.

### MCP 계약·문서 — 4/4

- [x] `resolution`과 `matched_via` enum을 출력 스키마에 추가했다.
- [x] 내부 camelCase 값을 공개 snake_case JSON으로 변환한다.
- [x] README에 완전한 DOI 형식, fallback과 충돌 경고를 설명했다.
- [x] `matched_via` 네 값과 `provider_primary_doi` 필드, 대표 DOI `null` 응답을 검증한다.

### 테스트 — 5/5

- [x] 불완전 DOI가 provider 호출 없이 거부되는지 검증한다.
- [x] 직접 DOI 성공 시 fallback 미실행을 검증한다.
- [x] 위치 DOI 1건·복수 건과 식별자 충돌 경고를 검증한다.
- [x] filter URL, `per_page=2`, `select` 및 deadline 선중단을 검증한다.
- [x] 직접 조회의 인증·rate limit·timeout·잘못된 응답에서 fallback이 실행되지 않음을 검증한다.

## Partial Items

없음. 1차 분석의 P-1~P-4를 모두 회귀 테스트로 보강했다.

## Missing Items

없음. 설계한 런타임 기능은 모두 구현됐다.

## Changed Items (Deviations from Design)

- [x] 별도 `ProviderResolutionResult` 타입 대신 `findWorksByLocationDoi()`를 제공자 계약에 추가하고 `PaperService`가 resolution 문맥을 구성했다. 설계 문서와 일치한다.
- [x] `IDENTIFIER_CONFLICT`는 대표 DOI가 `null`인 경우에도 입력 DOI와 다르다고 판단한다. 정보 누락을 숨기지 않는 보수적인 동작이다.
- [x] deadline 선검사를 위치 fallback 전용이 아니라 공통 `#request`의 모든 외부 요청에 적용해 동일 정책을 보장했다.

## 위험 재검토

| 위험 | 현재 대응 | 잔여 위험 |
|------|-----------|-----------|
| 잘못 병합된 OpenAlex Work | 단일 exact 결과만 확정하고 식별자 충돌 경고 | OpenAlex 자체의 병합 정확성은 보장할 수 없음 |
| 대표 DOI 오해 | requested·normalized·provider DOI와 matched_via 공개 | 사용자가 원문을 확인해야 함 |
| fallback 비용 증가 | 직접 404에서 한 번, filter 비용·deadline 공유 | 위치 조회 한 번의 추가 비용 존재 |
| 불완전 DOI 오검색 | 제목 검색 전에 INVALID_INPUT 반환 | DOI처럼 시작하는 특이한 논문 제목은 오류로 분류될 수 있음 |

## Recommendations

1. 설계 일치율 100.0%로 Report 단계에 진행한다.
2. 실제 Inspector에서 `10.48550/arXiv.1706.03762`를 호출해 `W2626778328`, `location_doi`와 두 경고가 표시되는지 수동 확인한다.
3. OpenAlex 대표 DOI나 발행 연도를 자동 교정하는 기능은 이 주기에 추가하지 않는다.

## Next Steps

- [x] `$pdca iterate doi-resolution-hardening` 완료
- [x] 2차 분석 설계 일치율 100.0% 달성
- [ ] `$pdca report doi-resolution-hardening`으로 완료 보고서 작성
