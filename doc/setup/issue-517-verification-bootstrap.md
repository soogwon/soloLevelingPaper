# #517 검증 worker bootstrap

기존 단발 진입점의 runtime factory로 `solo_leveling.workers.verification_bootstrap:factory`를 제공한다.
실제 실행할 때만 DB·R2를 조립하며 예산 행·동의·접수·모델을 자동 생성하거나 다운로드하지 않는다.
이번 검증은 합성 대역과 격리 PostgreSQL이며 실제 배포·PDF 전송·AI 호출·모델 추론은 없다.

## 준비 파일

1. [단발 대상 설정](issue-517-verification-runner.md)의 verification.json.
2. runtime.json: 아래 필드를 모두 지정한다. 알 수 없는 필드·중복 JSON 키는 거부한다.
3. 고정 모델 파일 및 model-lock.json, 실제 Linux 이미지의 runtime-lock.json.

| runtime 필드 | 내용 |
| --- | --- |
| schema_version | 정수 1 |
| scope_sha256 | verification.json을 아래 함수로 정규화한 해시. 다른 owner/job/DB/schema/한도로 재사용 금지 |
| runtime_image | 검토한 Linux 이미지 name@sha256:digest |
| runtime_lock_path / model_lock_path / model_dir | 절대 로컬 경로. 모델 폴더 이름은 고정 revision |
| embedding_spec | 기존 EmbeddingSpec 전체 필드. CPU PyTorch float32/l2/cosine 및 모델 지문 |
| limits | max_pdf_bytes, max_pages, max_chunks 양의 정수 |
| translation | model, timeout_seconds, max_output_tokens. 모델/한도는 승인값을 직접 입력 |
| batch_size | 실제 메모리 검증 후 정한 양의 정수 |
| policy_version / window_key | 이미 마련된 영속 예산 정책과 window. bootstrap은 새 예산을 만들지 않음 |
| r2 | endpoint=https://ACCOUNT_ID.r2.cloudflarestorage.com, bucket, region=auto |
| requests | max_requests, max_input_tokens, max_output_tokens 및 payloads: {정확한 payload SHA-256: 검토된 입력 토큰 상한} |

scope_sha256은 `verification_bootstrap.scope_digest(load_config(path))`로 오프라인 계산한다.
API payload 해시는 `gated_responses.payload_digest(payload)`와 동일한 전체 요청 정규화 규칙을 사용한다.
본문 대신 해시/상한을 승인 파일에 기록한다. 원문·지시·JSON schema·모델·출력 한도가 바뀌면 해시도 달라진다.

## 입력 토큰 상한의 의미

`ApprovedPayloadBounds`는 **토큰 추정기가 아니다**. 모델별 tokenizer와 전체 요청 overhead를 검토한
안전한 입력 상한을 운영자가 사전 제공해야 한다. 코드가 숫자의 안전성을 계산·증명했다고 간주하지 않는다.
미등록 payload나 다른 모델은 전송 전에 거부한다. 호출별 예약 전에 요청 수·입력 상한 합계·최대 출력 합계를
동시에 제한한다. 후속 영속 예약 거절도 단발 한도에서 환급하지 않는다.

서비스 번역 재시도는 1회, 추가 batch retry는 0회다. R2 SDK 숨은 재시도도 끈다.
이 단발 보조 한도는 달러 비용/사용자·서비스 전체 예산을 대체하지 않는다. 기존 영속 원장이 계속 검사한다.
실제 사용량이 상한을 넘거나 unknown이면 기존 원장 계약대로 후속 처리를 차단한다.
검증 설정을 바꿔 같은 job attempt를 초기화하거나 중복 전송을 우회하지 않는다.

## 오프라인 검사

```sh
python -m solo_leveling.workers.verification_bootstrap \
  --config /secure/verification.json --runtime-config /secure/runtime.json
```

자격을 읽거나 runtime 모델을 import하지 않는다. `--check-files`를 더하면 파일 해시와 Linux 패키지
metadata를 검사하지만 DB/R2/API에는 접속하지 않는다. 이 성공은 실제 loader 호환·추론·처리 성공이 아니다.

## 검증 전용 secret

- `SLP_VERIFY_ALLOW_EXTERNAL_API=true`, `SLP_VERIFY_LOCAL_ONLY=false`: 승인 후 명시적으로 설정. 누락/불일치 시 연결 전 거부
- `WORKER_BASE_REFERENCE`: 이미지가 고정한 base digest. runtime 설정과 일치해야 함
- `SLP_VERIFY_RUNTIME_CONFIG`: runtime.json의 절대 경로
- `SLP_VERIFY_DATABASE_URL`: 검증 DB 전용 DSN. DB 이름이 대상 설정과 일치해야 함
- `SLP_VERIFY_R2_ACCESS_KEY_ID`, `SLP_VERIFY_R2_SECRET_ACCESS_KEY`: 검증 버킷의 최소 읽기 자격
- `SLP_VERIFY_OPENAI_API_KEY`: 승인된 검증용 서비스 키

일반 DATABASE_URL/OPENAI_API_KEY/AWS 기본 자격으로 대체하지 않는다. secret은 이미지·JSON·Git에 넣지 않는다.
외부 DB는 sslmode=verify-full을 요구한다. 로컬 loopback만 TLS 없는 검증 DSN을 허용한다.
DSN query의 host/dbname/options/service 등 대체 접속 설정은 거부한다.
DB pool은 dict_row, 전용 schema 단독 search_path, max_size=4, 연결/대여 timeout을 고정한다.
모델/런타임 확인 → secret 검증 → 닫힌 pool 생성 → cleanup 등록 → pool open → 실제 DB/schema 확인 → R2 client 순이다.
pool open 실패·DB/schema 불일치·client 생성 실패·실행 오류·client close 실패에도 pool close를 시도한다.
R2 client가 생성된 뒤에는 먼저 닫고 pool을 닫는다. 상위 CLI는 원시 로그를 폐기하고 고정 오류만 출력한다.

## 승인 후 실행 형태

```sh
python -m solo_leveling.interfaces.cli.processing_verification \
  --config /secure/verification.json --execute \
  --runtime solo_leveling.workers.verification_bootstrap:factory
```

이 명령은 실제 객체 다운로드와 유료 번역을 유발할 수 있다. 이번 작업에서는 실행하지 않았다.
이미지 기본 entrypoint는 그대로 사전 검사다. 운영자가 승인된 실행 명령을 명시해야 한다.

## 역할별 검증과 남은 환경

- A: 실제 PostgreSQL pool의 DB/schema 확인과 오류 시 close, 전용 자격·TLS·접속 옵션 및 자원 수명.
- B: 정확한 payload별 승인 상한과 전체 한도, 실제 모델/런타임 preflight 연결, 추가 자동 재시도 금지.
- C: 기본 오프라인 검사, 고정 오류, 전송 승인과 실행 옵션의 구분. config/files 검증을 종단 성공으로 표시하지 않음.

남은 준비: 실제 Linux 전체 의존성 lock/이미지 digest·모델 파일과 CPU/메모리 실측, 검증 전용 DB/schema
및 migration·R2 자격·영속 예산 정책, 실제 PDF별 payload 해시와 검증된 토큰 상한, 호출/비용/보관 승인.
그 후 단발 실행과 원문·번역·벡터·게시·웹 결과를 확인한다. 운영 강제 종료·재개·번역 재사용은 후속이다.
파일/manifest/서버 설정은 신뢰하는 운영 입력이며 이 bootstrap은 임의 운영 코드를 격리하는 sandbox가 아니다.
#517은 실제 종단검증이 남아 있어 닫지 않는다.
