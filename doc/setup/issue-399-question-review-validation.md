# 원격 질문의 인증 웹 동의와 MCP 접수

## 구현과 책임

- A: 질문 검토 초안은 owner·UUID 요청 키·고정 context·입력 hash·정책·window에 결합한다. 검토 가능 기한을 넘기면 새 동의/접수를 거부한다. 논문 삭제 시 context와 함께 초안도 연쇄 삭제한다.
- B: 두 목적의 웹 동의가 모두 현재 입력·고지에 맞아야 기존 원자 예산 접수로 전달한다. 모델이 준 confirmed 값은 동의가 아니다. 검토/조회는 provider를 호출하지 않는다.
- C: 웹 `#/questions/{request_key}`에서 질문·판본·페이지/섹션·모델·호출/토큰 상한과 저장/AI 전송 동의를 따로 표시한다. 응답 유실은 같은 키 재조회, 로그아웃·화면 이탈은 늦은 응답 표시를 차단한다.

MCP는 `prepare_remote_question`과 `submit_remote_question`을 제공하며 동의 기록 도구는 제공하지 않는다. 준비 도구 설명에 질문/검색 조건의 서버 초안 저장을 명시한다. 서비스 조립체가 명시적으로 주입된 경우에만 등록하며 추가 OAuth `questions:write`를 요구한다. 기존 기본값은 조회 전용이다. 인증된 웹의 POST는 기존 세션/CSRF 경계를 사용한다.

## 검증

Python 관련 52개와 웹 3개 통과. 실제 격리 DB+ASGI에서 무권한 거부, 두 동의 분리, 동시 동의·동시 제출의 단일 실행, 고지 변경·철회·만료 차단, 같은 키 입력 충돌, migration/초기화/비슈퍼유저 복구를 확인했다. 웹에서는 조회만으로 동의/접수가 없고 HTML 이스케이프, 로그아웃 뒤 늦은 응답 차단, 접수 응답 유실 후 실행 ID 복원을 확인했다.

## 남은 환경 연결

실제 Claude Desktop·staging 브라우저 검증은 아직 하지 않았다. 서비스 bootstrap 설정, migration 0039~0041, 전용 DB 최소 권한, Auth0 questions:write와 검증 계정 재인증은 별도 승인 대상이다. 실제 API 호출/논문 전송/운영 데이터 삭제 없음. 운영 profile·budget window·토큰 추정기·실제 비용 상한과 검토 기한은 운영자가 확정해야 한다.

초안의 expires_at은 동의/접수 가능 기한이며 물리 삭제 완료 시각이 아니다. 별도 보관/정리 정책이 필요하다. 철회한 요청은 자동 재동의·새 키 재제출하지 않는다.

## 배포 준비 보완

`REMOTE_QUESTIONS_ENABLED=false`가 기본이다. true일 때만 `REMOTE_QUESTION_SETTINGS_JSON`의 전체 정책·처리 설정·window·검토 기한·HTTPS origin과 별도 `REMOTE_QUESTION_DATABASE_URL`을 요구한다. 정책 누락·중복 JSON·버전 불일치는 시작을 거부한다. 질문 검토 DB 풀은 기존 웹/MCP 읽기 풀과 수명을 분리한다.

`grant_question_review_role`은 기존 비특권 역할에 동의/접수에 필요한 테이블·열 권한만 부여한다. 로컬 실제 제한 역할로 두 동의·동일 접수·재조회를 통과했으며 web_sessions/device_credentials 조회, 정책 상한 변경, 논문 수정, 실행 완료 위조, 삭제를 거부했다. 아직 실제 역할 생성/자격 설정은 하지 않았다.
