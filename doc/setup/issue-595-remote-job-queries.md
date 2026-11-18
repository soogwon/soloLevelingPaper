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
