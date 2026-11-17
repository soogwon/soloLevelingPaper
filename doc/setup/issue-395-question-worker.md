# 원격 질문 worker 실행·만료·철회 정리

## 구현 범위

- `solo-leveling-question-worker`는 기본적으로 실행하지 않는다. `--execute --runtime module:factory`가 필요하며 기본 상한은 1건이다. 작업 수는 `--max-jobs N`, 상시 소비는 `--continuous`로 명시한다.
- 신뢰하는 서버 bootstrap의 context manager가 runner를 반환한다. 질문 실행은 `question_runtime.execution_runtime(pool_factory, **options)`로 기존 `create_question_runner`의 검색·전송 원장·원자적 결과 저장에 연결한다.
- `options`는 서버가 검토한 generation/semantic 설정, provider_profile_id, 로컬 embedding_runtime, window_key, token_upper_bound이며 사용자 입력이나 웹 요청에서 runtime 경로를 받지 않는다.
- 실행 전 무효 질문을 한 건 정리한다. 만료·보관 동의 철회·AI 동의 철회·논문 삭제는 유효 lease가 남아 있어도 종료한다. 기존 lease의 heartbeat·완료 저장은 거부된다.
- 종료 trigger는 미배정 예산만 반환한다. observed 사용량은 소비량으로 남고 reserved/unknown 전송분은 임의 환불하지 않는다. 재시작 시 이전 전송을 자동 반복하지 않는다.
- API 전용 정리 worker는 `solo_leveling.workers.question_runtime:maintenance_factory`를 사용한다. `QUESTION_WORKER_DATABASE_URL`과 `QUESTION_WORKER_SCHEMA`를 명시하며 모델·API 키를 읽지 않는다. 유효한 대기/실행 작업은 claim하지 않는다.
- 정리 역할의 로컬 검증용 GRANT는 `grant_question_maintenance_role`이다. 관리자 권한, 원문 조회, 공급자 원장 접근, 동의 수정, 삭제 권한은 부여하지 않는다. 다만 실행 상태·예산 행을 갱신하는 신뢰된 서버 역할이므로 웹 사용자에게 자격을 노출하지 않는다.

## Render 실행 연결

기본 `standby`는 유지한다. 승인된 이미지·전용 DB 역할 준비 후에만 다음 모드를 사용한다.

```text
python -m solo_leveling.workers.render_service --mode question-worker --runtime solo_leveling.workers.question_runtime:maintenance_factory --max-jobs 100
```

유효한 질문의 실제 실행에는 별도로 검토한 bootstrap을 지정한다. 해당 bootstrap은 전용 실행 DB 역할, 모델 fingerprint, 고정 정책/프로필/예산 window, 정확한 토큰 상한과 승인된 API 자격을 조립해야 한다. 후속 구현·로컬 검증과 승인 범위는 [생성 설정·권한 문서](issue-395-generation-runtime.md)를 참고한다. 실제 staging에는 아직 배포하지 않았다.

상한은 종료/처리한 작업 수이며 빈 큐에서는 종료 신호까지 대기한다. 자식이 실패하거나 상한에 도달하면 Render 부모는 standby로 돌아가 자동 재시작하지 않는다. SIGTERM/SIGINT 이후 새 작업을 가져오지 않으며 강제 종료 시 미확정 공급자 사용량은 보존한다. 이미 전송 중인 HTTP 요청 자체를 원격 취소하는 계약은 아니다.

## 검증

로컬 PostgreSQL/pgvector의 임시 schema와 제한 역할에서만 실행한다. HTTP·임베딩은 합성 대역이며 실제 API 요청은 0회다.

- 접수 예산 예약 → CLI/큐 → 검색 → 대역 답변 → 원자 저장 → owner/service 예산 정산
- 대기/실행 중 만료·두 동의 철회·삭제 → failed와 원인 유지, 미사용 예산 0
- 유효 작업 보존, 늦은 heartbeat·완료·실패 차단
- observed/unknown/중단된 reserved 전송 예산 보존, 자동 재전송 금지
- 제한 정리 계정의 종료·예산 반환 성공 및 원문·공급자 원장·동의 수정·삭제 거부
- 기본 미실행, 상한, runtime 정리, 진단 비밀정보 차단, Render 자식 실행·종료 계약

```text
pytest -q tests/unit/test_question_worker_cli.py tests/unit/test_remote_question_queue.py tests/unit/test_render_worker.py tests/unit/test_question_composition.py tests/integration/test_question_worker_lifecycle_postgres.py tests/integration/test_remote_question_queue_postgres.py tests/integration/test_question_admission_postgres.py tests/integration/test_question_composition_postgres.py
```

## 역할별 남은 범위

