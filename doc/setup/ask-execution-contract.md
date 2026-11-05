# ask 실행 기록 계약 (저장소 쪽, #119)

Desktop의 대기 시간이 끝나도 서버는 작업을 계속할 수 있고(취소 알림이 오지 않음), 같은 질문을 다시 보내면 겹쳐 실행된다.
이 저장소는 요청 키마다 실행 기록 한 행을 두고 **하나의 실행만 진행**하도록 한다. 초기값은 팀이 합의한 값이다.

| 항목 | 값 |
|---|---|
| 하트비트 간격(권장) | 10초 |
| 실행권 임대 | 60초(마지막 하트비트 이후) |
| 결과 보존 | 7일 |
| 요청 키 기록 보존 | 30일 |

## 규칙
- **단일 실행권**: 같은 키의 `begin_execution`은 처음 한 번만 `STARTED`와 소유자 토큰을 준다. 이후 호출은 토큰 없이 현재 상태만 알려 준다.
- **자동 재시작 금지**: 임대가 지난 실행도 새 소유자에게 넘기지 않는다. 같은 키로는 새 행도 새 토큰도 만들지 않는다.
  새 실행은 호출자가 **새 키**로 명시적으로 요청한다. 키와 context만으로 원래 질문을 재구성하지 않는다.
- **입력 충돌**: 같은 키에 다른 입력 해시가 오면 `RequestConflictError`(상태를 보기 전에 확인).
- **같은 키의 재사용 조건**: 저장된 `context_id`와 입력 해시가 모두 같을 때만 기존 실행을 돌려준다. 하나라도 다르면 상태·본문을 읽기 전에 `RequestConflictError`로 거부하고 행은 바뀌지 않는다.
- **판단 시각**: 임대 만료는 쓰기 잠금을 얻은 직후의 시각으로 판단한다. 다른 연결이 잠금을 잡고 있는 동안 임대 경계를 넘으면 하트비트·결과 저장·실패 기록 모두 `LEASE_LOST`이고 행은 바뀌지 않는다(`now`를 주입한 경우는 그 값).
- **소유자 토큰 형식**: 서버가 발급한 형식(소문자 16진수 32자)이 아닌 값(한글 등 포함)은 소유자가 아니므로 `LEASE_LOST`이며 예외가 나지 않고 행은 바뀌지 않는다.
- **만료 거부**: `now < lease_expires_at`일 때만 하트비트·결과 저장·실패 기록이 된다. 지난 뒤에는 원래 소유자라도 `LEASE_LOST`이고
  아무것도 바꾸지 않는다. 토큰이 다르거나 이미 끝난 실행도 `LEASE_LOST`다.
- **읽기 전용 조회**: `lookup_execution`은 행을 바꾸지 않고, 실행을 시작·재시작하지 않는다.
  `context_id`는 필수 인자다(생략하면 `TypeError`, 비어 있으면 `ExecutionValidationError`). 저장된 context와 다르면 없는 키와 같은 `not_found`로,
  다른 context의 상태·본문·존재 여부를 드러내지 않는다. 서비스의 `lookup`도 이 함수에 context를 넘긴다.
- **답변·근거·완료의 저장 단위**: `complete_execution(..., evidences=, history=)` 하나가 임대 확인, 근거 저장, 이력 저장, 완료 상태와 공개 답변을
  쓰기 잠금 하나에서 확정한다. 근거 검증·삽입이나 완료 UPDATE가 실패하면 근거와 완료 상태가 함께 롤백되고, 임대 만료·잘못된 토큰·잠금 대기 중 만료는
  근거를 넣기 전에 `LEASE_LOST`로 돌아온다. 이력 저장만 실패하면 답변은 보존하고 `verification_history.status`를 `failed`로 표시한다.
- **EXPIRED·NOT_FOUND는 자동 재실행의 근거가 아니다.** 결과 7일이 지나면 EXPIRED(본문은 돌려주지 않음), 키 기록 30일이 지나도 정리 전까지는 EXPIRED다.
  정리 뒤에는 NOT_FOUND다.

## 조회 상태(`ExecutionState`)와 호출자 표시
| state | 뜻 | failure_code |
|---|---|---|
| running | 임대 안에서 진행 중(접수와 실행 중은 하나) | - |
| completed | 결과가 있다(`result_json`, 7일 이내) | - |
| failed | 실패 기록 또는 임대 만료 | 호출자가 기록한 고정 코드 또는 `LEASE_EXPIRED` |
| expired | 결과 또는 키 기록의 보존 기간이 지남 | - |
| not_found | 키 기록 없음 | - |

`begin_execution`의 결과는 `STARTED / IN_PROGRESS / COMPLETED / FAILED / EXPIRED / CONTEXT_NOT_FOUND`다.
쓰기 함수의 결과는 `RENEWED / SAVED / LEASE_LOST / NOT_FOUND`다. 소유자 토큰은 `STARTED` 응답에만 있고 조회·반복 시작에는 담기지 않는다.

