# #595 원격 처리 작업 조회 1차

## 구현 범위

- `list_remote_processing_jobs(after, limit)`와 `get_remote_processing_job(job_id)`는 인증된 `papers:read` 소유자에 한정한다.
- 목록은 UUID 오름차순 커서(기본 20, 최대 50)다. 동일 집합에서 상태 변경 때문에 순서가 바뀌지 않는다. 조회 중 새로 생성된 작업까지 일관된 스냅샷으로 보장하지 않으므로 새 작업 확인은 첫 페이지부터 다시 조회한다.
- 내부 running/succeeded는 processing/completed로 표시한다. 처리 완료와 학습 완료는 별개다.
- 완료 게시본은 해당 작업의 source_job_id로 연결한다. 현재 active pointer로 대체하지 않는다. 웹 경로는 논문 상세로 이동하는 안내이며 과거 게시본 고정 조회를 보장하는 링크는 아니다.
- 파일명은 승인된 업로드 의도에 연결된 경우에만 반환한다. 원문 오류·lease 토큰·owner·요청 키·payload hash는 반환하지 않는다. 검토한 고정 코드 외 오류는 processing_failed로 축약한다.
- 조회 미존재·오류·재연결은 작업 접수·claim·재시도·provider 호출을 하지 않는다.

## 검증

로컬 합성 PostgreSQL로 23건 페이지네이션, 다른 owner·삭제 논문 차단, 조회 후 queued/attempt 0 유지, 오류 비노출, 새 게시 후 이전 작업 게시본 보존을 검증한다. 기존 MCP ASGI 인증 경계 테스트에서 두 도구의 실제 등록·호출·교차 계정 차단을 검사한다.

## 남은 범위 및 실제 적용

취소는 아직 제공하지 않는다. 요청 키 원장과 상태/실행 세대 충돌 검사를 갖춘 별도 변경이 필요하다. 카드·웹 상태 비교와 실제 worker 재시작 검증도 남았다. Refs #595이며 종료하지 않는다.

staging 미적용. 기존 MCP 읽기 역할에 server_processing_jobs SELECT가 추가로 필요하고, 조회에서 사용하는 기존 papers/publications/job_authorizations/upload_intents SELECT도 확인해야 한다. 실제 권한 변경과 배포는 별도 승인 후 진행한다. 이번 변경은 migration·AI 호출·R2/PDF 전송이 없다.

## 2차: 취소 저장 계약

`PostgresProcessingCancellationStore.cancel(owner, job_id, request_key, expected_revision)`는 인증 계층에서 결정한 owner만 받는 내부 저장 포트다. 아직 MCP/웹에 노출하지 않는다.

- migration 0047의 job revision은 상태·attempt·lease token 변경에 증가한다. heartbeat만으로는 증가하지 않는다. 조회 응답에 revision을 포함한다.
- UUID 요청 키는 owner 범위에서 직렬화한다. 같은 키·job·expected_revision 재전송은 기존 applied_revision과 현재 상태를 반환한다. 같은 키의 다른 입력은 충돌이다.
- 논문 삭제 상태를 확인하고 논문/작업 행 잠금을 얻은 뒤 revision과 queued/running을 검사한다. 게시된 작업·종료 상태·이전 실행 세대의 취소는 거부한다.
- 취소 상태 변경과 요청 원장은 하나의 트랜잭션이다. 원장 저장 실패 시 모두 롤백한다. worker의 늦은 성공·실패·게시가 취소를 덮지 못한다.
- 이미 시작된 외부 HTTP 호출을 물리적으로 중지하거나 사용량을 반환하지 않는다. 비용 확정/반환은 기존 사용량 원장의 별도 절차다. 기존 게시본도 삭제하지 않는다.

로컬 회귀 범위: 동시 같은 키, 재접속 재조회, 키 재사용 충돌, worker lease 회수, heartbeat, 늦은 성공/실패, owner/삭제/종료 거부, 게시와 취소 경쟁, 원장 실패 롤백. 기존 제한 worker·웹·백업/비슈퍼유저 복구도 검사한다.

실제 적용 전 남은 것: 전용 취소 DB 역할과 변경 열 제한/행 잠금 보호, 취소 전용 OAuth scope, 기능 flag, MCP/웹 카드 명시적 확인 연결. 기존 조회 역할에 쓰기 권한을 자동 부여하지 않는다. migration 0047·배포도 아직 실제 staging에 적용하지 않는다.

## 3차: 원격 취소 확인 카드와 제한 역할

`prepare_remote_job_cancellation`은 대상·revision·비용 제한을 표시할 뿐 취소하지 않는다. 사용자가 확인한 뒤 `confirm_remote_job_cancellation`을 호출한다. 현재 인증 토큰·소유자·작업·요청 키·revision에 묶인 확인값을 검사하므로 재로그인 후 이전 카드의 쓰기는 거부한다. 읽기 전용 `get_remote_job_cancellation`으로 기존 결과를 복구할 수 있다. 응답이 유실되면 카드는 쓰기를 자동 재전송하지 않는다.

`papers:read`만 있는 기존 조회 연결을 유지한다. 준비·확인 도구에는 별도로 `jobs:cancel`이 필요하며 지원 권한 메타데이터에만 추가한다. 기능은 `REMOTE_JOB_CANCELLATIONS_ENABLED=false`가 기본이고, 활성화 시 `REMOTE_JOB_CANCELLATION_DATABASE_URL`에 기존 조회·웹 역할과 다른 전용 자격이 필요하다. DB·schema 연결 대상은 기존 MCP 설정과 같아야 한다.

migration 0048과 `grant_processing_cancel_role`은 신규 비특권 NOINHERIT 역할에 적용한다. 여섯 테이블 조회, 취소 원장 INSERT, 작업의 취소 관련 열 UPDATE, 논문 잠금용 UPDATE만 부여한다. 보호 트리거는 작업의 queued/running→cancelled 외 전환 및 논문 실수정을 차단한다. 기존 광역 권한을 회수하는 도구가 아니므로 관리자·소유자·다른 역할을 상속하는 계정에 적용하지 않는다. 설정과 비밀 URL은 저장소에 넣지 않는다.

검증: 로컬 PostgreSQL의 실제 제한 로그인으로 취소·재조회 및 상태 재활성화/성공/논문 수정/예산 쓰기 차단, 두 인증 계정의 격리, 인증 토큰 교체, 명시적 확인·멱등 재조회, 카드 응답 유실·늦은 응답·잘못된 응답을 검사한다. Docker 이미지에도 도구와 카드가 포함되어야 하며 이미지 내부에서 네트워크 없이 등록·리소스 조회를 검사한다.

남은 실제 범위: migration 0047·0048, 신규 DB 역할과 자격, Auth0 `jobs:cancel`, Render 기능 flag 및 배포는 아직 미적용이다. 실제 Claude Desktop 카드 확인·취소와 웹 상태 비교·worker 종료 검증도 남았다. 별도 승인 전 staging·AI·worker를 변경하지 않는다. #595를 자동 종료하지 않는다.
