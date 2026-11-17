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