## 호출 순서(서비스 연결 시)
1. `begin_execution` → `STARTED`면 토큰을 보관하고 실행한다. 그 밖의 결과면 실행하지 않고 상태를 보여 준다.
2. 실행 중 10초마다 `heartbeat_execution`. `LEASE_LOST`면 즉시 중단하고 결과를 저장하지 않는다.
3. 끝나면 `complete_execution`(JSON 결과, 256KiB 이하) 또는 `fail_execution`(대문자 고정 코드).
   `LEASE_LOST`이면 늦은 결과이므로 저장되지 않았다.
- B 서비스 진입점은 `build_services(...).executions`입니다. 작업 스레드에서 `run(request_key, effective_question, context_id=..., top_k=5, pdf_pages=(), section_ids=())`을 호출합니다. 최초 실행은 완료까지 기다리며, 다른 실행이 같은 키를 사용 중이면 즉시 IN_PROGRESS를 반환합니다.
- 상태 복원에는 `lookup(request_key, context_id=...)`을 사용합니다. 다른 맥락의 키도 NOT_FOUND로 표시합니다. 완료된 결과는 view.result_json의 공개 답변입니다.
- v1 입력 해시는 실제 질문 문자열과 context, top_k, 정렬한 두 범위 배열에서 계산합니다. 중복 필터는 거부합니다. 인용 근거와 답변 스냅샷, 완료 상태는 한 트랜잭션에서 확정합니다. 검증 이력 저장만 실패하면 답변에 failed를 표시합니다.
- 하트비트가 실행권 상실을 감지하면 다음 처리 단계로 진행하지 않습니다. 이미 진행 중인 동기 호출은 반환될 때까지 기다릴 수 있습니다. 완료 저장 시 저장소가 임대를 재검사합니다.
- 실행 준비·스레드 시작 실패는 `EXECUTION_START_FAILED`, 하트비트 갱신 예외는 `HEARTBEAT_FAILED`로 기록합니다. 실패 기록 시에도 소유권을 검사하며, 기록에 성공하면 같은 키의 재호출과 상태 조회에서 같은 실패를 확인할 수 있습니다.
- 완료 또는 실패를 처리하다 실패 상태까지 DB에 기록할 수 없으면 서비스는 본문 없는 `STATUS_UNKNOWN`을 반환합니다. 호출자는 같은 키로 조회합니다. 임대가 유효한 동안에는 `running`, 갱신 없이 만료된 뒤에는 `failed / LEASE_EXPIRED`로 보일 수 있습니다. 하트비트 시 행이 없으면 `NOT_FOUND`, 기존 행의 실행권이 없으면 `LEASE_LOST`를 반환합니다.
- 같은 검증 실행 ID가 이미 있으면 저장 시각을 제외한 이력 내용이 일치할 때만 스냅샷에 `saved`를 표시합니다. 내용이 다르면 기존 이력을 보존하고 `failed`를 표시합니다.
- 호출자가 키를 보관한 뒤 `submit_paper_question`에 context_id, request_key, question을 전달합니다. 최초 호출은 완료까지 기다립니다. 응답을 놓치면 같은 context_id와 request_key로 읽기 전용 `get_paper_question_status`를 호출합니다. 두 도구는 state, failure_code, answer를 반환하고 완료된 answer에는 저장된 공개 답변과 인용이 들어 있습니다.
- 호출 전 키 보관은 호출자(C) 범위다. 서버가 접수 뒤 저장한 키를 호출 전 보관으로 간주하지 않는다.

## 정리(삭제는 명시적 호출뿐)
- `plan_execution_purge`는 dry-run으로 건수만 센다(결과를 비울 행, 지울 행).
- `purge_expired_executions`는 결과 7일이 지난 본문을 비우고, 키 30일이 지난 행을 지운다. 배치마다 짧은 트랜잭션이라 중단 후 이어서 할 수 있다.
  유효한 임대의 실행은 지우지 않는다. 자동 실행·예약은 없다. #82의 정리 명령(PR #164)과 같은 방식(dry-run + 명시적 실행)으로 맞추며,
  명령줄 연결은 그 PR이 머지된 뒤 별도로 한다.
- 질문·답변·키는 로그에 남기지 않는다.

## #184 main 통합
- #184는 #181 작업 브랜치로 병합되어 main 반영을 별도 진행한다. #119는 열어둔다.
- 요청 키 제출에서도 focus.strict_pages를 AnswerService까지 전달한다. true는 v1 해시 입력에 포함한다. false/생략은 기존 동작과 동일하므로 기존 v1 해시를 보존한다. 같은 키에서 false ↔ true 변경은 재실행 없이 충돌로 거부한다.
- 실제 API 및 Desktop 검증은 이번 통합 테스트에 포함하지 않는다.
