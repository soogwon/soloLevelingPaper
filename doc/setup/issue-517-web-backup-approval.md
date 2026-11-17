# #517 웹 제한 계정·백업 복구 및 적용 승인안

2026-11-17, main `8fafb0c` 기반. 실제 staging에는 접속·변경하지 않았다.
앞서 사용자가 직접 실행한 읽기 전용 결과만 참고했다.

## 검증 결과와 범위

- 로컬 PostgreSQL 18.7 / pgvector 0.8.7, 별도 NOINHERIT·비슈퍼유저 웹 LOGIN 역할.
- 신규 웹 DB 시나리오 5개 + 백업/복구 1개를 포함해 HTTP 회귀 **156 passed**.
- Node 22 Docker의 `web/tests/*.test.mjs` 전체 **145 passed, 0 skipped**.
- Ruff 통과. 실제 브라우저의 Auth0 로그인·R2 PUT·OpenAI 호출·Render 배포는 수행하지 않았다.

| 기능 | 제한 DB 계정으로 확인한 경로 |
| --- | --- |
| 인증 | 로그인 state 생성·소비, 대역 OIDC callback, 세션·me, CSRF 거부, 로그아웃·재조회 401 |
| 논문·학습 | 제품 저장소로 동기화 입력, HTTP 목록·상세·요약 조회, 읽기 완료 저장·조회 |
| 메모·하이라이트 | 제품 저장소 저장, HTTP 기록 목록·상세 조회 |
| 컬렉션 | 생성·목록·상세·이름 변경·논문 추가/제거·삭제 |
| 이어하기 | 웹 resume 요청 생성 (Desktop 실행은 범위 밖) |
| 기기 | 등록·목록·자격 발급·폐기 |
| 기존 PDF | 동기화 버전의 업로드 예약·완료 저장, 웹 파일 접근 URL 대역 조회 |
| 원격 PDF | intent 생성·재조회, 보관/AI 동의 조회·저장·철회, prepare·complete 대역, 처리 접수·조회 |
| 삭제 | 명시적 삭제 요청 접수·조회; 물리 R2 삭제는 수행하지 않음 |
| 격리/권한 | 다른 사용자 논문 404, DDL/예산 상한/게시 포인터/migration 이력 변경 차단 |
| 시작 | 실제 migration 이력 --check 통과, apply 권한 거부 |

프런트엔드 전체 자동 테스트와 위 DB 기능별 시나리오가 통과했다는 뜻이다.
모든 API 입력 조합이나 실제 브라우저→외부 제공자 종단 검증을 완료했다는 의미는 아니다.

## 확인한 제한과 보완

- 처리 접수의 `server_processing_job_authorizations`, 삭제 접수의
  `server_deletion_requests` 행 잠금에 UPDATE 권한이 필요했다.
  식별자 열 UPDATE + 관리자 소유 변경 차단 트리거로 보완했다.
- 웹 역할 계약은 `tests/fixtures/web_permissions.py`다. 업무 테이블의 명시적
  SELECT/INSERT/UPDATE, 컬렉션/멤버십 및 일회용 로그인 상태 DELETE만 허용한다.
  job 접수·취소에 필요한 열 권한은 있지만 publication·벡터·예산 관리 권한은 없다.
  열 UPDATE는 값의 의미까지 제한하지 않으므로 서비스가 침해된 상황의 job 상태 조작 방지는
  별도 범위다. 앱의 owner/lease 검사를 대체하는 RLS 계약도 아니다.
- **원격 업로드 직후 기존 `/file-access`는 404**다. 파일 저장소가 해당 버전의
  `server_learning_records` 존재를 요구하기 때문이다. 동기화 논문에서는 ready로
  정상 조회된다. 권한 오류가 아닌 현재 제품 연결 제한이며 원격 PDF 열람 완료로 보고하지 않는다.
- `/upload-status`는 기기 전용이므로 웹 세션에는 403을 반환한다.
- 로컬 superuser가 schema/역할을 준비했다. 실제 Render는 18.6 비슈퍼유저 관리자다.

## 백업·복구 리허설

`test_web_backup_restore_postgres.py`는 opt-in으로 새 UUID DB 두 개와 새 역할만
생성한다. 외부 DB를 입력하지 않으며 DSN host는 loopback만 허용한다.
DB의 system_identifier를 Docker 내부 결과와 비교해 서로 다른 클러스터이면
리허설 DB 생성 전에 중단한다. 백업 파일 정리가 실패해도 DB·역할 정리는 시도한다.
CI에서는 서비스 컨테이너 ID를 명시하고 신규 6개 사례의 skip/실패가 없음을 확인한다.

