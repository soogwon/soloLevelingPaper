# 질문 생성용 고정 설정과 전용 DB 권한

## 범위

`solo_leveling.workers.question_bootstrap:factory`를 #570의 질문 worker runtime으로 지정한다. 기본 실행은 꺼져 있으며 이번 검증은 로컬 임시 PostgreSQL/pgvector와 합성 HTTP·임베딩 대역만 사용했다. 실제 staging 변경, 모델 다운로드, PDF 전송, API 호출은 하지 않았다.

최초 검증은 semantic verification을 비활성화한 단일 질문 생성 경로다. 모델명/정책/예산에 운영 기본값을 넣지 않는다. 정확한 전송 payload 해시와 검토된 입력 토큰 상한이 없는 요청·보완 요청은 전송 전에 거부한다. 범용 토큰 추정기나 불특정 질문을 계속 처리하는 운영 모드는 아니다.

## 설정 계약

`QUESTION_WORKER_CONFIG`에 비밀정보 없는 JSON 파일의 절대 경로를 지정한다. 중복 키·알 수 없는 필드·정책 불일치·유효하지 않은 수치/경로를 거부한다.

| 필드 | 내용 |
| --- | --- |
| schema_version | 정수 1 |
| database_name / schema_name / database_role | 승인한 DB·schema·전용 비특권 역할 이름 |
| policy | version, timeout_seconds, stage_timeout_seconds, max_calls, max_input_tokens, max_output_tokens |
| generation | 승인한 model, timeout_seconds, max_output_tokens |
| provider_profile_id / window_key | 웹 접수 snapshot 및 예산 행과 일치하는 승인 프로필·기간 |
| runtime_image | digest로 고정한 worker runtime image |
| runtime_lock_path / model_lock_path / model_dir | 이미지에 포함한 고정 artifact의 절대 경로 |
| embedding_spec | 게시 벡터와 동일한 EmbeddingSpec 전체 fingerprint |
| requests | max_requests, max_input_tokens, max_output_tokens, payloads(정확한 payload SHA256 → 승인된 입력 토큰 상한) |

환경변수는 `QUESTION_WORKER_EXECUTION_ENABLED=true`, `QUESTION_WORKER_DATABASE_URL`, `QUESTION_WORKER_OPENAI_API_KEY`를 별도로 명시한다. 웹 DATABASE_URL이나 일반 OPENAI_API_KEY를 대신 읽지 않는다. DSN의 DB·사용자·TLS 조건과 연결 후 DB/schema/역할을 대조하고 superuser·CREATEDB·CREATEROLE·BYPASSRLS·INHERIT 역할은 거부한다. 로컬 loopback 외에는 verify-full TLS만 허용한다.

```text
python -m solo_leveling.workers.question_bootstrap --config /secure/question.json
python -m solo_leveling.workers.question_bootstrap --config /secure/question.json --check-files
```

첫 명령은 설정만 검사한다. 두 번째는 설치 metadata·모델 파일을 오프라인 검사하며 DB/API 연결을 하지 않는다. 실제 실행은 아래 명령과 환경변수가 모두 준비된 후에만 가능하다.

```text
python -m solo_leveling.workers.render_service --mode question-worker --runtime solo_leveling.workers.question_bootstrap:factory --max-jobs 1
```

해당 정책 version의 작업만 선택하며 실제 정책 snapshot 전체도 대조한다. 다른 정책의 queued/철회 작업은 해당 실행 worker가 종료하지 않는다. 공통 정리 worker는 별도로 유지한다.

## 승인 전 본문 준비

내부 함수 `preview_payload(pool, plan, owner, execution_id, embedding_runtime)`는 queued 질문의 고정 입력을 검색하여 최초 API 본문을 메모리에 반환한다. API 키·HTTP 전송·claim·예산 예약 변경·DB 쓰기를 하지 않는다. 실행별 UUID에서 결정적으로 근거 ID를 만들어 같은 입력의 미리보기와 실제 전송 해시가 일치한다. 실행 간 ID는 분리된다.

반환값에는 질문·원문·번역 근거가 있으므로 로그·GitHub·일반 파일에 기록하지 않는다. 승인된 로컬 검토 절차에서 내용과 안전한 토큰 상한을 확인하고 `payload_digest(payload)`와 상한만 설정에 넣는다. 검색 결과·모델·프롬프트가 달라지면 미리보기/승인을 다시 준비한다. 설정을 변경하지 않은 상태에서 해시 불일치를 무시하거나 자동 재승인하지 않는다.

미리보기는 동의를 대신하지 않는다. 실제 전송 직전 기존 영속 gate가 현재 동의·기한·lease·예산을 다시 검사한다. API 응답에 따라 생성되는 보완 본문은 최초 승인에 포함되지 않으므로 자동 전송하지 않는다.

