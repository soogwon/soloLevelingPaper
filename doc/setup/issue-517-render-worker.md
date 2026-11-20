# #517 Render Background Worker 준비

## 이번 범위

Render용 `render` Docker target과 기본 대기 진입점, linux/amd64 로컬 빌드 계획을 추가한다.
기존 기본 Docker target은 사전 검사 후 종료하며 Render는 --target render로만 선택한다.
실제 서비스 생성·이미지 게시·PDF 전송·AI 호출은 하지 않는다. 합성 프로세스와 이미지 CI 통과는
실제 모델 Linux 추론이나 종단 처리가 성공했다는 의미가 아니다.

## 실행 계약

- 이미지 시작 시 모델/runtime preflight를 자식 프로세스에서 실행한다. 실패하면 작업을 시작하지 않는다.
- 기본 `--mode standby`는 DB/R2/API 자격을 읽거나 작업을 claim하지 않고 종료 신호를 기다린다.
- `--mode verify-once --config /etc/secrets/verification.json`은 기존 bootstrap을 고정해 한 번 실행한다.
  runtime 설정 경로와 전용 secret, 외부 API 승인 플래그는 기존 bootstrap 계약을 그대로 요구한다.
- 정상/실패/not_claimed 모두 같은 프로세스에서 재시도하지 않고 대기로 돌아간다.
  서비스 재시작으로 다시 진입해도 DB의 지정 job attempt=0 조건이 중복 claim을 차단한다.
  이는 모든 운영 queue를 처리하는 상시 consumer가 아니다.
- SIGTERM/SIGINT를 받으면 새 실행을 중단하고 현재 자식 프로세스 그룹에 SIGTERM을 보낸다.
  5초 안에 끝나지 않으면 SIGKILL 후 wait로 회수한다. 강제 중단 시 lease 만료/세대 fencing에 의존한다.
  API 사용량이 불명확해질 수 있으므로 중단 작업을 새 attempt로 임의 재실행하지 않는다.
- 자식 stdout/stderr는 폐기하고 부모는 고정 상태만 출력한다. 원문/DSN/provider 예외를 로그에 내지 않는다.
  `render_ready`는 사전 검사 완료, `render_verification_finished`는 단발 CLI 성공이며 웹 검증 완료는 아니다.

## 이미지 준비

Render는 linux/amd64 이미지가 필요하다. Mac ARM64 패키지 목록을 Linux lock으로 재사용하지 않는다.
먼저 [오프라인 runtime 준비](issue-517-offline-runtime.md)에 따라 승인된 Python base와
전체 hash 고정 Linux CPU wheel로 runtime 이미지를 준비하고 실제 digest와 runtime-lock을 확보한다.
모델은 고정 revision과 model-lock으로 검증한다. 모델/자격을 Git에 넣지 않는다.

```sh
python deploy/worker/build_render.py \
  --runtime-image 'registry/worker-runtime@sha256:검증한64자리해시' \
  --model-dir /absolute/model/revision \
  --runtime-dir /absolute/runtime-manifest
```

기본은 JSON 명령 계획만 출력한다. 검토 후 `--execute`를 붙이면 BuildKit으로 linux/amd64의
`render` target을 `--load`하고, network=none/read-only로 preflight만 검사한다.
빌드 내부 RUN은 network=none이지만 Docker base/frontend 조회에는 네트워크가 필요할 수 있다.
이 도구는 wheel/model을 다운로드하거나 레지스트리에 push하거나 Render를 호출하지 않는다.
실제 이미지 import/모델 추론·메모리와 플랫폼 확인 후 별도 승인으로 레지스트리에 게시한다.

## Render 생성 직전 확인 — 여기서 사용자에게 비용 안내

Background Worker는 유료다. **생성/Deploy 버튼을 누르기 전에 표시된 사양·요금·지속 과금과
검증 후 중지 계획을 사용자에게 알리고 확인한다.** 이번 작업에서는 결제/서비스를 생성하지 않는다.

