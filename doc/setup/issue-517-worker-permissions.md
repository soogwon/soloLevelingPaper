# #517 제한 worker 계정의 DB 권한 계약

2026-11-17, main `9fadf7e` 기반 로컬 PostgreSQL 18.7 / pgvector 0.8.7에서
검증했다. 실제 staging·Auth0·Render·R2 설정은 변경하지 않았다.

제한 계정 신규 5개를 포함해 관련 회귀 테스트 **127개 통과**, Ruff와 diff 검사 통과.

## 확정한 권한 범위

현재 `verification.run_once` → 제품 worker의 승인 작업 실행·정산·게시 경로에
필요한 테이블/열 권한을 `tests/fixtures/worker_permissions.py`에 명시했다.
이 파일은 새 역할을 사용하는 로컬 리허설 계약이며 운영 적용 CLI가 아니다.
`GRANT ALL`, 전체 테이블 UPDATE/DELETE, schema CREATE, 소유권·관리자 역할은 주지 않는다.
역할은 LOGIN / NOSUPERUSER / NOCREATEDB / NOCREATEROLE / NOINHERIT / NOBYPASSRLS다.

| 대상 | 권한 |
| --- | --- |
| processing_jobs | SELECT, 상태·시도·재시도·lease·heartbeat·완료·오류·수정 시각 열 UPDATE |
| processing_versions | SELECT, active_publication_id UPDATE |
| processing_result_staging | SELECT, INSERT |
| processing_provider_attempts | SELECT, INSERT, 상태·실사용 토큰·정산 시각 UPDATE |
| dispatch_budget_limits | SELECT, 예약·소비 토큰·수정 시각 UPDATE |
| parse_revisions / translation_revisions / embedding_sets | SELECT, INSERT, status·completed_at UPDATE |
| processing_chunks / translation_chunks | SELECT, INSERT |
| embedding_vectors | SELECT, INSERT, embedding UPDATE (멱등 UPSERT에 필요) |
| processing_publications | SELECT, INSERT, staging_payload_sha256 UPDATE |
| papers / processing_job_authorizations / remote_upload_intents / pdf_uploads / remote_upload_consent_bindings / remote_consents / dispatch_controls | SELECT 및 아래의 잠금 전용 열 UPDATE |

테이블 이름에는 모두 `server_` 접두사가 붙는다. 정확한 열 목록은 계약 파일의
`UPDATE`, `LOCK_ONLY`에 있다. 현재 경로는 sequence를 쓰지 않으므로 sequence 권한도 없다.
기존 migration 함수·pgvector 함수의 EXECUTE 기본 권한은 유지한다. 함수별 EXECUTE 축소는
이번 테이블/열 계약 범위 밖이며, 전역 카탈로그 접근을 차단한다는 의미도 아니다.

## 잠금 전용 UPDATE와 변경 차단

PostgreSQL의 `SELECT ... FOR SHARE/UPDATE`는 SELECT 외에 최소 한 열의 UPDATE
권한이 필요하다. 동의·입력·제어 행은 잠그되 변경하지 않아야 하므로:

1. `LOCK_ONLY`의 식별자 한 열에만 UPDATE를 부여한다.
2. 해당 worker 역할의 UPDATE는 관리자 소유 BEFORE UPDATE 트리거가 SQLSTATE 42501로 거부한다.
3. 실제 worker의 SELECT 행 잠금은 정상 통과한다.

**GRANT만 떼어 적용하면 안 된다. 트리거와 묶인 계약이다.** worker는 테이블·함수의
소유자가 아니며 트리거 해제나 `session_replication_role=replica` 변경도 거부된다.
동의 철회·예산 상한·dispatch_enabled 변경 권한은 없다. 관리자 동의 철회는 정상 작동한다.

이 역할은 서비스 worker이므로 여러 사용자의 처리 행을 읽고 갱신할 수 있다.
DB 사용자별 RLS 격리 또는 침해된 worker의 usage 조작 방지까지 보장하는 계약은 아니다.
현재 앱의 owner/lease 조건·무결성 트리거를 함께 사용한다.

## 실행 검증

`tests/integration/test_worker_permissions_postgres.py`에서 새 LOGIN 역할로 접속해:

- 실제 claim → 입력·동의 검증 → heartbeat → 제공자 예약·사용량 정산 → staging 저장
  → 원본/번역/벡터 정규 테이블 → publication·active pointer·작업 완료를 실행한다.
- 정산을 두 번 호출해 중복 소비가 없는지 확인한다.
- 같은 작업 재실행은 not_claimed, 게시 결과는 한 번만 남는다.
- 사용량 unknown은 예약을 유지하며 게시하지 않는다.
- 처리 중 관리자 동의 철회는 게시를 막는다.
- 웹 세션·기기 자격 조회, DDL, 작업 삭제·owner 변경, 예산 상한 변경,
  잠금용 식별자 UPDATE, 트리거 해제를 차단한다.
- 추가로 실제 합성 2페이지 PDF 파서와 `ProcessingStagesFactory`를 사용해
  제품 번역 gate·임베딩 adapter·DB 게시까지 실행한다. HTTP와 모델만 대역이다.
  번역 시도 2건 observed, 벡터 2건, staging 1건과 active pointer를 확인한다.
  requests의 실제 전송은 테스트에서 차단한다.

DB 권한 검증의 접수·동의·예산 seed와 관찰은 관리자 연결로 수행하고,
worker의 런타임 의존성에는 제한 계정 pool만 전달한다.
실제 R2·OpenAI·모델 추론·Render worker 배포를 검증했다는 의미는 아니다.

## 실제 환경에 적용하기 전에

- A: 실제 schema/extension 소유자, 함수·trigger 생성 권한과 기존 PUBLIC/역할 상속을 확인한다.
  새로운 빈 역할에 적용하고 예상 밖 권한이 없는지 검사한다.
- A/B: worker 시작·migration 계정을 분리하고 별도 cleanup/관리 작업에는 이 역할을 사용하지 않는다.
  TEMP 권한과 public schema 접근은 앞선 초기화 구성처럼 별도로 제거한다.
- C: 실제 웹 게시 조회와 계정 격리는 배포 승인 후 검증한다.
- staging은 앞서 18.6이었고 로컬은 18.7이다. 로컬 관리자 superuser의 구성 권한을
  Render 비슈퍼유저가 갖는지는 별도 확인이 필요하다.
- 실제 삭제·GRANT·환경 설정 변경은 사용자에게 대상을 제시하고 승인받은 뒤 수행한다.
