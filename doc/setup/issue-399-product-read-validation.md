# 원격 MCP 제품 조회 연결 검증

`REMOTE_MCP_PRODUCT_READS_ENABLED=false`가 기본값이다. 켜려면 기존 MCP 인증 설정과 DB 연결이 필요하다. backend가 없으면 시작을 거부한다. 원격 endpoint 자체가 꺼져 있으면 도구를 노출하지 않는다.

## 도구

- `list_remote_papers`: 인증된 계정의 게시된 판본 목록, 20개씩 조회. 정렬은 논문/판본 ID 기준이다. 동시 목록 변경 중 offset 페이지의 완전한 snapshot은 보장하지 않는다.
- `open_remote_paper`: 명시한 논문/판본의 publication을 UUID context_key에 고정한다. AI 실행이나 PDF 다운로드가 아니다. 응답 유실 때 같은 키를 재사용한다.
- `get_remote_question`: `ask_paper` 작업의 request_key로 기존 저장 결과만 재생한다. 없거나 조회 실패해도 새 작업/attempt를 만들지 않는다.

owner는 도구 인자가 아니라 매 요청 SDK 검증 토큰의 issuer/raw subject에서 가져온다. DB의 명시적 활성 identity 연결이 없으면 거부하며 자동 계정 생성·연결은 하지 않는다. 세션 ID는 계속 거부한다. 로컬 경로·PDF 열기·질문 생성·삭제 도구는 공개하지 않는다.

## 검증

실제 ASGI와 로컬 PostgreSQL을 연결해 합성 인증 두 계정의 교차 조회 거부, 고정 publication 재조회, identity 폐기, 앱 재생성 후 동일 실행/결과 복구, tombstone 차단, 실행/attempt 수 불변을 확인했다. 서명·audience·scope 회귀는 기존 Auth0 대역 테스트를 함께 실행한다. 실제 Auth0 계정 두 개나 Claude Desktop 실검증을 대신하지 않는다. API 호출은 0회다.

## staging 적용 전 승인 범위

1. 웹 이미지에 병합 코드를 반영하고 #395 migration을 관리자 경로로 적용한다.
2. 별도 MCP DB 역할은 schema USAGE 및 아래 테이블 SELECT만 갖는다: server_remote_identities, server_papers, server_processing_versions, server_processing_publications, server_embedding_sets, server_execution_contexts, server_remote_executions, server_provider_attempts. 맥락 고정에는 server_execution_contexts INSERT를 추가한다. tombstone 트리거의 행 잠금 때문에 server_papers의 paper_id 열 UPDATE 권한도 필요하나, 전용 역할의 실제 UPDATE는 읽기 전용 트리거로 거부한다. owner 접근 제한은 인증된 서버 쿼리에서 시행한다.
3. 제품 role에 DDL·삭제·원장 쓰기·동의 변경·예산 변경·토큰 테이블·R2 권한을 주지 않는다. 로컬 비특권 역할에서 맥락 고정 성공과 토큰 테이블 조회·삭제·논문 수정·전송 제어 변경 거부를 확인했다. 이 문서만으로 실제 권한을 변경하지 않는다.
4. 기존 웹 DB 역할에 자동으로 위 권한을 확대하지 않는다. `REMOTE_MCP_DATABASE_URL`의 별도 pool만 사용하며 일반 DATABASE_URL로 fallback하지 않는다. 값이 없으면 제품 도구 활성화를 거부한다.
5. Auth0의 기존 spike scope를 제품 데이터 허용으로 무심코 확대하지 않는다. 제품 도구는 추가 `papers:read` scope를 요구한다. 해당 scope와 승인된 계정 연결을 확정하고 flag를 켜기 전에 사용자 승인을 받는다.

현재는 코드·로컬 검증만 수행했다. 운영 flag, DB 권한, Auth0, Render 설정은 변경하지 않았다. 질문 입력 영속화·큐·provider 조립·운영 수치·호스트 카드와 실계정 검증이 남아 있으므로 원격 MCP 전환 완료가 아니다.
