# 원격 퀴즈 큐·실행 설정

Refs #593. 코드와 로컬 격리 DB·HTTP 대역만 검증했으며 staging 변경·실제 API 호출은 없다.

## 연결 범위

- `solo-leveling-quiz-worker --execute --max-jobs 1` 또는 `python -m solo_leveling.interfaces.cli.quiz_worker --execute --max-jobs 1`로 승인된 큐를 소비한다. `--continuous`는 종료 신호까지 실행한다. 기본값은 실행하지 않는다.
- `generate_quiz`이면서 설정의 정책 버전·예산 창이 같은 작업만 `FOR UPDATE SKIP LOCKED`로 선점한다. 질문·요약 큐는 건드리지 않는다. 취소·만료·철회 작업은 기존 종료/예산 반환 계약을 사용한다.
- 재시작 후 이미 전송을 시도한 실행은 재전송하지 않는다. 처리 실패 시 루프를 중단한다. SIGTERM은 새 선점을 멈추고 진행 중 작업은 기존 단계 timeout·lease 계약으로 마무리한다.
- 입력 본문은 `quiz_payload(request, settings, policy)`로 단일 조립한다. `payload_digest`로 정확한 본문의 해시를 구하고, **검토한 해당 본문의 입력 토큰 상한**만 설정에 넣는다. 미등록 본문은 전송 0회로 실패한다. 글자 수나 바이트 수를 토큰 상한으로 간주하지 않는다. 이 변경은 토큰 계수 API를 호출하지 않는다.
- `remote-quiz-cloze-v1`은 고정 원문/번역 전체의 한 표현을 `[빈칸]`으로 바꾼 문항만 생성한다. 정답을 넣었을 때 해당 근거가 정확히 복원되어야 한다. 객관식은 정규화된 선택지가 모두 다르고 복원 가능한 선택지가 정답 하나뿐이어야 한다. 단답형도 같은 근거 복원 규칙을 사용한다.
- 자유 서술형 질문의 의미 판단·난이도·교육적 품질은 보장하지 않는다. 임의의 자유 생성에 대한 검증기를 구현한 것으로 보지 않는다. 별도 검증 API나 자동 보완 호출은 없다.

## 운영자가 준비할 설정 — 아직 적용하지 않음

`QUIZ_WORKER_CONFIG`는 아래 구조의 검토한 JSON 파일 경로다. 비밀정보는 파일에 넣지 않는다.

```json
{
  "schema_version": 1,
  "database_name": "검토한_db_이름",
  "schema_name": "검토한_schema_이름",
  "database_role": "검토한_전용_역할",
  "policy": {
    "version": "검토한_퀴즈_정책",
    "timeout_seconds": 120,
    "stage_timeout_seconds": 60,
    "max_calls": 1,
    "max_input_tokens": 4000,
    "max_output_tokens": 1024
  },
  "generation": {"model": "검토한_모델", "timeout_seconds": 60, "max_output_tokens": 1024},
  "window_key": "검토한_예산_창",
  "validation_profile": "remote-quiz-cloze-v1",
  "requests": {
    "max_requests": 1,
    "max_input_tokens": 4000,
    "max_output_tokens": 1024,
    "payloads": {"정확한_전송_본문의_sha256": 4000}
  }
}
```

위 예시는 설명용 자리표시자이며 그대로 실행할 수 없다. DB 이름·schema·역할은 영문 소문자 식별자여야 한다. 수치도 실제 전송 승인이나 실측값이 아니다.

별도 `QUIZ_WORKER_DATABASE_URL`, `QUIZ_WORKER_OPENAI_API_KEY`, `QUIZ_WORKER_EXECUTION_ENABLED=true`가 있어야 조립한다. 일반 `DATABASE_URL`·`OPENAI_API_KEY`로 대체하지 않는다. 원격 DB는 `sslmode=verify-full`을 요구하며 현재 DB/schema/역할과 비특권·NOINHERIT를 검사한다. 권한을 생성하거나 migration을 적용하지 않는다.

웹의 생성 검토 설정도 같은 정책·모델·`prompt_version=remote-quiz-cloze-v1`을 사용해야 한다. 이전 프롬프트의 요청을 이 runtime에서 자동 전환하지 않고 전송 전에 차단한다. 이미 받은 동의·고정 입력을 덮어쓰지 않는다.

## 필수 회귀

동시 큐 소비의 1회 전송, 정책·예산 창 격리, 미승인 본문 전송 차단, 정답 불일치 게시 차단, 취소·철회, 기존 만료·중단·늦은 완료 회귀, CLI 기본 차단·실패 출력 마스킹, 잘못된 DB/관리자 역할 거부를 확인한다.

로컬 필수 회귀 29 passed, Ruff·diff 검사 통과. 실제 API 호출은 0회다.

## 남은 범위

실제 전용 역할의 최소 권한 부여·제한 계정 종단 검증, 운영 설정·이미지 배포, 실제 본문 상한 확인과 API 비용 승인, 실제 Desktop·다기기·두 계정 검증은 남는다. 일반 문항 의미 검증과 빈칸형의 교육적 품질 실측도 별도다. 실제 설정·호출 전에 승인 범위를 안내하며 #593은 열어둔다.
