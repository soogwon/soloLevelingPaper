# 원격 퀴즈 웹 시작과 전용 DB 역할

Refs #593. 이 문서는 코드 준비 범위이며 staging 적용 승인이 아니다.

## 웹 시작 설정

기본값은 비활성이다. `REMOTE_QUIZZES_ENABLED=true`일 때 아래 두 설정을 모두 요구한다.

- `REMOTE_QUIZ_DATABASE_URL`: 새 퀴즈 서비스 역할의 DSN. 일반 `DATABASE_URL`로 대체하지 않는다. 원격은 `sslmode=verify-full`을 요구하고 전용 풀에 certifi CA를 지정한다. 로컬 테스트 주소만 TLS 없는 연결을 허용한다.
- `REMOTE_QUIZ_SETTINGS_JSON`: 아래 구조의 명시적인 정책. API 키는 포함하지 않는다.

```json
{
  "policy": {"version":"quiz-policy-v1","timeout_seconds":120,"stage_timeout_seconds":60,"max_calls":1,"max_input_tokens":4000,"max_output_tokens":1024},
  "settings": {"provider":"openai","model":"승인된 모델 ID","prompt_version":"remote-quiz-cloze-v1","language":"ko","budget_policy_version":"quiz-policy-v1"},
  "window_key":"승인된 예산 창",
  "review_seconds":3600,
  "web_origin":"https://example.invalid"
}
```

예시는 운영 예산 승인이 아니다. 같은 정책·예산 창의 사용자/서비스 원장, 별도 생성 저장·AI 동의가 있어야 접수한다. 답안 저장 동의는 생성 동의와 별개다. 기존 worker 설정과 정확한 본문 해시별 토큰 상한은 [퀴즈 runtime](issue-593-quiz-runtime.md)을 따른다.

운영 worker에는 `remote-quiz-cloze-v1`을 명시한다. 기존 `remote-quiz-generation-v1` 입력은 호환 조회·기존 주입 실행을 위해 허용하지만 빈칸형 runtime이 자동 변환하지 않는다. 동의 고지 버전 `remote-quiz-generation-v1`과 생성 프롬프트 버전은 별개다.

제품 MCP 활성화·조회 객체가 필요하며 환경 설정과 직접 서비스 주입을 동시에 사용하면 시작을 거부한다. 하나의 전용 풀에서 생성 검토 서비스와 제출/복습 서비스를 웹·MCP에 연결한다. 풀은 lifespan에서 열고 시작 실패와 종료 시 닫는다. 웹 시작은 worker나 AI 호출을 실행하지 않는다.

## 역할 분리

`grant_quiz_role(conn, schema, role, worker=False)`를 승인된 관리자 트랜잭션에서 호출한다. helper는 역할 생성·암호 저장·commit·migration·배포를 하지 않는다. 신규 NOSUPERUSER/NOCREATEDB/NOCREATEROLE/NOINHERIT/NOBYPASSRLS 역할을 서비스/worker 각각 준비하고 DB별 search_path를 설정한다. 다른 역할의 구성원이면 거부한다. 기존 추가 권한을 제거하는 함수가 아니므로 새 역할의 소유권·기존 권한도 점검한다.

| 범위 | 웹·MCP 서비스 | 퀴즈 worker |
|---|---|---|
| 준비·생성 동의·접수·취소 | 허용 | 검토/동의 생성 금지 |
| 고정 퀴즈 조회·답안 저장·시도 복구 | 허용 | 답안 INSERT 금지 |
| 생성 결과 퀴즈 INSERT·실제 사용량 정산 | 금지 | 허용 |
| 일반 웹 세션·기기 자격 조회 | 금지 | 금지 |
| 예산 상한 확대·원문 수정·DELETE | 금지 | 금지 |

공유 실행·예약·전송 원장은 `generate_quiz`만 변경하도록 역할별 trigger를 둔다. 서비스는 완료 상태를 위조할 수 없고, worker는 동의를 만들거나 철회를 되돌릴 수 없다. 논문/동의/제어 행 잠금용 UPDATE는 실제 행 수정을 막는 기존 lock guard를 사용한다. 이 역할 분리는 사용자별 RLS가 아니며 인증 owner·context 격리는 제품 서비스가 수행한다. 웹과 MCP는 같은 서비스 역할을 사용하되 동의 쓰기 API는 웹 세션만 허용하고 MCP에는 동의 부여 도구가 없다.

## 필수 검증

- `tests/unit/test_remote_quiz_settings.py`: 기본 비활성, 전용 설정 누락·위조·잘못된 TLS 차단, 웹/MCP 연결, 풀 정리.
- `tests/integration/test_quiz_permissions_postgres.py`: 로컬 제한 계정으로 준비→두 동의→접수→대역 생성→제출/복구, 취소·철회·다른 작업 변경 차단.
- `deploy/validation/remote_mcp_image.py`: 네트워크 없는 배포 이미지에서 퀴즈를 실제 환경 설정으로 조립.

## 별도 승인 후 남은 적용

최신 migration 이력 확인, 새 두 역할·최소 권한/search_path, 서비스 DSN·정책·기능 flag, Auth0 퀴즈 scope/검증 계정 연결, 병합 이미지 배포를 별도 승인 범위로 확정한다. 실제 worker 설정/이미지 배포·본문 상한·API 호출은 별도 단계다. 기존 #592 성찰 배포 보류 결정을 이 변경으로 해제하지 않는다.

실패 시 퀴즈 flag를 끄고 이전 웹으로 복원하며 새 역할의 로그인·권한만 회수한다. 기존 기록과 schema는 삭제하지 않는다. 실제 Desktop·두 계정·다기기·생성 품질 검증은 남아 있다.