## DB 권한

관리자 연결에서 **새 역할**을 NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS로 생성한 뒤 `grant_question_execution_role(conn, schema, role)`을 적용한다. 기존 웹/검토/처리 worker 역할을 재사용하지 않는다. 이 함수는 역할 생성·비밀번호 발급·기존 권한 회수를 하지 않으므로 기존 광범위 권한 역할에 적용하는 것만으로 최소 권한이 되지 않는다.

- 조회: 고정 context·논문/게시·파싱/번역 revision·청크/벡터, 실행/동의/제어/예약·사용량.
- INSERT: provider attempt, 답변 근거만 허용. 질문 접수·동의 생성·원문 게시 권한 없음.
- UPDATE: 실행/lease/결과, 예약·소비 토큰, attempt 정산 열만 허용. 정책·최대 예산·출처 내용 변경 불가.
- 잠금용 UPDATE: 논문/게시/revision/동의/제어의 ID 열은 PostgreSQL 행 잠금에만 사용한다. 역할별 trigger가 실제 UPDATE를 거부한다.
- 웹 세션 접근, 원문/벡터 수정, 동의 변경, 원장 삭제, schema DDL은 부여하지 않는다.

이 역할은 여러 사용자의 질문을 처리하는 신뢰된 서버 역할이다. SQL 자격 자체가 사용자별 RLS 격리를 제공한다는 뜻은 아니며 자격을 클라이언트에 노출하지 않는다. owner/context/lease 격리는 기존 서버 계약과 별도 두 사용자 종단 검증으로 확인한다.

## 검증과 다음 승인 범위

로컬 제한 역할에서 성공·전송 불명확·동의 철회, 검색/답변 저장/예산 정산, 금지 SQL, 미리보기 무전송·무쓰기 및 실제 bootstrap 전송 본문 일치를 검증한다. 모델 파일·PyTorch 실행은 기존 검증을 재사용하며 이 테스트의 모델/HTTP는 대역이다.

다음 staging 승인은 다음 내용을 구체화한 뒤 받는다.

1. 새 질문 실행 역할 생성, 위 GRANT/잠금 trigger 적용, 전용 자격 암호화 보관/Render 입력. 신규 migration·데이터 삭제 없음.
2. 고정 이미지·모델 fingerprint, 새 생성 profile/정책/window, 웹 접수 설정 일치 및 owner/service 예산 행 적용. 기존 no-dispatch 작업 재사용 금지.
3. 승인할 합성 질문/선택 원문·번역/모델·정확한 payload 해시·최대 호출/입출력 토큰·비용을 제시. 미리보기 접근 범위도 명시한다. 이 문서는 실제 외부 전송 승인이 아니다.
4. worker 1건 실행 후 standby 복원. 실패·unknown이면 재시도/임의 환불 없이 기록한다. 문제 시 실행 flag를 끄고 새 자격/권한을 회수하며 미확정 원장은 유지한다.
5. Desktop 답변·인용·재조회, 실제 예산 정산과 두 사용자 격리를 추가 확인한다. #395/#396/#399는 아직 닫지 않는다.

## 일반 질문 bootstrap v2와 입력 토큰 계수 (2026-11-18)

이번 추가는 코드·합성 HTTP·로컬 임시 PostgreSQL 검증이다. 실제 staging migration/권한,
Render 설정, 입력 계수·생성 API 호출은 하지 않았다. 기존 v1 고정 payload 방식은 유지한다.

### 계산 방식과 외부 전송 경계