1. migration 37개와 제한 웹 권한·트리거, 합성 사용자/세션/논문을 구성.
2. `pg_dump -Fc`로 전체 DB를 파일 권한 0600으로 백업.
3. `pg_restore --list` 검사 후 빈 별도 로컬 DB에 `--single-transaction --exit-on-error` 복원.
4. 42개 테이블 전체 행, relation ACL, 사용자 트리거 정의를 원본과 비교하여 일치 확인.
5. 복원본에서 제한 계정으로 논문 조회, migration 이력 변경과 잠금 전용 행 변경 거부 확인.
6. 원본 합성 사용자/세션 삭제 후 `--clean --if-exists --single-transaction`으로 복구,
   전체 데이터/ACL/트리거 일치 및 migration --check 통과 확인.
7. 생성한 DB·역할·백업 파일 정리.

이 `--clean` 명령은 **로컬 전용**이다. staging 복구에 그대로 사용하지 않는다.
pg_dump는 역할·비밀번호·Render 환경 변수를 백업하지 않는다. 이번 복원은 동일 로컬
클러스터의 기존 역할을 유지한 상태에서 수행했으며 역할 소실 복구까지 입증하지 않는다.
[PostgreSQL pg_dump](https://www.postgresql.org/docs/18/app-pgdump.html),
[pg_restore](https://www.postgresql.org/docs/18/app-pgrestore.html).

## 실제 적용을 위한 최종 승인 범위

현재 문서는 승인안이며 실행 승인을 받았다는 뜻이 아니다.

1. **백업 승인**: 기존 staging DB의 테스트 데이터·웹 세션·기기 자격 해시를 로컬 암호화
   저장소에 export. 채팅/GitHub/공유 저장소에 올리지 않는다. 원본 DB 계정·비밀번호는 유지한다.
   역할 속성·membership·DB별 search_path·ACL·Render 기존 설정은 별도 보안 경로에 보관한다.
   보관 위치/접근자/삭제 시점은 사용자 확인 후 확정한다.
2. **복구 검증 선행**: PostgreSQL 18 클라이언트로 백업 목록/체크섬과 복원을 확인한다.
   실제 관리자 소유권에 맞춰 pgvector extension 삭제/재생성을 제외한 복구 목록과
   `public`↔`slp_staging` 이동 역순을 확정한다. 로컬 superuser 복구 성공만으로 이를 생략하지 않는다.
3. **서비스 일시 중지 승인**: 웹·worker·Desktop 동기화·cleanup 등 모든 writer를 멈춘다.
   읽기 전용 점검 당시 연결 0은 지금도 중지됐다는 증거가 아니므로 직전 재확인한다.
4. **DB 변경 승인**: 사전 확인한 앱 테이블 42개/함수 8개만 초기화·재구성,
   `slp_staging` 생성, vector 이동(삭제하지 않음), NOLOGIN owner 및 웹/worker 제한 역할 생성,
   명시적 GRANT와 변경 차단 트리거, database별 search_path와 TEMP/public 권한 제한.
   기존 DB 계정/password·Auth0 계정/설정·plpgsql·R2/PDF·키체인은 보존한다.
5. **설정·재시작 승인**: 웹 DATABASE_URL을 제한 계정으로 변경하고 migration은 별도 관리자 단계,
   웹은 SERVER_MIGRATION_MODE=check로 시작한다. 시작 cleanup은 별도 관리 작업으로 분리한다.
   worker는 실제 실행 승인 전 standby 유지. 이 단계는 실제 Render 설정 변경·재시작을 포함한다.
6. **검증/복구 조건**: 로그인·목록·쓰기·조회·권한 차단·schema 일치 확인. 실패 시 writer를 계속
   멈춘 채 트랜잭션 rollback 또는 승인된 백업 복원, 이전 환경 복구 후 재검증한다.
   확인되지 않은 상태로 서비스를 재개하지 않는다.

삭제 후에는 앱 프로필·세션·기기 연결을 다시 만들어야 한다. 사용자·서비스 예산과 동의는
관리자가 무제한 허용값으로 임의 재생성하지 않는다. 실제 API 호출·R2 전송·worker 배포·결제는 별도 승인이다.
현재 #517 종료 또는 실제 초기화 실행 승인을 요청하지 않는다. 복구 SQL과 백업 보관 조건이 확정되면
삭제 직전 구체적인 실행안을 다시 제시한다.
