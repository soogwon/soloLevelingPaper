# #594 복구 worker 연결

Refs #594. staging 변경·외부 API 호출 없이 구현한다.

## 연결 범위

- A: 기존 검토 원장의 owner/request key/input hash와 현재 설정이 일치하는 **한 건**만 명시적으로 실행한다. migration 0060에 실행 세대/lease, 시도 해시/사용량, 게시 전 결과를 추가한다. 동시 claim·취소/철회·삭제·판본 변경·만료 뒤의 늦은 완료를 차단한다.
- B: 재파싱은 비공개 R2 PDF의 실제 길이/해시를 고정값과 비교하고 기존 제품 파서로 로컬 추출한다. 이번 재파싱에는 번역 API 호출이 없다. 번역 복구는 고정 원문의 누락/실패/original_only만 기존 OpenAI 번역 어댑터로 처리하며 성공·참조·번역 불필요 구간을 보존한다. 명백한 번역 품질 실패는 원문 전용으로 저장한다.
- C: 웹에서 실행 승인 대기/처리 중/처리 중단/결과 저장·새 색인 게시 대기를 구분한다. 조회는 실행하지 않는다. 취소는 현재 세대를 무효화한다.

`staged`는 원장에 새 파싱·번역 결과가 저장됐다는 뜻이며 정규 테이블·임베딩·publication 게시 완료가 아니다. 기존 context, active publication, 과거 근거는 변경하지 않는다. 결과 원문은 상태 API에 반환하지 않는다.

## 실행과 기본 차단

```sh
python -m solo_leveling.interfaces.cli.reprocessing_worker --execute
```

`--execute`와 `REPROCESSING_WORKER_EXECUTION_ENABLED=true`가 모두 필요하다. 연속 큐 소비나 자동 재시도는 없다. 종료 신호는 후속 처리를 중단하고, 만료 세대는 다음 명시 실행에서 검사한다. 전송 시도가 있으면 자동 복구/재전송하지 않고 실패 상태로 남긴다. 전송 전 중단만 새 lease로 재개할 수 있다.

필수 환경변수:

- `REPROCESSING_WORKER_CONFIG`: 로컬 실행 계획 JSON 경로
- `REPROCESSING_WORKER_DATABASE_URL`: 전용 비특권 NOINHERIT 계정. 원격은 `sslmode=verify-full`, 계획의 DB/역할/schema와 일치해야 한다.
- 재파싱만: `REPROCESSING_R2_ACCESS_KEY_ID`, `REPROCESSING_R2_SECRET_ACCESS_KEY` (대상 비공개 버킷 읽기 전용)
- 번역 복구만: `REPROCESSING_OPENAI_API_KEY`

계획 JSON의 정확한 최상위 키는 `database_name`, `database_role`, `schema_name`, `approval`, `config`, `r2`, `translation`이다.

- `approval`: `owner`, `request_key`, `input_hash` 필수. `lease_seconds`(기본 120), `max_pdf_bytes`(20MB), `max_pages`(500), `max_chunks`(10000) 선택. hash는 접수 당시 snapshot+contract의 고정 해시이며 서버 운영자가 검토한 한 건에만 부여한다. 웹 접수 자체가 실행 승인을 부여하지 않는다.
- `config`: 웹에서 사용한 동일 계약 입력 11개 필드. 변경되면 기존 작업을 실행하지 않는다.
- `r2`: 재파싱은 `endpoint`, `bucket`, `region`; 번역 복구만 하면 null. endpoint는 HTTPS Cloudflare R2 계정 주소만 허용한다.
- `translation`: 재파싱은 null. 번역 복구는 `timeout_seconds`, `max_output_tokens`, `requests`. requests는 기존 `ApprovedPayloadBounds` 형식(`max_requests`, `max_input_tokens`, `max_output_tokens`, `payloads={정확한본문SHA256:검토한입력상한}`)이다. 본문 해시/상한은 별도 오프라인 준비 및 실제 전송 승인 대상이며 자동 추정하거나 넓은 기본값을 부여하지 않는다.

번역은 물리 요청 전 동의·판본·lease·전체 중단 flag·호출/토큰 상한을 재검사한다. 영속 시도 기록과 전체 계약 상한의 보수적 소비 처리를 커밋한 뒤 전송한다. 성공하고 모든 usage가 확인된 결과만 실제 사용량으로 정산한다. 오류·전송 불명확·전송 중 취소는 승인 상한을 보존하며 후속 정산이 필요하다. 이는 실제 청구액이 아니라 중복 소비를 막는 내부 예산 처리다. provider가 승인 입력 상한을 초과하면 관측값/초과분을 기록하고 결과 저장·추가 전송을 차단한다. 이미 진행 중인 HTTP 요청 자체를 되돌릴 수는 없다.

## 필수 검증

- 실제 로컬 PostgreSQL: 원자 claim, lease 세대 교체/늦은 worker, 취소·철회·삭제·판본 변경, 프로세스 중단 후 재전송 차단, 예산 반환/보수적 차감/초과 관측.
- 합성 PDF를 기존 제품 파서로 실제 추출하고 이전 게시본 유지 확인.
- 기존 OpenAI 어댑터에 HTTP 대역을 주입해 누락 1건만 복구하고 기존 7건 유지 확인. 승인 payload 외 전송은 거부.
- CLI 기본 차단, 잘못된 DB/TLS/설정 거부, 원본 예외·자격 출력 차단.
- 웹 결과 상태·같은 키 복구·로그아웃 뒤 늦은 응답 차단.

## 남은 범위와 적용 전 승인

1. 정규 파싱/번역 테이블과 #587 fragment/임베딩 연결, 새 색인의 검증·원자 게시 및 명시적 선택. 이번 staged 결과는 이를 위한 입력이다.
2. #595 공통 MCP 작업 목록/상태 연결, 무인 만료 정리, 불명확한 호출의 별도 정산/복구 정책.
3. staging migration 0060, 전용 worker 역할 최소 권한 실측(복구 원장 SELECT/UPDATE, 입력·동의·context·게시·PDF 조회, 예산 SELECT/UPDATE; 정규 결과 테이블 쓰기 권한은 불필요), 이미지 게시·배포·실행 설정. 이번에는 적용하지 않는다.
4. 정확한 전송 payload·단가·호출 수·비용 한도 승인 후 실제 R2/worker/API·Desktop 종단 검증. 이전 유료 호출 승인을 재사용하지 않는다.

#594 완료 기준은 아직 충족하지 않으므로 티켓을 닫지 않는다.
