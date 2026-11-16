# 원격 처리 worker 입력 연결

관련 티켓: #517. 이 범위는 PostgreSQL 큐와 검증된 R2 PDF를 B의 메모리 처리 단계에 연결한다.
결과 저장·원자 게시·작업 성공 전이는 후속 범위다.

## 실행 경계

`RemoteProcessingWorker.run_once()`는 다음 순서를 고정한다.

1. `PostgresProcessingJobStore.claim()`으로 한 작업과 새 lease token을 받는다.
2. `PostgresRemoteProcessingInputStore`가 같은 owner/job/lease의 authorization, verified upload
   intent, ready remote PDF, 삭제되지 않은 paper, 현재 보관·AI 동의를 함께 확인한다.
3. DB에 기록된 서버 object key만 R2에서 읽고 실제 크기·Content-Type·SHA-256·PDF header를
   다시 확인한다. 사용자 경로나 URL은 입력으로 받지 않는다.
4. 새 parse/translation/embedding ID와 원래 lease token을 고정한 `ProcessingSource`로
   `RemoteProcessingStages.run()`을 호출한다.

처리 단계의 `check_allowed`에는 `PostgresRemoteProcessingInputStore.allowed`를 주입한다. 이 검사는
원래 claim token을 그대로 비교하므로 lease가 회수된 뒤 이전 worker가 새 token을 빌려 계속 실행할
수 없다. 번역 직전에는 현재 동의 외에 `server_dispatch_controls.dispatch_enabled`도 확인한다.
실제 provider 전송의 비용 예약·attempt 원장 검사는 B translator의 기존 dispatch gate가 담당한다.

## 완료로 표시하지 않는 이유

runner가 반환하는 `ClaimedProcessingResult`는 lease에 묶인 미게시 메모리 결과다. `prepared`를
`succeeded`로 바꾸거나 active publication을 갱신하지 않는다. 후속 저장/게시 구현은 동일 lease에서
staging 행을 쓰고, owner·동의·예산·tombstone·게시 집합을 재검증한 한 transaction으로 active
pointer와 job 성공을 함께 확정해야 한다.

현재 runner 자체에는 장시간 처리 중 lease를 연장하는 supervisor가 없다. 실제 worker 배포 전에
heartbeat 수명 관리, 안전한 실패 분류, 프로세스 종료/재시작, parser 자원 격리와 원자 게시를
연결해야 한다. 이 모듈만 배포 가능한 전체 worker로 간주하지 않는다.

## 검증

- 단위: owner/job/lease SQL 결합, 이전 token 사용, R2 stream 크기 제한·닫기, 메타데이터 불일치의
  처리 전 차단, 성공 상태 미기록
- PostgreSQL: verified 원격 업로드와 현재 두 동의의 결합, AI 동의 철회 차단, lease 회수 뒤 이전
  세대의 load/check 거부

실제 PostgreSQL 테스트는 격리된 `SOLO_TEST_POSTGRES_DSN`이 있을 때 실행한다. 실제 R2·provider·모델,
비공개 PDF는 이 테스트에서 사용하지 않는다.

## C 재검토 보완 (2026-11-17)

기존 CI Python job의 실제 실패는 PostgreSQL 테스트 2개였다. 공용 `admitted_source`
대역이 `private/files/<임의 ID>.pdf`를 게시해 worker의 owner/paper/version 경로 검사에서
거부됐다. 대역은 실제 `finalize_upload`처럼 임시 객체의 owner/paper/version을 보존하도록
수정했다. 제품의 경로 검사는 완화하지 않았다.

R2 byte reader의 read/close 원본 예외도 고정 코드로 제한했다. 닫기 실패는 성공으로 처리하지
않으며 선언 길이 검사와 실제 읽은 크기 제한을 모두 유지한다. 매번 남은 크기+1 이하를 요청하고
짧은 응답을 반복해서 읽어 PDF 헤더·본문을 보존한다.

macOS 로컬: worker·큐·B 처리 단계·HTTP 업로드 및 관련 PostgreSQL 테스트 묶음에서
**169 passed, 8 skipped**. skip 8개는 실제 PostgreSQL DSN 부재이며 CI 성공을 뜻하지 않는다.
읽기/닫기 오류 3종, 선언 길이와 다른 실제 증가, 짧은 응답 회귀 5개를 추가했다.
실제 R2·provider 호출은 하지 않았다. 수정본 PostgreSQL CI 재검증이 남아 있다.
