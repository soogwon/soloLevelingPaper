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