- A: 실제 질문 실행 역할의 검색·결과 저장·원장 최소 권한과 배포 설정 확정. 정리 역할 적용도 별도 승인 후 수행한다.
- B: 운영 bootstrap의 로컬 임베딩/제공자 프로필/토큰 상한을 고정하고 접수 snapshot과 일치시킨다. 기존 `staging-question-review-no-dispatch` 작업을 실제 생성용으로 재사용하지 않는다.
- C: 승인된 신규 합성 질문으로 Desktop 동의→실행→답변/인용→재조회와 철회/만료 표시를 실제 staging에서 확인한다.
- 이번 변경은 staging DB·Render 환경변수·worker 모드·Auth0 권한을 변경하지 않는다. 실제 API 호출에는 신규 승인이 필요하다. #395/#396/#399는 닫지 않는다.

## 상시 소비 준비 (2026-11-18)

이번 추가는 **코드·합성 자동 테스트**다. 배포 이미지, staging DB, Render 설정은 변경하지 않았다.
앞서 별도 단일 프로세스에서 성공한 합성 질문 1건은 상시 프로세스 운영 검증과 구분한다.

- CLI는 `--execute`를 생략하면 실행하지 않고, 수명을 생략하면 기존처럼 1건 상한이다.
- `--continuous`를 명시하면 종료 신호까지 순차 소비한다. `--max-jobs`와 동시에 지정할 수 없다.
- 빈 큐는 5초 간격으로 대기하며 종료 신호로 대기를 깨운다. 실행 중 종료 요청은 현재 작업을 마무리한 뒤 다음 claim을 막는다.
- 실패·알 수 없는 결과·예산 제한에 도달하면 소비를 중단한다. Render 부모는 고정 실패 진단 후 standby가 되며 자동 재시작하지 않는다. **프로세스가 살아 있다는 사실만으로 큐가 소비 중이라고 판단하면 안 된다.**
- `--shutdown-grace-seconds`는 1~600초, 기본 5초다. 대기 한도를 넘으면 자식 프로세스 그룹을 강제 종료·회수한다. 공급자에 이미 보낸 HTTP 요청을 취소하거나 사용량을 환불한다는 뜻은 아니다.
- 호스트가 허용하는 종료 유예가 이 값보다 짧으면 호스트가 먼저 강제 종료할 수 있다. 실제 배포 전에 작업 timeout·정산 여유와 호스트 유예를 함께 맞춰야 한다. 아래 90초는 설정 전달 예시이며 운영 확정값이 아니다.

```text
python -m solo_leveling.workers.render_service --mode question-worker --runtime solo_leveling.workers.question_runtime:maintenance_factory --continuous --shutdown-grace-seconds 90
```

이는 **승인 후 적용할 정리 전용 예시**다. 그대로 실행하거나 실제 생성 runtime으로 교체하지 않는다.
실제 생성 bootstrap의 payload 해시 허용 목록, 물리 호출 상한, 동의·정책·예산·lease 검사는 우회하지 않는다.
고정 검증용 payload 설정을 임의 사용자 질문을 계속 처리하는 운영 설정으로 간주하지 않는다.
재시작으로 인메모리 한도가 초기화되므로 DB 전송 원장·예산과 신규 호출 승인 확인 없이 재시작하지 않는다.

### 이번 검증과 남은 승인

| 역할 | 코드·자동 테스트 확인 | staging 상시 운영 전 남은 범위 |
| --- | --- | --- |
| A | 유휴/작업 중 종료, 자식 정상 회수·강제 회수, 잘못된 설정 차단 | 새 이미지 검토·게시, 실행 명령·종료 유예, 전용 역할·정리 주기·고정 진단 알림 설정 |
| B | 기존 생성 bootstrap·전송 원장 제한 유지, 실패 후 재시도 금지 | 일반 질문의 토큰 상한·허용 전송 계약, 반복 작업 정책·사용자/서비스 예산 및 실제 호출 승인 |
| C | CLI와 Render 옵션 연결, 기본 미실행/1건 유지, 민감 오류 출력 차단 | 실제 재배포/재시작 중 요청 키 재조회, 정상·철회·만료 상태의 Desktop 표시 |

`render_verification_failed`, `render_service_failed`, `render_cleanup_failed`는 운영자가 원장·lease·사용량을 확인할 신호다.
호스트의 자동 재배포/재시작까지 금지하는 기능은 아니므로 장애 후 자동 재전송 방지는 DB 계약과 함께 확인한다.
복구는 승인된 이전 이미지와 `standby`, `QUESTION_WORKER_EXECUTION_ENABLED=false`로 되돌리고,
미확정 전송은 원장 확인 전 재실행·환불하지 않는 순서로 진행한다. 이번 작업에서 실제 복구 조작은 하지 않는다.
