# 원격 학습 대시보드 1차 검증 (#591)

## 읽기 계약

`get_remote_learning_dashboard(after?, limit=20)`와 `get_remote_reading_state(context_id)`는 기존 메모 기능 flag 아래 등록한다. 인증 papers:read 및 annotations:read와 소유권 검사를 재사용한다. 조회는 기록·동의·AI 작업을 생성하지 않는다.

- 이미 열린 고정 원격 context 단위로 조회한다. 아직 context를 열지 않은 업로드/논문 목록은 이 도구 범위 밖이다.
- 게시 상태 `published`와 읽기 상태 `not_started`/`reading`/`completed`를 분리한다.
- 시작하지 않은 맥락의 revision은 null이다. 웹 학습 상세 경로는 시작한 기록에만 제공한다.
- 메모·하이라이트 건수는 현재 맥락의 삭제되지 않은 기록만 포함한다.
- UUID 커서를 기준으로 20개씩 조회한다(최대 50). 최근 시각 정렬은 아니며 새로고침은 커서 없이 시작한다.
- `last_learning_updated_at`은 저장된 학습 상태 변경 시각이며 마지막 열람이나 메모 수정 시각으로 주장하지 않는다.
- 최근 위치·퀴즈·성찰은 null과 명시적 limitations로 반환한다. 0점이나 완료로 대체하지 않는다.

## 검증

PostgreSQL·FastMCP·도구 회귀: 미시작 조회의 무변경, 21개 맥락 20+1 페이지, 상태 변경 반영, 타 계정 및 삭제 논문 차단, 삭제 메모 제외, 구조화 날짜 직렬화를 검증한다. 기존 시작·revision·동시성 회귀도 실행한다.

## 남은 범위

최근 위치 저장, 요청 키 기반 상태 변경과 재전송, 제품 카드·웹 대시보드 연결, 실제 Desktop 및 다기기 검증은 후속 단계다. 상태 변경 포트에는 revision 검사가 이미 있지만 요청 키 원장과 최근 위치는 추가해야 한다. 해당 schema·권한·staging 배포는 별도 승인 전 실행하지 않는다. 이번 단계는 AI 호출과 staging 변경이 없다. #591은 닫지 않는다.

## 2차 요청 키·최근 위치 계약

migration 0046은 `last_pdf_page`와 맥락별 요청 키 원장을 추가한다. 기존 행의 위치는 null이고 상태·메모는 유지한다. `set_remote_reading_progress`는 명시적으로 시작한 맥락만 갱신하며 기존 papers:read·annotations:write 검사와 부모 논문 잠금을 재사용한다.

- 입력: UUID request_key, reading/completed, pdf_page(양의 정수 또는 명시적 null), expected_revision.
- 페이지는 고정 parse metadata의 page_count 이하여야 한다. 메타데이터가 없으면 임의 페이지를 수락하지 않는다. null은 위치 지우기이며 상태 변경과 같은 트랜잭션이다.
- 같은 키+같은 입력은 재적용하지 않는다. 다른 입력의 같은 키 및 오래된 revision의 새 키는 conflict다.
- 늦은 재전송에는 최초 applied_revision과 **현재 state**를 함께 반환한다. 과거 완료 상태를 현재 상태처럼 되돌려주지 않는다.
- 완료·재열기는 메모·하이라이트를 삭제하지 않는다. 기존 시작 재전송도 완료를 자동 취소하지 않는다.
- 최소 추가 권한 helper `grant_learning_progress_role`: server_parse_revisions 및 요청 원장 SELECT, 원장 INSERT, 학습 기록 last_pdf_page 컬럼 UPDATE. 원장 UPDATE/DELETE나 원문·자격·예산 권한은 주지 않는다.

로컬 회귀 60개 통과(동시 동일 키, 재시작 후 복구, 늦은 재전송, 타 계정, 페이지 범위, 제한 계정 저장 및 원장 수정·삭제 차단, migration 순서 포함). 실제 staging 적용·기록 변경은 별도 승인 전 실행하지 않는다. 제품 카드·웹 입력 연결과 다기기 실제 검증은 계속 남는다.
