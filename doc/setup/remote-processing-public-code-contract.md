# #517 B 처리 결과·오류 → A 공개 코드 대응

기준 main: `cb9dd18` (#525/#527 병합). A 계약:
[경계 수용](https://github.com/soogwon/soloLevelingPaper/issues/517#issuecomment-6918805007),
[공개 필드 확정](https://github.com/soogwon/soloLevelingPaper/issues/517#issuecomment-6919200400).

`application/processing_outcomes.py`는 B의 **분류 힌트**만 반환한다. 현재 worker/HTTP에 연결하지 않았다.
job 상태 변경·원장 정산·lease 갱신·재시도·게시·응답 직렬화는 하지 않는다. 오류 예외 문자열,
원문·번역·provider 응답·키·ID는 힌트에 복사하지 않는다. 예외를 문자열화하지 않고 정확한 내부 타입/코드만 받는다.

## B가 지금 분류할 수 있는 것

| 실제 B 결과·코드 | 힌트의 action / error_code / retryable | A가 확인할 것 |
|---|---|---|
| 구조가 완성된 `prepared` | await_publication / null / null | 현재 실행 세대·lease·동의·출처·게시 집합 검증 후 저장·원자 게시. 아직 succeeded 아님 |
| 번역·벡터 누락, 번역 실패/빈 문자열, ID·파싱 리비전·개수 연결 불일치가 있는 `prepared` | stop / processing_failed / null | 상태 문자열만으로 게시 후보로 분류하지 않음 |
| `translation_incomplete` | stop / processing_failed / null | 현재 부분 게시 정책 미확정: 미게시 실패로 유지. 성공 번역은 staging 보존 여부를 A가 결정 |
| `PROCESSING_PDF_MISMATCH`, `PDF_UNREADABLE`, `PDF_ENCRYPTED`, `EMPTY_DOCUMENT`, `LIMIT_EXCEEDED` (모두 PROCESSING_ 접두어) | stop / source_validation_failed / false | source 검증 실패 사유를 안전하게 축약. 새 입력이 필요하며 같은 입력 자동 재시도 금지 |
| `DISPATCH_USAGE_UNKNOWN`, `DISPATCH_RECONCILIATION_UNAVAILABLE` | reconcile_usage / usage_pending / null | 예약 유지·실제 usage 정산 확인. 자동 환급·일반 provider 재시도 금지 |
| `DISPATCH_BLOCKED` | stop / processing_failed / null | 앞선 usage unknown·예산·정산 등 실제 원장 원인을 조회. 이 힌트로 기존 usage_pending을 덮어쓰지 않음 |
| `DISPATCH_NOT_AUTHORIZED`, `DISPATCH_AUTHORIZATION_UNAVAILABLE`, `PROCESSING_NOT_ALLOWED`, `PROCESSING_CHECK_UNAVAILABLE` | stop / processing_failed / null | 동의·취소·tombstone·lease·예산·조회 장애를 내부 코드만으로 구분할 수 없음. A 상태 확인 전 공개 사유 확정 금지 |
| `DISPATCH_*BUDGET_EXCEEDED`, `DISPATCH_RESERVATION_EXCEEDED` | stop / processing_failed / null | 시간·토큰 실행 한도와 실제 비용 예약 부족을 동일시하지 않음. 원장/실행 상태로 공개 사유 결정 |
| 설정·gate·parse/translation/embedding 계약 오류, 알 수 없는 예외·잘못된 타입·코드 | stop / processing_failed / null | 내부 상세 노출 없이 중단. 재시도 가능 여부는 추정하지 않음 |
| 청크 `provider_unavailable`·빈 번역·미번역·언어 불일치 | 결과가 translation_incomplete이므로 processing_failed / null | provider_unavailable은 인증·권한·잘못된 요청도 축약한다. provider_temporarily_unavailable 또는 retryable=true로 추정 금지 |

모든 힌트는 `requires_authoritative_review=true`다. 어느 힌트도 공개 상태나 전송 허가가 아니다.
`retryable=false`는 알려진 입력 거부의 보수적 제한일 뿐이고, true는 이 helper에서 반환하지 않는다.
특히 후속 BLOCKED/일반 오류로 앞선 usage 불명 상태를 지우거나 예약을 환급해서는 안 된다.

### 미정산 usage 우선순위

두 분류 함수는 선택적 내부 인자 `usage_unresolved`를 받는다. **공개 요청 필드가 아니며**
A가 같은 owner/job/attempt의 현재 영속 원장에서 확인한 정보만 공급해야 한다.
사용자 입력·다른 작업의 결과·과거 힌트를 그대로 이 값에 넣지 않는다. 실제 원장 조회 구현은 이번 범위에 없다.

| 입력 | 분류 규칙 |
|---|---|
| `usage_unresolved=True` | 후속 BLOCKED·일반 오류·입력 거부·prepared/부분 결과보다 usage_pending / retryable=null 우선 |
| `False` 또는 미공급 `None` | 정산 완료·전송·게시 권한의 증거가 아님. 기존 분류 힌트만 반환하며 A의 실제 검증은 여전히 필수 |
| 문자열·숫자 등 잘못된 타입 | 게시 후보/재시도 허가로 변환하지 않고 일반 중단. 직접 usage unknown 신호가 있으면 usage_pending 유지 |
| 직접 `DISPATCH_USAGE_UNKNOWN`/정산 실패 신호 | False/None으로 덮어쓰지 않음. 해제·정산 판정은 A의 별도 영속 경로 책임 |

원장 조회 실패를 `False`나 정산 완료로 해석해서는 안 된다. A는 확인이 끝나기 전 저장·게시·재전송을
진행하지 않는다. helper는 원장 상태를 보관·변경하지 않으며, A가 확정한 cancelled 등 공개 상태를
usage_pending으로 덮어쓰는 상태 전환기도 아니다. 취소 상태와 미정산 비용의 보존은 A가 각각 관리한다.

prepared의 추가 검사는 완성 구조·연결·벡터 행 개수/길이만 확인한다. 벡터의 유한성·0벡터·L2·지문
전체 검사 등 기존 `RemoteProcessingStages` 검사는 그대로 필수이며 이 helper로 대체하지 않는다.

## A의 영속 상태·추가 정보가 필요한 공개 코드

| A 공개 코드 | 필요한 신뢰 근거 | B가 임의 추정하지 않는 것 |
|---|---|---|
| storage_consent_required / ai_consent_required | 현재 유효한 해당 동의 검사 결과 | NOT_ALLOWED나 문자열에 consent가 있다는 이유로 분류하지 않음 |
| budget_unavailable | 실제 비용 예약·사용량 한도 판정 | 모든 시간/토큰 한도 오류를 비용 부족으로 바꾸지 않음 |
| provider_temporarily_unavailable | 알려진 usage 정산과 안전한 일시 오류 분류 | 청크의 provider_unavailable만으로 일시성·재전송 안전성을 보장하지 않음 |
| lease_expired | 현재 실행 세대·lease 검사 | checkpoint 거부만으로 lease 만료라 하지 않음 |
| processing_cancelled | A가 확정한 취소 상태 | 일반 예외·checkpoint 거부를 취소 완료로 바꾸지 않음 |
| usage_pending | 원장의 미확정 사용량 상태, 또는 B의 직접 unknown/정산 실패 신호 | 원장 상태 확인 전 해제·환급·자동 재시도하지 않음 |

현재 청크 결과는 provider 실패의 일시성 세부 분류를 보존하지 않는다. 추가로 필요하면 A+B가
안전한 구조화 정보 전달을 합의해야 하며, stderr 로그나 외부 예외 본문에서 복원하지 않는다.

`retryable=true`는 A가 **명시 재시도 경로와 동일 입력·동의·예산 재검사 가능성**을 확인할 때만
결정한다. 명시 API가 없으면 C가 재시도 버튼을 열지 않는다. 같은 job의 새 attempt 계약을 유지하며
이 helper는 job/attempt/request key를 생성·교체하지 않는다.

## phase와 공개 응답 경계

| B checkpoint | 단계 의도 힌트 | 뜻하지 않는 것 |
|---|---|---|
| parse | parsing | 입력 owner/R2 검증 완료의 영속 증거 |
| translation | translating | 동의·예약·전송 승인 |
| embedding | embedding | 모델 준비·임베딩 성공·게시 가능 |
| result | 없음 | publishing/finished/succeeded |
| 알 수 없는 값 | 없음 | input_validation 등으로 대체하지 않음 |

B checkpoint는 단계 진입 전의 협력적 검사다. A는 해당 실행 세대의 phase를 조건부로 저장하고,
공개 running 응답은 **영속 checkpoint**에서만 읽는다. 이 함수의 None은 wire의 phase=null을 뜻하지 않는다.
알 수 없는 phase는 저장·공개를 거부한다. 이 helper는 queued/input_validation/publishing/finished를 생성하지 않는다.

A 공개 응답은 phase/error_code/retryable/published_revision_id/searchable 키를 항상 포함한다.
queued/running은 error_code·retryable·publication=null, searchable=false;
cancelled는 finished/processing_cancelled/false/publication=null/searchable=false다.
failed는 finished와 안전한 error_code가 필요하다. succeeded도 원자 게시 뒤에만 기록하고
published_revision_id는 **publication_id**이며 parse/translation revision ID가 아니다.
searchable은 현재 owner·삭제 상태·active publication 기준 새 context 생성 가능성이다.
기존 context의 고정 publication·근거는 교체하지 않는다. 이 응답 모델·조회 코드는 A 영역으로 남긴다.

## 합성 검증과 한계

테스트는 새 helper의 오류 타입/코드 오인·본문 유출·재시도 허가·게시 성공 오인을 검사하며,
기존 실제 파서/번역 gate를 합성 HTTP·메모리 원장·임베더에 연결한 흐름과도 대조한다.
네트워크와 모델 import를 차단하고 원본 DB/PDF/벡터에는 접근하지 않는다.

```powershell
python -X utf8 -m pytest tests/unit/test_processing_outcomes.py tests/unit/test_remote_processing_stages.py -q --basetemp=runtime/pytest-517-contract
```

실제 PostgreSQL/R2·큐 claim·lease/철회 경합·late usage·조건부 저장·원자 게시·공개 HTTP 응답·staging 종단
검증은 아니다. A 연결 뒤 이 매핑과 영속 상태의 우선순위, C의 조회·사용자 안내를 함께 검증해야 한다.
#517은 열린 상태로 유지한다.

### Windows 오프라인 결과 — 2026-11-16

- 최초 신규 분류 계약 77개 / 처리 단계 포함 166개가 통과했다. 아래 수치는 재검토 보완 후의 전체 결과다.
- 신규 분류 계약 **133개**, 기존 처리 단계 포함 **222 passed**.
- 파싱·청킹·번역·전송 gate·실행 예산 회귀 포함 **427 passed, 13 subtests passed**.
- 합성 HTTP 503/timeout·usage 누락·정산 오류는 usage_pending으로 분류하며, 후속 청크/같은 transport 재실행에서 추가 전송이 없는 것을 확인했다.
- 동의/권한 확인 실패·checkpoint 거부는 세부 원인을 추정하지 않고 중단하며 전송이 없는 것을 확인했다.
- prepared의 반복 분류가 추가 전송·원장 변경·원본 처리 결과 변경을 일으키지 않으며, publication/searchable/succeeded를 생성하지 않음을 확인했다.
- 재검토에서 번역·벡터가 없는 prepared가 게시 후보로 분류되고, 후속 BLOCKED를 단독 분류하면 일반 실패로 바뀌는 것을 재현했다. 미정산 입력의 우선순위·잘못된 타입·결과 구조 거부 회귀를 추가해 보완했다.
- 보완 후에도 기존 오류·부분 번역 분류와 정상 결과는 유지하며 Ruff·공백 검사를 통과했다.
- 실제 API·모델·원본 데이터 접근, 큐·DB·게시·HTTP 구현 변경은 없다.
