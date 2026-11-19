# 요약 웹 시작 설정과 staging 승인 범위

Refs #581. 실제 staging 변경·DB 쓰기·API 호출 없이 설정·앱 조립·HTTP/MCP 연결을 대역으로 검증했다.

## 확정한 환경변수

- `REMOTE_SUMMARIES_ENABLED`: 기본 `false`. 정확히 `true`일 때만 전용 설정을 읽는다.
- `REMOTE_SUMMARY_DATABASE_URL`: 요약 서비스 전용 PostgreSQL 자격. 일반 `DATABASE_URL`로 대체하지 않는다. 원격 연결은 `sslmode=verify-full`, 로컬 리허설은 localhost 예외를 허용한다. DB별 search_path는 승인된 역할 설정으로 준비하며 URL의 임의 options는 받지 않는다.
- `REMOTE_SUMMARY_SETTINGS_JSON`: 아래 구조. 값은 설명용 예시이며 전송·과금 승인이나 운영 예산을 의미하지 않는다.

```json
{
  "policy": {
    "version": "검토한_요약_정책",
    "timeout_seconds": 120,
    "stage_timeout_seconds": 60,
    "max_calls": 1,
    "max_input_tokens": 4000,
    "max_output_tokens": 1024
  },
  "settings": {
    "provider": "openai",
    "model": "검토한_모델",
    "prompt_version": "remote-summary-v1",
    "language": "ko",
    "budget_policy_version": "검토한_요약_정책"
  },
  "window_key": "검토한_예산_창",
  "review_seconds": 3600,
  "web_origin": "https://검토한-staging-origin"
}
```

동일 정책 버전, 최대 1회 호출 계약, 60~86400초 검토 기간, HTTPS 웹 origin, 중복/알 수 없는 JSON 키를 검증한다. 이 설정은 API 키를 포함하지 않는다. 요약 worker의 정책·예산 창·모델·프롬프트와 별도로 대조해야 한다.

기존 원격 MCP·제품 조회와 product query 연결이 있어야 활성화된다. 명시적으로 주입한 요약 서비스와 환경 설정을 동시에 사용하면 시작을 거부한다. 전용 풀은 lifespan에서 열고 종료/열기 실패 시 닫는다. 웹과 MCP는 같은 요약 서비스를 사용한다. 동의 부여는 기존 웹 세션/CSRF 경로만 사용하며 MCP에 동의 부여 도구를 추가하지 않는다. worker DB 역할·API 키는 별도이고 이번 코드에서 worker를 시작하지 않는다.

## 테스트

필수 25 passed: 설정 15개 + 기존 웹 조회 2개 + MCP 도구 8개. 기본 비활성, 자격 미대체·비밀정보 숨김, JSON 중복/잘못된 정책·모델 계약/HTTPS 차단, 앱 기능 조건, 실제 웹 라우트→대역 서비스 및 MCP 동일 서비스 연결, 풀 정상 종료·실패 정리를 확인했다. 실제 DB/외부 AI는 사용하지 않았다. Ruff·diff 통과.

## staging에서 승인받을 실행 범위

1. 현재 배포 SHA·기능 설정·migration 이력·역할 권한을 읽기 전용으로 기록한다. 0052/0053과 선행 migration은 실제 미적용분만 별도 확정한다.
2. 요약 서비스 전용 비특권·NOINHERIT 역할과 DB별 search_path, 조회/검토/동의/접수/취소/예산 예약에 필요한 최소 권한을 로컬 제한 계정으로 검증한 뒤 적용한다. 이번 PR은 GRANT SQL 확정·실제 최소 권한 검증까지 완료한 것이 아니다. 적용 SQL과 회수 목록을 실행 전에 제시한다.
3. Auth0의 summaries:write를 기존 합성 검증 계정에만 허용하고 전용 클라이언트 범위를 확인·재인증한다. 기존 웹/질문/메모 권한은 유지한다.
4. 위 환경변수 3개를 웹에 설정하고 이 PR의 검증한 병합 커밋으로 배포한다. 실행 전 정상 배포 SHA를 복구 대상으로 기록한다. 새 자격 입력은 사용자에게 별도로 안내한다.
5. 요약 worker는 standby 및 SUMMARY_WORKER_EXECUTION_ENABLED=false를 유지한다. API 키 추가·유료 요청·PDF 업로드·운영 삭제·새 유료 자원 생성은 제외한다.
6. 기존 합성 context의 준비·조회·권한/빈 상태·웹/MCP 연결을 검증한다. 새 동의/검토/예산/합성 완료 결과 기록 생성이 필요하면 대상·수량을 별도 명시해 승인받는다. 실제 provider 결과인 것처럼 합성 fixture를 표시하지 않는다.

실패 시 기능 flag를 끄고 작업 전 정상 웹 SHA로 복원한다. 이번에 추가한 Auth0/DB 권한과 역할 로그인만 회수하며 기존 데이터는 보존한다. SQL 실패는 트랜잭션 취소, 이미 적용된 추가 schema는 파괴적으로 되돌리지 않는다.

실제 생성은 이후 전송할 원문·번역·지시, 모델, 정확한 payload/토큰 상한, 호출 수·전체 비용 상한을 별도 승인받는다. Desktop↔웹·다기기·두 계정 및 실제 요약 품질 실측도 남아 있어 #581은 열어둔다.