- Existing Image: 검증한 최종 worker 이미지 digest. runtime base digest와 구분한다.
- 이름 예: solo-leveling-worker-staging, DB와 같은 리전, 인스턴스 1개.
- 초기 제안: 2 CPU/4GB. 실제 Linux 모델/RSS 측정 후 결정하며 Mac 관측값으로 보장하지 않는다.
- Docker 기본 명령(standby)으로 먼저 모델/runtime 검사와 종료 동작을 확인한다.
- 아직 승인하지 않은 PDF/API 작업이 자동 시작되지 않도록 verify-once는 별도 승인 후 설정한다.
- 승인한 verification.json/runtime.json은 Render secret file로, DB/R2/API 자격은 비밀 환경변수로 주입한다.
  [bootstrap 설정](issue-517-verification-bootstrap.md)의 전용 변수·TLS·payload/토큰 상한을 지킨다.
- 대기 중에도 worker 비용이 발생한다. 검증 종료 후 서비스 중지 및 비용 상태 확인이 필요하다.

## 남은 준비와 역할

A: 실제 Linux wheel/base/runtime/final image digest, 레지스트리·Render 설정, 격리 DB/schema·migration,
R2 최소 권한, 동의·영속 예산/작업 접수, 배포·종료/lease 실측.
B: Linux 실제 모델 CPU/FP32 추론·메모리, 번역 모델·정확한 payload hash/입력 상한과 호출/비용 승인.
C: 합성 검증과 실제 성공 표시 구분, 상태 로그, 이후 웹/근거/두 계정 종단 확인.

공식 안내: https://render.com/docs/background-workers , https://render.com/docs/deploying-an-image
#517은 실제 종단 검증 전까지 열어둔다.

## 퀴즈 단발 실행과 시작 명령 (#593)

Render Existing Image의 Docker Command는 이미지의 CMD를 교체한다.
이 이미지의 ENTRYPOINT는 이미 `python -m solo_leveling.workers.render_service`다.
따라서 `env ... python -c ...` 또는 `python -m ...`를 Docker Command에 넣지 않는다.
실패한 긴 명령은 ENTRYPOINT의 잘못된 인자가 되어 종료 코드 2로 재현됐다.

퀴즈 실행 시 Docker Command에는 아래 **인자만** 지정한다.

```text
--mode quiz-worker --max-jobs 1 --shutdown-grace-seconds 30
```

환경 변수 `QUIZ_WORKER_CONFIG`(검토한 설정 파일의 절대 경로),
`QUIZ_WORKER_DATABASE_URL`, `QUIZ_WORKER_OPENAI_API_KEY`,
`QUIZ_WORKER_EXECUTION_ENABLED=true`가 별도로 필요하다.
verify-full 연결은 이미지의 CA 경로를 `PGSSLROOTCERT`로 지정한다.
새 역할·설정 파일·기능 실행은 해당 환경의 승인을 받은 뒤 적용한다.

사전 검사 후 고정 퀴즈 CLI를 자식으로 한 번 실행하며, 성공·실패 모두
재시도 없이 `render_standby`로 돌아간다. SIGTERM은 자식 프로세스 그룹에 전달되고
종료 유예 뒤에도 남으면 강제 종료·회수한다. 자식 원문 출력은 로그에 포함하지 않는다.
호스트가 프로세스 전체를 재시작하는 것은 별개이므로 영속 원장의 호출 제한도 유지한다.
검증 후 Docker Command를 비우고 실행 플래그를 false로 복원한다.

먼저 API 없는 대역으로 시작·완료·실패·standby·SIGTERM을 검증하고,
실제 실행에서는 worker 준비 전에 작업을 접수해 120초 기한을 소진하지 않도록 한다.
Live 표시는 생성 성공을 의미하지 않으므로 실행·provider attempt·사용량 원장을 확인한다.
실제 재생성은 별도 승인 없이 자동으로 재시도하지 않는다.

## 데모 PDF 상시 처리

