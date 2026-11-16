# #395 동일 실행의 읽기 전용 복구 경계

> 실제 PostgreSQL 실행/저장/복구는 main에 연결돼 있다. 구현 근거와 아직 남은
> runner·정책·종결 경합은 [A·B·C 실행 연결 계약](remote-question-runner-contract.md)에서 구분한다.

`application/remote_replay.py`는 인증된 서버 owner·operation·request key로만 기존 실행을 조회한다.
input_hash는 유일 키가 아니라 일치 검사용이다. 같은 키의 다른 hash/context는 충돌이며 새 실행을
만들지 않는다. 조회 실패·결과 손상도 생성·재접수·다른 키로 대체하지 않는다.

ExecutionReader는 A의 `PostgresRemoteExecutionStore`가 구현하는 읽기 포트다. 현재 조회 권한과
원자 저장된 결과를 같은 읽기 snapshot에서 반환한다. 메모리 대역은 실제 영속 멱등성 증명이 아니다.
각 호출 전 인증/owner 매핑은 C/#390 경계가 수행하며 이 DTO 생성은 인증을 보장하지 않는다.

저장 결과는 정확한 UTF-8 JSON과 SHA-256을 함께 읽는다. B는 hash·공개 필드·context·기존 주장/인용
구조를 검증하고 복사된 dict를 반환한다. 의미 검증을 다시 실행하거나 model_checked를 재부여하지 않는다.
원자 저장 시의 원문/출처 일치 검증과 현재 조회/삭제 권한은 A adapter가 보장해야 한다.
SHA-256은 인증 서명이나 서버 저장의 진위 증명이 아니다.

기존 partial·verification_level·주장/근거 ID·verification_history를 그대로 보존한다.
현재 동의/한도와 조회 권한은 구분하며 budget limited/usage_unknown만으로 결과를 지우지 않는다.
실행 상태·cancel_requested/cancelled·결과 가용성을 구분하고 완료라도 결과가 없으면 상태만 반환한다.

이 코드에는 submit/run/provider/save 포트가 없고 제품 도구에 등록하지 않는다.
접수·답변/근거의 원자 저장과 usage 원장의 각 저장 경계는 구현돼 있으나, 이들을 실행하는
runner와 정책 snapshot·deadline·heartbeat·종결 사유 분리, C 공개 매핑은 남아 있다.
답변/근거/usage 모두를 하나의 트랜잭션에 저장하는 것으로 간주하지 않는다.
실제 Desktop 재시작·두 계정·종단 동시 요청 검증은 별도이며 기존 SQLite 실행 저장을 원격으로 재사용하지 않는다.
가짜 결과를 새 검증 결과로 만들거나 타임아웃을 근거 부족으로 해석하지 않는다. #395는 열어둔다.

## C 계정별 복구 모델

`interfaces/mcp/remote_recovery.py`는 인증 adapter가 전달한 owner별로 저장된 요청 키를 조회하고
기존 `read_existing_execution`만 호출한다. 계정 전환·로그아웃·재연결 때 표시 결과와 세대를 초기화하고,
조회 중 계정이 A→B→A로 돌아와도 이전 세대 결과를 버린다. 없음·접근 거부·충돌·조회 오류는
자동 제출/새 키 생성으로 바뀌지 않는다. 결과 사본은 화면에서 변경해도 내부 표시를 변경하지 않는다.

RecoveryKeyStore는 **저장 포트 초안**이다. 테스트의 메모리 대역 공유는 프로세스/Claude 재시작의
영속 복구 증거가 아니다. 실제 adapter는 owner+operation+request_key별 원자 저장·입력 충돌 거부,
보관/삭제 정책을 구현해야 한다. 계정 선택 메서드는 인증 수단이 아니며 실제 요청 전 현재 인증과
서버의 접근 권한 재검사는 필요하다. 이 모델을 제품 도구에 등록하거나 공개 wire 코드로 확정하지 않는다.

## C와 실제 PostgreSQL 조회의 연결 회귀

`tests/integration/test_remote_recovery_postgres.py`는 main의 `PostgresRemoteAnswerStore`로 저장한
답변을 `PostgresRemoteExecutionStore` → `read_existing_execution` → `AccountRecovery`로 재조회한다.
새 화면/조회 객체와 동시 조회 8회에서 같은 실행 ID·답변·주장·인용·검증 수준을 복원하고,
계정 전환/타인 키, hash/context 충돌, 손상된 결과, tombstone, 미존재 실행을 확인한다.
조회 전후 실행·근거·provider attempt·예산·동의 행 전체가 같은지 대조한다.

인증된 owner 전달, 키 저장, 생성/의미 판정은 대역이다. 특히 키는 메모리에만 있으므로
프로세스/Claude Desktop 재시작 후 키 영속 복구나 실제 OAuth 계정 전환을 검증한 것이 아니다.
공개 MCP 등록·새 유료 접수·외부 API 호출은 하지 않는다. PostgreSQL DSN이 없으면 해당 검증은 skip한다.

```sh
PYTHONPATH=src python -m pytest tests/unit/mcp/test_remote_recovery.py tests/unit/test_remote_replay.py tests/integration/test_remote_recovery_postgres.py -q
```
