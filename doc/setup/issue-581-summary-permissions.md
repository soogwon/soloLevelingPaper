# 요약 전용 DB 권한과 staging 적용안

Refs #581. 실제 staging 변경·PDF 전송·AI 호출 없이 로컬 PostgreSQL의 별도 LOGIN 역할로 검증한다.

## 권한 구분

`grant_summary_role(conn, schema, role, worker=False)`는 이미 생성된 전용 역할에만 적용한다. 역할 생성·암호 저장·commit·배포를 자동 수행하지 않는다. 승인된 관리자가 트랜잭션 안에서 호출하고 검사 후 commit한다. NOSUPERUSER/NOCREATEDB/NOCREATEROLE/NOBYPASSRLS/NOINHERIT이며 다른 역할의 구성원이 아니어야 한다. 기존 일반 웹/질문/worker 역할에 덧붙이지 않고 **새 요약 전용 역할 두 개**를 사용한다. 기존 권한을 삭제하는 helper가 아니므로 재적용 전 예기치 않은 추가 권한·소유권도 점검한다. 웹↔worker 용도 변경은 거부한다.

| 범위 | 웹·MCP 공용 요약 서비스 역할 | 요약 worker 역할 |
|---|---|---|
| 읽기 | 논문·context·게시본·embedding set·원문/번역 청크·요약 검토/고정 입력·동의·실행·사용량·예산·dispatch | 논문·context·고정 요약 입력·동의·실행·사용량·예산·dispatch |
| 새 기록 | 요약 검토·요약 동의·요약 실행·고정 입력·예약 | 요약 실행의 공급자 시도 |
| 갱신 | 검토의 동의 ID, 요약 동의 철회, 취소 요청, 예약·반환 | lease·종료·결과·사용량 정산·예약 반환 |
| 금지 | 실행 완료/결과·사용량 위조, 질문/퀴즈 실행 변경 | 새 동의·실행·검토 생성, 동의 철회, 질문/퀴즈 실행 변경 |
| 공통 금지 | 원문/번역 수정, 예산 상한·dispatch 설정 변경, 인증 테이블 접근, DELETE/DDL | 동일 |

행 잠금에 필요한 열 UPDATE는 역할별 trigger로 실제 수정을 차단한다. 공유 실행·예약·공급자 원장은 generate_paper_summary 작업만 쓰며 다른 작업 변경은 trigger로 거부한다. 웹 역할의 실행 갱신은 cancel_requested만 허용한다. 동의는 remote-summary-v1 고지만 다룬다. helper가 만드는 역할별 scope 함수는 SECURITY INVOKER이고, 기존 server_question_lock_guard를 사용한다. 관리자 권한으로 같은 함수/trigger를 재설치할 수 있으나 서비스 역할은 schema CREATE 권한이 없다.

이것은 **서비스별 최소 권한**이다. 같은 서비스 DB 역할 내 사용자 행을 RLS로 분리하지 않는다. owner 검증은 인증 계층과 repository 조건으로 수행하며, DB 자격을 사용자에게 제공하지 않는다. 공유 예산의 예약/소비 열 갱신 권한도 서비스의 신뢰 경계에 속한다. DB 자격 탈취까지 방어하는 사용자별 SQL 격리를 검증했다고 해석하지 않는다.

## 로컬 검증

`tests/integration/test_summary_permissions_postgres.py`는 임시 schema와 두 제한 LOGIN 역할을 생성해 실제 연결한다. HTTP 응답만 합성 대역으로 바꾸고 실제 원장·lease·budget을 사용한다.

- 동의 준비·storage/ai_provider 각각 허용→같은 키 접수→queue claim→대역 생성 1회→고정 근거/최신 결과 복구, 다른 owner 조회 거부
- 취소·철회 시 전송 0회 및 미사용 예산 반환
- 원문·상한·인증·DDL/DELETE 및 잠금용 UPDATE 차단
- 다른 질문 실행/예약/동의 수정 차단, 기존 상태 보존
- 철회 동의의 재활성화·완료 작업 취소 차단, 같은 철회 재조회 유지
- 권한 재적용, 역할 용도 변경 및 특권 역할 적용 거부

제한 역할 7개와 기존 검토/executor 15개를 합해 **22 passed**. Ruff·diff 통과.

전체 회귀 명령(격리 DB DSN만 사용):

```sh
python -m pytest tests/integration/test_summary_permissions_postgres.py tests/integration/test_remote_summary_reviews_postgres.py tests/integration/test_remote_summary_executor_postgres.py -q
```

## 최종 staging 승인 범위

아직 실행하지 않았다. [시작 설정](issue-581-summary-bootstrap.md)과 함께 아래 범위를 승인 후 수행한다.

1. 현재 정상 웹 SHA, migration 이력, 기존 권한·소유권을 읽기 전용 확인한다. 0052/0053 및 선행 migration의 미적용분이 있으면 정확한 목록을 먼저 제시한다.
2. 새 요약 웹 서비스/worker 역할 각각 1개와 DB별 `search_path=slp_staging,public`을 준비한다. 위 helper의 GRANT·역할별 trigger/function만 트랜잭션으로 적용한다. 관리자 URL은 저장하지 않고 새 자격만 기존 암호화 보관 위치에 둔다.
3. 두 역할의 직접 로그인·조회·금지 권한을 확인한다. 실제 동의·검토·접수·대역 결과를 staging에 넣는 검증은 합성 대상/개수를 먼저 확정한다. 정책·예산 행을 임의로 생성하거나 기존 상한을 늘리지 않는다.
4. 웹에 REMOTE_SUMMARY_DATABASE_URL, REMOTE_SUMMARY_SETTINGS_JSON, REMOTE_SUMMARIES_ENABLED를 설정하고 검증한 병합 SHA로 배포한다. Auth0 summaries:write는 합성 검증 계정과 전용 클라이언트에만 연결한다. 비밀 자격 입력·재로그인은 사용자에게 안내한다.
5. worker는 standby·SUMMARY_WORKER_EXECUTION_ENABLED=false 유지. 이번 준비에서 worker 실행/배포, API 키 추가, PDF 전송, 실제 생성, 유료 자원 추가는 제외한다. worker 자격의 Render 등록도 실행 전 별도 범위를 확인한다.

## 실패 복구

- commit 전 SQL/검사 실패: 트랜잭션 전체 취소.
- commit 후 연결 검사 실패: **이번 신규 역할만** NOLOGIN, 추가한 테이블/열/schema 권한 회수. 역할별 trigger/function은 승인 기록의 이름·의존 관계를 확인해 정리하고 기존 공용 guard·기존 역할·데이터는 유지한다. 기존 연결이 남아 있으면 해당 새 역할 연결만 종료하도록 별도 실행안에 포함한다.
- 웹 배포 실패: 요약 flag=false, 작업 전 정상 SHA로 복원. 이번 Auth0 추가 권한/허용 표시만 회수한다.
- 이미 적용된 추가 schema와 검증 이력은 삭제하지 않는다. 기존 웹·질문·메모·worker 설정을 손대지 않는다.

실제 요약 전송 내용·모델·호출 수·비용 상한, Desktop/웹/두 계정 검증은 남아 있으며 #581을 닫지 않는다.