[OpenAI 입력 토큰 계수 안내](https://developers.openai.com/api/docs/guides/token-counting)는
메시지 경계와 schema 토큰까지 포함하는 서버 계수와 일반 텍스트 로컬 토크나이저를 구분한다.
따라서 임의 글자 수나 로컬 토큰 수에 여유분을 더해 운영 비용의 확정 상한으로 사용하지 않는다.
v2는 `/v1/responses/input_tokens` 결과를 같은 실행의 고정 생성 본문 SHA256·profile에 묶는다.
계수에는 생성 본문과 같은 model/instructions/input/text를 보내고 생성 전용 store/max_output_tokens는 보내지 않는다.
PDF·계정 정보·DB 자격은 본문에 추가하지 않는다. 질문·선택 원문/번역 근거 자체는 외부로 전송된다.

이 경로는 **계수 최대 1회 + 생성 최대 1회**로 총 HTTP 최대 2회다. 이전 생성 1회 승인을 계수 승인으로 재사용하지 않는다.
계수는 생성 호출/유료 토큰 원장과 별도 예약하며, 실제 계수 비용·해당 모델 지원은 활성화 전에 공식 안내와 계정에서 재확인한다.
실측 전이므로 대역 성공을 실제 제공자의 계수와 생성 사용량 일치 검증으로 간주하지 않는다.

### v2 설정

공통 DB/모델/runtime 경로는 v1과 같다. `schema_version=2`에서 `requests` 대신 아래 두 필드가 필요하다.

- `contract`: `QuestionContract`의 모든 필드. model/policy_version/window_key/estimator_revision/price_revision,
  max_input_tokens/max_output_tokens/max_payload_bytes, 입력·출력 토큰당 USD nanos, 질문/사용자/서비스 USD nanos 상한.
- `counting`: `{"timeout_seconds": 정수}`만 허용. 1~30초이면서 질문 stage timeout 이하여야 한다.
- `estimator_revision`은 `responses-input-tokens-v1`로 고정하고 provider_profile_id는 contract.profile과 일치해야 한다.
- 모델·정책·입출력 상한·profile 불일치, v1/v2 필드 혼용을 시작 전에 거부한다.
- `QUESTION_WORKER_EXECUTION_ENABLED=true`와 별도로 `QUESTION_WORKER_TOKEN_COUNT_ENABLED=true`가 필요하다.
  설정 검사와 bootstrap 시작 자체는 계수나 생성을 호출하지 않는다. 기본값은 비활성이다.

### 동의·원장·권한

계수는 새 `question-count-and-generation-v1` 고지가 있는 두 목적의 동의만 허용한다.
기존 웹 고지는 자동 변경하지 않았다. 새 고지/전송 시점/계수 포함 호출 수를 제품 동의 화면에 연결하고 재동의해야 한다.
현재 고지로 생성된 작업은 v2 계수 전에 차단된다.

migration `0042_question_token_counts.sql`은 원문 없이 owner/execution/lease/profile/payload hash/status/input token count만 기록한다.
전송 전에 실행·기한·lease·논문 삭제·현재 동의·dispatch 중지·접수 예산·잠긴 owner/service 상한을 검사하고 예약을 commit한다.
외부 호출 중 DB 잠금을 유지하지 않으며, 응답 후 다시 검사한다. 생성 직전에는 기존 영속 전송 gate가 다시 검사한다.
예약 후 응답 유실·형식 오류·상한 초과는 재시도하지 않는다. 같은 실행의 같은 본문·profile에 대한 관측된 계수는
동의/기한이 유효할 때 재사용하며 다른 본문은 거부한다. 동시 계수는 한 요청만 보내고, 중복 요청은 최초 예약을 덮어쓰지 않는다.
재시작 후 reserved/unknown 계수는 자동 재전송하지 않는다. 실제 생성이 없었던 계수 실패는 유료 생성 사용량으로 정산하지 않는다.

`grant_question_token_count_role`은 기존 질문 실행 역할에 새 원장의 SELECT/INSERT와 UPDATE(status,input_tokens)만 추가한다.
기존 GRANT 함수는 계수 권한을 자동 부여하지 않는다. v2 시작 시 계수 SELECT/INSERT/정산 UPDATE 권한이 있고 DELETE·profile/hash UPDATE 권한이 없는지 확인해, 권한이 없거나 과하면 claim 전에 중단한다. DELETE·profile/hash/실행 식별자 수정은 허용하지 않는다.
허가된 기존 논문/실행 물리 삭제 시 계수 메타데이터도 FK cascade로 제거한다. 이번 작업에서는 실제 삭제를 실행하지 않는다.

### 다음 적용 승인 범위

1. 제품 동의 화면에 계수와 생성의 외부 전송 시점·본문·최대 요청 수를 표시하고 새 고지/재동의를 연결하는 코드 작업.
2. 검토한 이미지 digest 게시/교체, staging migration 0042와 기존 전용 역할의 계수 원장 권한 추가.
3. 새 v2 contract/profile/정책/window와 예산, 별도 계수 활성화 flag. 기존 고지/작업/사용량은 수정하지 않는다.
4. 승인한 합성 질문·근거를 계수 1회와 생성 1회 보내는 범위 및 생성 비용 상한을 별도로 승인받는다. 이번 호출 수는 0회다.
5. 문제 시 v1/이전 이미지·standby·두 실행 flag=false로 복원하고 계수/생성 원장은 보존한다.

A: migration·제한 역할·동시 예약·기한/동의/lease 검사. B: 같은 본문의 계수와 생성 연결·해시 고정·비용 상한.
C: 기존 고지 차단·다음 동의 화면 연결·실제 Desktop 조회가 남았음을 구분한다. 관련 티켓은 닫지 않는다.
