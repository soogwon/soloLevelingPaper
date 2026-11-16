# #395 원격 질문 실행 연결 — A·B·C 계약과 구현 순서

작성일: 2026-11-16. 기준 main `af64094`.
[A 회신](https://github.com/soogwon/soloLevelingPaper/issues/395#issuecomment-6906020695)과
[C 회신](https://github.com/soogwon/soloLevelingPaper/issues/395#issuecomment-6905684503)을 반영한
연결 계약 문서이며 runner 구현 완료·운영 승인을 의미하지 않는다.
DB의 구체적인 저장 형태·공개 wire 필드/enum·시간 제한값은 별도로 확정한다. 기존 설계는
[ADR26](../architecture/26-remote-execution-budget-proposal.md), 구현 경계는
[전송 계약](remote-dispatch-contract.md)과 [읽기 복구](remote-execution-replay.md)를 참조한다.

## 1. 재구현하지 않을 부분과 실제 공백

| 준비된 기반 | 근거 | 연결이 남은 부분 |
|---|---|---|
| 요청 키 멱등 접수·실행 lease·취소 요청/확정 | `PostgresRemoteExecutionStore` (#451) | runner의 장애/제한 종결, lease 갱신·재시작 정책 |
| 호출/토큰 예약·unknown·늦은 정산 | 영속 원장 및 transport (#450/#463) | 신뢰하는 토큰 상한·영속 전송 키·실행 정책 공급 |
| 누적 호출/토큰·생성 시각 기준 경과 시간 검사 | `ExecutionBudget`, `BudgetedExecutionScope` (#464) | 정책의 실행별 영속 고정, 단계 timeout 및 저장 deadline |
| 고정 검색/출처·검사·답변/근거 원자 저장 | `PersistedRemoteAnswerService` (#454/#461) | 종료 상태와 저장 경합을 제어하는 worker 연결 |
| 고정 제한 오류 보존·실제 gate와 저장 연결 회귀 | #472 | 제한 발생 후 실행 상태 종결과 C 공개 응답 |
| 동일 실행 읽기 복구·계정별 화면 모델 | B replay 및 C recovery | 실제 키 영속 보관·MCP 제품 연결·호스트 재접속 |

현재 저장소에는 정책 실제 제한값의 snapshot, 전체 deadline, 종결 사유, lease 갱신 포트가 없다.
아래 합의가 코드에 이미 반영된 것으로 해석하지 않는다. 호출 제한 테스트 통과를 worker 전체
완료로 표시하지 않는다.

## 2. 합의된 원칙 — 유지

- 유일 키는 owner + operation + request key다. 다른 입력/hash는 충돌이며 새 실행으로 대체하지 않는다.
- 같은 논리 실행의 lease·프로세스·호스트가 바뀌어도 소비/unknown·기존 결과는 유지한다.
- host timeout/접속 종료는 서버 취소·미전송·무과금 증명이 아니다. 읽기 복구는 추가 생성/예약을 하지 않는다.
- 전송 전 동의/고지·권한/삭제·실행권·예산을 재검사한다. 이미 전송한 시도의 정산과 게시 권한은 별개다.
- 미검증 주장을 structural_only로 낮춰 통과시키거나 예산 제한을 근거 부족으로 표시하지 않는다.
- 완료 결과는 원자 commit 후에만 반환한다. 삭제/권한 상실 때 최신 context로 fallback하지 않는다.

## 3. A·C 회신을 반영한 연결 계약 — 구현 대기

### 실행별 정책 고정

접수 시 서버가 선택한 정책 버전과 **실제 제한값**(전체 시간·단계별 제한·호출·입출력 토큰 상한)을
검증된 불변 snapshot으로 실행에 연결한다. 재claim·재시작은 저장된 snapshot만 읽으며 현재
설정으로 재해석하지 않는다. B는 이를 기존 `ExecutionBudget`과 단계별 제한에 연결한다.
기존 `ExecutionBudget` 생성자만으로 단계별 timeout이 구현됐다고 보지 않는다.
저장 구조와 읽기 모델은 A가 구현한다. 클라이언트가 수치를 전달하거나 재시작 때 상향하지 않는다.

현재 input hash의 budget_policy_version만으로 실제 제한값 고정이 입증되는 것은 아니다.
정책과 실행의 영속 연결·불일치 거부가 필요하다. 원장의 billing window와 실행 deadline도 구분한다.
정책 버전 소실/불일치 때 기본 예산을 주거나 새 실행을 만들지 않고 전송을 막는다.

`deadline_at`은 **DB 시계의 created_at**을 기준으로 접수 시 한 번 계산한다.
큐 대기도 포함되며 lease 연장·재claim·재시작으로 늘리지 않는다. B의 경과 시간 검사는 DB에
고정된 deadline 검사를 대체하지 않는다. 큐 시간과 처리 시간은 따로 측정한다.
lease_expires_at은 실행권 만료이며 전체 deadline의 대체물이 아니다.
최종 수치·단계 timeout·동기/비동기 전환 기준은 #388/제품 호스트 실측 전 정하지 않는다.

현재 실행 저장소의 공개 메서드에는 lease 갱신이 없다. A가 running·현재 token/generation·
lease 미만료·전체 deadline 미도달·현재 접근/삭제/실행 허용 조건을 같은 트랜잭션에서 검사하는
heartbeat/CAS 포트를 추가한다. 갱신은 lease만 연장하고 전체 deadline은 늘리지 않는다.
갱신 실패/실행권 불명 때 새 전송과 결과 게시를 막고, 진행 중 HTTP의 물리 취소 여부와
늦은 usage 정산은 별도로 취급한다. B가 메모리 heartbeat만으로 저장 권한을 연장하지 않는다.

### 실행 상태·종결 사유·사용량·답변 품질 분리

현재 `StoredExecution`/`ReplayView`에는 별도 종결 사유가 없고, `_stored()`는 unknown 원장을
`budget_state=usage_unknown`으로 덮는다. runner 연결 전에 A가 읽기 모델과 저장을 분리한다.
다음은 의미 계약이며 새로운 공개 DTO 필드나 wire enum을 이 문서로 추가하지 않는다.

| 독립 정보 | 의미와 예시 |
|---|---|
| 실행 state | queued/running/cancel_requested/cancelled/completed/failed 등 서버 확정 상태 |
| terminal_reason | A가 제시한 고정 사유: budget_exhausted, deadline_exceeded, provider_failed, cancelled, scope_revoked, internal_failed |
| 사용량 확인 상태 | 관측된 사용량과 usage unknown을 종결 사유와 독립 보존 |
| 저장 답변 품질·검증 수준 | 기존 answer status·verification_level 보존. 실행 제한의 대체 필드로 사용하지 않음 |

예를 들어 예산 소진 종료와 usage unknown은 함께 표현해야 한다. 이를 `failed + limited`나
`budget_state` 하나에 합치지 않는다. 공개 가능한 내부 사유 목록과 실제 표시 매핑은 C와 확정하며
타인/삭제 대상의 존재를 드러내지 않는다.

### 사건별 처리 — 저장소 구현 및 공개 매핑은 남음

| 사건 | 연결할 처리 | 상태/사용량 구분 |
|---|---|---|
| 전송 전 호출/토큰/시간 제한 | 추가 HTTP 0회, 현재 실행권으로 A 종결 CAS 사용 | budget_exhausted 또는 deadline_exceeded와 usage 상태를 분리. 비용 0은 단정하지 않음 |
| 완료된 호출 뒤 다음 단계 제한 | 받은 사용량 유지, 미검증 초안은 저장하지 않음 | 제한 종결과 이미 받은 usage를 각각 보존. 중간 partial 게시 제외 |
| HTTP timeout·usage 없음 | unknown 예약 유지, 자동 재전송 없음 | 서버가 확정한 종결과 usage unknown을 분리. 정산 불명만으로 state=unknown을 강제하지 않음 |
| 원장/권한 조회 장애 | 추가 전송/새 키 접수 없이 기존 실행 조회로 복구 | 미전송/환급·취소를 추측하지 않음. 종결 가능한 서버 판정은 A 포트 필요 |
| 동의 철회·삭제·운영 전송 중지 | 다음 전송·게시 권한을 다시 검사 | 현재 DISPATCH_NOT_AUTHORIZED만으로 원인을 세분 추정하지 않음 |
| 명시적 취소 | request_cancel로 lease 폐기 → 후속 실행/게시 차단 → 서버에서 취소 종결 | 요청 접수와 취소 확정을 구분. 물리 HTTP 중단/환급은 보장하지 않음 |
| 이미 원자 저장 완료 | 같은 결과만 재생 | 늦은 취소/한도 변경으로 완료 payload를 덮어쓰지 않음. 조회 권한/삭제는 별도 적용 |

일반적인 provider/DB 예외의 본문은 계속 비식별화한다. 고정 내부 오류 보존은 공개 wire 코드
확정과 다르다. A의 원자 승인 거부 사유가 없다면 C는 추후 조회의 추측값을 원래 거부 사유로 표시하지 않는다.
진행 알림 중단·클라이언트 타이머 종료로 failed/cancelled를 확정하지 않는다. 취소 API의
None/false도 성공/완료의 근거가 아니며 현재 실행을 재조회한다.

### 늦은 응답·저장 경합

- **최종 commit 경계에서도 같은 실행 정책의 deadline/lease/tombstone을 판정**한다.
  현재 저장소의 lease 검사는 deadline 검사 완료 증명이 아니다. HTTP timeout만으로 전체 기한을 보장하지 않는다.
- 기한 이후 돌아온 응답의 신뢰 가능한 usage는 기존 attempt에 정산하되 새 생성/보완을 시작하지 않는다.
  늦은 정산은 만료 lease나 완료 게시 권한을 복구하지 않는다.
- 기한 초과 시 아직 commit하지 않은 결과는 completed로 전환하지 않는다.
  완료 저장과 실패/제한 종결은 같은 현재 lease·deadline 조건의 CAS로 경쟁한다.
  DB에서 먼저 확정된 terminal 상태가 최종이며 이후 요청은 덮어쓰지 않는다. 해당 포트는 A 구현 대기다.
- 취소와 저장이 겹치면 commit이 먼저 확정된 결과는 재생하고, lease 폐기가 먼저 확정되면 늦은 저장을 거부한다.
  호스트의 버튼 시각이나 worker 메모리 플래그로 승자를 정하지 않는다.
- **중간 partial snapshot 보존은 이번 runner 필수 경로에서 제외한다.** 후속으로 snapshot ID·
  검증 수준·고정 출처·종결 사유의 원자 저장 계약이 마련된 뒤 노출한다. 메모리 중간 응답이나
  저장 실패 결과는 완료 답변으로 표시하지 않는다. 기존 검사 후 정상 완료 경로로 원자 저장한
  partial 답변의 재생 계약은 유지한다.

## 4. 연결 순서

```text
C 인증 owner·기존 요청 키/동의 참조
  → A 원자 접수 또는 같은 실행 조회
  → A 현재 lease + 실행에 고정한 정책 해석
  → B 검색/생성/보완/검증 (전송마다 기존 영속 gate)
  → A 현재 lease/권한/deadline을 반영한 원자 완료 또는 제한/실패 종결
  → C 같은 실행의 상태·저장 결과 복구
```

구현 순서는 A의 (1) 정책 snapshot·deadline·terminal_reason·독립 usage 상태 migration/읽기 모델,
(2) heartbeat·제한/실패 종결 CAS, (3) B runner 연결·늦은 usage 정산,
(4) C 공개 응답 매핑·실제 키 복구, (5) 임시 PostgreSQL 경합 검증이다.
A의 1~2가 준비되기 전 B는 임시 SQL writer나 메모리 정책 저장소를 제품에 추가하지 않는다.
공개 도구 확대는 #399와 해당 인증 검증 뒤다.

## 5. 후속 경합 검증표 — 미실행 계획

| ID | 검증할 연결 | 기존 증거와 추가할 것 |
|---|---|---|
| POL-01 | 재시작/정책 변경 후 같은 실행의 제한 불변 | 누적 예산 검사는 있음. 불변 정책 참조/불일치 거부는 추가 필요 |
| RUN-01 | 같은 키의 동시 접수·두 worker claim | 저장소 멱등/lease 기반은 있음. 전체 runner의 실행·HTTP 계수를 대조 |
| LIM-01 | 생성 후 의미 검사 예산 소진 | #472의 HTTP 1회/미저장에 더해 최종 실행·예산 상태를 조회로 확인 |
| TIME-01 | 전송 전 기한 초과 | #472의 HTTP 0회에 더해 deadline_exceeded 종결·동일 키 재생 확인 |
| TIME-02 | HTTP 도중 기한 초과·늦은 응답 | usage 1회 정산, 새 전송/늦은 게시 0회, 미완료 payload 비노출 |
| CANCEL-01 | HTTP 도중 취소 | lease 폐기 뒤 새 전송/저장 0회, 늦은 usage 보존, 취소 완료 의미 대조 |
| RACE-01 | 취소/기한 종결과 완료 commit 동시 실행 | 두 DB 연결로 순서를 제어. 결과+근거 전부 저장 또는 전부 거부, terminal 상태 덮어쓰기 없음 |
| LEASE-01 | lease 상실 후 구 worker의 늦은 반환 | usage 정산과 게시 권한 분리, 새 worker의 예약/unknown 초기화 없음 |
| LEASE-02 | heartbeat와 취소/기한 초과 경합 | 현재 running/token만 갱신, 전체 deadline 불변, 갱신 실패 뒤 신규 HTTP 0건 |
| STATE-01 | 예산 소진과 usage unknown 동시 발생 | 실행 state·종결 사유·usage·답변 품질이 서로 덮어쓰이지 않음 |
| PART-01 | 중간 partial 뒤 제한 | 이번 범위에서는 중간 snapshot 게시 0건. 후속 원자 계약 후 보존 검증 추가 |
| REPLAY-01 | 응답 유실·재시작·계정 변경 | 기존 DB 읽기 회귀에 실제 키 영속 복원·호스트 연결 및 추가 실행/attempt 0건 증거 추가 |
| REPLAY-02 | 키 저장 실패/유실·조회 없음/손상/거부 | 자동 새 키/접수/예약/전송 0건. 실제 키 저장소와 연결해 확인 |
| REPLAY-03 | A→B→A·같은 계정 재로그인·동시 재조회 | 이전 조회 세대 응답 폐기, 현재 권한 재검사, 실행/유료 attempt 수 불변 |

표 전체를 새로 실행했다는 뜻이 아니다. A/B의 단위·DB CI와 실제 Auth0/Claude 결과를 구분한다.
최초 구현은 합성 HTTP와 임시 PostgreSQL로 검증하고, 실제 자료/API가 필요한 실측은 범위·상한을
먼저 알린 뒤 별도 승인하에 진행한다. 로그/메트릭에 질문·원문·답변·토큰·업로드 권한 URL을 넣지 않는다.

## 6. 담당과 남은 결정

| 담당 | 합의된 작업과 남은 결정 |
|---|---|
| A | 정책 snapshot·deadline·종결 사유·독립 usage 읽기 모델 및 heartbeat/CAS 구현. 구체적인 schema/port와 migration 호환성 확인 |
| B | A 포트 준비 후 기존 gate·검색·의미 검사·원자 저장을 runner에 연결. 늦은 usage, 예산/취소/완료 경합 회귀 |
| C | state·종결 사유·usage·답변 품질의 공개 매핑. 실제 키 영속 보관, timeout/취소·계정 변경·Desktop 재시작 후 복원 |
| 공동 | #388 실측 후 시간값·동기/비동기 기준 결정. 중간 partial은 이번 제외하고 원자 snapshot 계약은 후속 결정 |

이번 PR은 위 계약 문서와 기존 안내 연결만 반영한다. DB·제품 코드·MCP 입력·운영 설정을
변경하지 않는다. 위 경합표와 실제 호스트 검증은 미실행이며, #395와 ADR25 미결 10은 열린 상태다.

## 7. 이번 문서 변경의 확인

2026-11-16 Windows에서 기존 예산·실행 저장·읽기 복구·C 상태 안내 회귀 **87 passed**.
이는 기존 단위 테스트 재실행 결과이며 위 신규 runner 경합표의 통과 증거가 아니다.

```powershell
python -X utf8 -m pytest tests/unit/test_execution_budget.py tests/unit/test_remote_executions.py tests/unit/test_remote_replay.py tests/unit/mcp/test_remote_recovery.py tests/unit/mcp/test_remote_consent_contract.py -q
```

A·C 회신/현재 API 대조, 상대 문서 링크·공백 검사를 통과했다. 실제 API·PostgreSQL·Desktop
호출은 하지 않았다. 후속 PR은 A 저장 포트가 준비된 뒤 합성 provider와 일회용 DB로 검증한다.