Render 이미지 기본 명령은 standby입니다. 데모를 승인한 뒤에만
`--mode demo-pdf-worker`와 `SLP_DEMO_PROCESSING_ENABLED=true`를 함께 사용합니다.
Render에는 배포한 이미지와 모델 지문을 고정한 JSON을
`SLP_DEMO_PROCESSING_CONFIG_JSON`으로 설정합니다. 로컬에서는 같은 JSON 파일의 절대 경로를
`SLP_DEMO_PROCESSING_CONFIG`로 지정할 수 있습니다.
기존 단발 runtime 설정의 `scope_sha256`과 `requests` 대신 다음 항목을 넣습니다.

- `database_name`, `database_schema`와 데모 전용 `policy_version`, `window_key`.
- `demo_budget`에 시간대가 포함된 `starts_at`, `expires_at`, 프로세스당 `max_jobs`를 지정합니다.
- 같은 객체에서 `input_token_limit`, `input_overhead_tokens`, 입력·출력 각각의
  `*_microusd_per_million_tokens`, `owner_budget_microusd`, `service_budget_microusd`를 지정합니다.
  실제 활성화 전에 고정 번역 모델의 요율과 전체 요청 기준 입력 상한을 검증합니다.
  현재 구현은 직렬화된 요청의 UTF-8 바이트와 승인된 오버헤드로 예약하며,
  모델과 요청 형식에 대한 실제 상한 검증을 배포 전에 별도로 수행해야 합니다.
- 참가자별 owner 행과 service 행을 동일 정책·기간으로 준비합니다. 두 토큰 한도에
  각각의 단가를 곱한 합이 금액 상한을 넘으면 전송을 허용하지 않습니다.
- Render에 데모 전용 `SLP_DEMO_DATABASE_URL`, `SLP_DEMO_R2_ACCESS_KEY_ID`,
  `SLP_DEMO_R2_SECRET_ACCESS_KEY`, `SLP_DEMO_OPENAI_API_KEY`를 준비합니다.
  DB URL은 검증된 TLS와 제한 worker 역할, R2는 승인된 bucket의 읽기 자격을 사용합니다.

worker는 해당 기간에 접수된 미실행 작업만 가져옵니다. 이전 작업은 별도 실행 범위에서
처리합니다. 게시 성공 뒤 웹에서 해당 판본의 Claude 첫 학습 요청을 복사할 수 있습니다.

신규 접수를 중지하려면 운영자가 현재 generation을 확인한 뒤
`python -m solo_leveling.interfaces.cli.processing_intake_stop --schema <schema> --expected-generation <generation>`
을 실행합니다. 공용 접수 스위치이므로 질문·퀴즈 등 같은 제어를 사용하는 접수도
일시 중지됩니다. 기존 접수와 전송 제어는 별도로 유지됩니다. 이미 전송된 요청은
회수할 수 없으며, 신규 전송까지 멈춰야 하면 별도의 dispatch 제어를 사용합니다.
데모 종료 시 설정을 끄고 worker를 standby로 복원합니다.

분할 임베딩을 게시하는 제한 worker 역할에는 기존 게시 권한에 더해
`server_embedding_fragments`의 다음 열 권한이 필요합니다. 아래의 스키마와 역할은
대상 환경의 제한 계정에 맞게 지정하고, 배포 전에 해당 역할로 게시까지 리허설합니다.

```sql
GRANT SELECT (owner_user_id, embedding_set_id, embedding_fragment_id, chunk_id,
              token_start, token_end, parent_token_count, dimension, embedding),
      INSERT (owner_user_id, paper_id, server_version_id, parse_revision_id,
              translation_revision_id, embedding_set_id, chunk_id, embedding_fragment_id,
              token_start, token_end, parent_token_count, dimension, embedding),
      UPDATE (embedding)
ON slp_staging.server_embedding_fragments TO slp_staging_worker;
```

`UPDATE (embedding)`은 동일한 조각의 멱등 삽입에 사용됩니다. 삭제·DDL 권한은
필요하지 않습니다. 이 권한의 운영 적용과 worker 활성화는 별도 승인 범위입니다.

