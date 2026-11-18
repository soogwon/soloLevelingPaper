# 원격 메모 제품 조립 준비

기본값은 비활성이다. 이번 PR은 코드·대역 검증만 수행하며 실제 환경 설정과 DB 권한은 변경하지 않는다.

## 별도 승인이 필요한 설정

- 0043·0044 migration 적용 및 기존 데이터/복원 목록 확인
- 새 전용 MCP 역할에 `grant_annotation_role`, 새 전용 웹 역할에 `grant_annotation_web_role` 적용
- 실제 계정에 기존 `papers:read`와 필요한 `annotations:read` / `annotations:write`만 연결
- `REMOTE_ANNOTATIONS_ENABLED=true`
- `REMOTE_ANNOTATION_WEB_DATABASE_URL`: 동의 저장 가능한 전용 웹 역할
- `REMOTE_ANNOTATION_MCP_DATABASE_URL`: 동의 조회만 가능한 전용 MCP 역할
- `REMOTE_ANNOTATION_SERVICE_ID`: 재시작·배포·기기 간 동일한 요청 복구용 식별자

두 URL은 동일 DB·검색 경로를 가리키되 사용자 이름이 달라야 한다. 같은 계정·다른 DB·누락된 설정을 거부한다. 이 검사는 실제 GRANT의 적정성을 보장하지 않으므로 적용 전에 제한 역할의 성공/거부 테스트를 별도로 실행해야 한다. 기존 관리자 URL을 사용하지 않는다. 제품 조회 MCP와 owner resolver가 준비되지 않은 상태에서도 활성화를 거부한다.

동의 부여 경로는 인증 웹에만 있으며 MCP 도구가 대신 동의하지 않는다. 서비스 ID를 변경하면 과거 요청 키 조회 범위가 달라지므로 임의 변경하지 않는다. 비밀 URL은 로그·문서·티켓에 남기지 않는다.

## 복구

문제가 발생하면 `REMOTE_ANNOTATIONS_ENABLED=false`로 복원하여 새 제품 경로를 비활성화한다. 별도 승인 후 신규 역할 권한을 회수하되 기존 메모·동의 이력과 migration 테이블은 자동 삭제하지 않는다. PDF·AI·기존 웹 계정 설정은 이 변경 대상이 아니다.

## 검증

대역으로 기본 비활성, 엄격한 설정 거부, 자격 repr 제외, 서로 다른 풀 조립·정상 종료 정리·두 번째 풀 시작 실패 시 첫 번째 정리를 확인한다. 실제 DB 역할과 staging·Desktop·다중 기기 검증은 아직 남아 있다.
