# 원격 요약 생성 worker 연결

Refs #581. 코드·격리 PostgreSQL·HTTP 대역 검증이며 staging 변경과 실제 API 호출은 없다.

## 실행 경로

- `solo-leveling-summary-worker --execute --max-jobs 1` 또는 `python -m solo_leveling.interfaces.cli.summary_worker --execute --max-jobs 1`로 실행한다. `--continuous`는 종료 신호까지 큐를 확인한다. 실행 플래그가 없으면 DB·API에 접근하지 않는다.
- 사용자 요청과 별도 저장/AI 동의를 거쳐 이미 접수된 요약만 소비한다. PDF 처리 완료에 따른 자동 생성이나 새로운 요청 생성은 하지 않는다.
- 정책 버전·예산 창이 같은 `generate_paper_summary` 작업을 `FOR UPDATE SKIP LOCKED`로 하나씩 선점한다. 선점 트랜잭션 종료 후 provider를 호출하며 질문·퀴즈 큐는 소비하지 않는다.
- 취소·만료·철회·삭제·lease 및 재전송 방지는 기존 영속 계약을 사용한다. 실패 시 루프를 중단하고 재시도하지 않는다. 종료 신호는 신규 선점을 멈추며 진행 중 작업은 기존 단계 timeout·heartbeat 계약을 따른다.
- `summary_payload(request, settings, policy)`와 `payload_digest`로 정확한 전송 본문을 확인한다. 검토한 본문별 입력 토큰 상한만 허용하며 미등록 본문은 전송 전에 실패한다. 글자 수를 토큰 상한으로 추정하지 않으며 계수 API도 자동 호출하지 않는다.
- 결과는 기존 구조·근거 ID 소속 검사를 통과해야 저장된다. 빈 결과·외부 근거 ID·응답 불명확은 성공으로 게시하지 않는다. 이 검사는 의미적 정확성·요약 품질 보증이 아니다.

## 운영 준비 설정 — 아직 적용하지 않음

`SUMMARY_WORKER_CONFIG`는 비밀정보 없는 JSON 파일 경로다. 아래는 자리표시자이며 그대로 실행할 수 없다. 수치는 API 승인이나 실제 토큰 실측값이 아니다.

```json
{
  "schema_version": 1,
  "database_name": "검토한_db",
  "schema_name": "검토한_schema",
  "database_role": "검토한_전용_역할",
  "policy": {
    "version": "검토한_요약_정책",
    "timeout_seconds": 120,
    "stage_timeout_seconds": 60,
    "max_calls": 1,
    "max_input_tokens": 4000,
    "max_output_tokens": 1024
  },
  "generation": {"model": "검토한_모델", "timeout_seconds": 60, "max_output_tokens": 1024},
  "window_key": "검토한_예산_창",
  "prompt_version": "remote-summary-v1",
  "requests": {
    "max_requests": 1,
    "max_input_tokens": 4000,
    "max_output_tokens": 1024,
    "payloads": {"검토한_정확한_본문_sha256": 4000}
  }
}
```

DB/schema/역할은 영문 소문자 식별자여야 한다. 웹 준비 설정과 worker의 정책·모델·언어(ko)·프롬프트를 일치시킨다. 기존 동의나 고정 입력을 새 설정으로 덮어쓰지 않는다. `requests.max_requests=1`은 **실행 1건별** 한도다. 전체 서비스·사용자 예산은 기존 영속 원장이 제한한다.

별도 `SUMMARY_WORKER_DATABASE_URL`, `SUMMARY_WORKER_OPENAI_API_KEY`, `SUMMARY_WORKER_EXECUTION_ENABLED=true`를 요구한다. 일반 DB/API 키 환경변수로 대체하지 않는다. 원격 DB는 `sslmode=verify-full`이며 현재 DB/schema/역할 및 비특권·NOINHERIT를 검사한다. 이 검사는 실제 테이블별 최소 권한 검증을 대체하지 않는다. 역할 생성·권한 변경·migration은 수행하지 않는다.

## 필수 검증과 남은 범위

로컬 필수 회귀 25개 통과: 동시 소비 1회 전송·재시작 복구, 정책/예산 창 격리, 미승인 본문·철회·취소 차단, 근거 부족/잘못된 ID/timeout 게시 차단, 기존 성공 보존·이전 세대 완료 거부, 실행 opt-in·오류 출력 마스킹·DB 오접속/관리자 역할 거부. Ruff·diff 검사도 통과했다.

전용 DB 최소 권한과 제한 계정 종단 검사, 운영 이미지/설정 배포, 실제 토큰 상한 확인·전송/비용 승인, 상세 카드·내보내기 연결, Desktop·다기기·두 계정 검증과 실제 요약 품질 실측은 남는다. 실제 환경 변경·API 호출 전 승인 범위를 안내하며 #581은 열어둔다.