## 일반 계정의 자동 게시 준비

웹의 `REMOTE_AUTOMATIC_PDF_BUDGET_JSON`에 `policy_version`(`pdf-auto-` 접두사),
`window_key`, 시간대가 포함된 `starts_at`·`expires_at`, 계정별 `input_tokens`·`output_tokens`를 설정합니다.
제한 검증에서는 `allowed_owner_ids`와 `max_jobs_per_owner`로 검증 대상을 고정합니다.
같은 정책·기간의 서비스 예산 행은 운영자가 미리 설정합니다. 인증된 첫 웹 로그인에서
계정 예산이 생성되고, 이미 로그인한 계정은 접수 시 생성됩니다. 로그인·재접수는 기존
한도와 사용량을 초기화하지 않습니다.

worker의 기존 처리 설정에 동일한 정책·기간·시간 범위를 사용하고
`SLP_AUTOMATIC_PDF_PROCESSING_ENABLED=true`로 설정합니다.
worker는 해당 정책과 기간으로 명시 접수된 작업만 소비합니다.
비용 제한은 전송 시 계정과 서비스 원장 행을 모두 잠근 뒤 각각 적용합니다.
기간이 끝나면 다음 승인 기간·서비스 예산을 준비하고 웹과 worker를 함께 전환합니다.

스테이징 적용은 migration 0068을 먼저 반영한 뒤 두 서비스를 배포합니다.
웹 역할은 예산 조회·계정 예산 삽입만 필요하며 사용량 갱신은 worker 역할에서 수행합니다.
별도 자동 게시 허용 없이 기존 접수 계약을 사용하는 환경은 두 설정을 비워 둡니다.

## 첫 원격 MCP 계정 연결

migration 0069는 검증된 웹 로그인 식별자와 제한된 연결 함수를 추가합니다.
웹과 원격 MCP의 OIDC issuer가 정확히 같고 제품 조회가 활성화된 환경에서만
`REMOTE_MCP_AUTO_ACCOUNT_LINK_ENABLED=true`를 설정합니다. 기존 웹 계정은 활성화 후
웹에서 한 번 다시 로그인하면 이전 OAuth 로그인에 누락됐던 검증 기록이 생성됩니다.
이후 Claude에서 `papers:read`로 첫 논문 조회를 하면 계정 연결이 자동으로 생성됩니다.
토큰의 이메일이나 표시 이름은 연결에 사용하지 않습니다.

승인된 환경의 스키마 소유자가 해당 웹 역할에 증거 행 삽입만, MCP 역할에는 연결 함수의
실행 권한만 추가합니다. MCP 역할은 웹 인증 증거 테이블을 직접 읽거나 쓸 필요가 없습니다.

```sql
GRANT INSERT (issuer, subject) ON slp_staging.server_verified_web_identities TO slp_staging_web;
GRANT EXECUTE ON FUNCTION slp_staging.server_link_verified_mcp_account(text, text) TO slp_staging_mcp;
```

중단하려면 플래그를 끄고 웹을 재배포한 뒤 신규 권한을 회수합니다.
이미 기록된 계정 연결과 폐기 이력은 그대로 둡니다.

웹 연결 관리 화면에서 원격 MCP의 활성 연결을 표시하려면 웹 역할에
필터와 날짜 표시에 사용하는 세 열의 조회 권한을 추가합니다.

```sql
GRANT SELECT (owner_user_id, linked_at, revoked_at)
ON slp_staging.server_remote_identities TO slp_staging_web;
```

일회용 연결 확인을 배포하기 전에 migration 0070을 적용하고 전용 역할에
다음 권한을 추가합니다. 확인 코드는 발급 응답에만 포함되며 DB에는 해시로 저장합니다.

```sql
GRANT SELECT, INSERT, UPDATE ON slp_staging.server_remote_connection_checks TO slp_staging_web;
GRANT SELECT (owner_user_id, code_hash, expires_at, verified_at), UPDATE (verified_at)
ON slp_staging.server_remote_connection_checks TO slp_staging_mcp;
```
