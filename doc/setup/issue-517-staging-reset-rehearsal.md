# #517 로컬 초기화·권한 분리 리허설

## 범위와 결과

2026-11-17, 기준 main `9fadf7e`에서 PostgreSQL 18.7 / pgvector 0.8.7의
일회용 Docker DB로 검증했다. 실제 staging DB, Render 설정, Auth0, R2는
조회·변경하지 않았다. PDF 전송·유료 API 호출·자원 결제도 하지 않았다.

리허설·처리 DB·검증 runner·웹 시작·migration 관련 테스트 **65개 통과**,
Ruff와 diff 공백 검사 통과. 생성한 DB/역할 잔여 수는 각각 0이다.
staging은 앞선 읽기 전용 확인에서 18.6이었으므로 패치 버전 차이는 남는다.

`tests/integration/test_staging_reset_rehearsal.py`는 명시적인
`SOLO_RESET_REHEARSAL_DSN`이 있어야 실행되며 loopback 주소만 허용한다.
입력 DSN의 DB는 삭제하지 않고 새 UUID DB와 역할을 만들어 그 안에서만 실행한다.
이 테스트는 운영 초기화 도구가 아니다. 로컬 포트 포워딩으로 staging을 연결하지 않는다.

- 원본 migration 0001–0037로 public의 테이블 42개를 구성한다.
- 합성 데이터 삭제 중 실패를 주입해 트랜잭션 rollback을 확인한다.
- 전용 DB에 방금 만든 테이블을 명시적으로 동시에 DROP RESTRICT하고,
  앱 함수를 제거한다. public schema 자체나 vector extension은 삭제하지 않는다.
- vector extension을 slp_staging으로 옮겨 extension OID가 유지되는지 확인한다.
- NOLOGIN owner로 새 schema와 migration을 구성하고 기존 접속 계정으로 재접속한다.
- 웹·worker 계정은 superuser/CREATEDB/CREATEROLE/BYPASSRLS 및 owner 역할 전환이 없다.
- 두 계정의 search_path는 slp_staging 하나이며 vector_dims와 거리 연산이 정상이다.
- 두 계정의 일반·public·임시 테이블 생성과 migration 이력 삭제를 차단한다.
- worker의 웹 세션·기기 자격 조회와 동의·예산 설정 변경을 차단한다.
- 웹 계정으로 합성 사용자와 새 세션을 저장·조회한다. 실제 Auth0 로그인 검증은 아니다.

## 웹 시작과 migration 분리

`deploy/render-start.sh`의 `SERVER_MIGRATION_MODE`:

| 값 | 동작 |
| --- | --- |
| apply (기존 기본값) | 기존처럼 migration 적용 후 웹 시작 |
| check | `solo-leveling-server-migrate --check`만 실행, 이력 정상일 때 웹 시작 |
| 그 외 | 웹·cleanup 실행 전에 실패 |

권한 분리 배포에서는 별도 관리자 단계에서 migration을 적용한 뒤 웹의
DATABASE_URL을 제한 계정으로 바꾸고 `SERVER_MIGRATION_MODE=check`를 지정한다.
DB 역할의 database별 search_path도 slp_staging으로 설정해야 한다.
worker의 기존 명시적 schema 설정과 같은 schema를 사용한다.
미적용 migration이나 DB 검사 실패는 웹 시작을 막는다. 이력 검사는 물리 schema의
모든 수동 변경을 감지하는 도구는 아니다.

기존 배포에 영향을 주지 않도록 Render blueprint와 환경 변수는 변경하지 않았다.
실제 전환 시 시작 cleanup 권한도 별도로 확인해야 한다. 이번 제한 계정에는
cleanup 권한을 부여하지 않았으므로, 권한 확정 전에는 해당 시작 작업을 끄고
별도 관리 작업으로 실행하는 방안을 검토한다.

## 재현

로컬의 새 PostgreSQL 클러스터만 사용한다. 관리자 테스트 계정은 DB/역할 생성이
가능해야 한다. 다음 명령의 DSN은 해당 로컬 클러스터로 지정한다.

```sh
SOLO_RESET_REHEARSAL_DSN="$LOCAL_TEST_DSN" PYTHONPATH=src:. \
  python -m pytest tests/integration/test_staging_reset_rehearsal.py -q
python -m pytest tests/unit/test_render_start_contract.py tests/unit/test_server_migrations.py -q
```

## 실제 초기화 전에 남은 사항

후속 로컬 worker 종단 검증과 테이블/열 권한 계약은
[worker 권한 검증](issue-517-worker-permissions.md)에 기록했다.
아래 최소 표본 권한의 한계는 초기 리허설 당시의 상태이며, 실제 staging 적용은 계속 미실행이다.

- **A**: 실제 객체 이름·의존성·extension 소유자와 Render 관리자 권한을 확인한다.
  이번 Docker 관리자는 superuser이므로 Render 비슈퍼유저의 extension 이동 권한까지
  입증하지 않는다. 실제 42개 객체 이름이 로컬과 같다는 것도 아직 확인하지 않았다.
- **A/B**: worker의 lease·동의 조회·usage 원장·staging 저장·게시를 모두 실행하며
  테이블/함수/sequence별 권한을 확정한다. 이번 최소 표본 권한을 제품 전체에 적용하면 안 된다.
- **C**: 제한 계정의 실제 웹 로그인·조회·동기화와 worker 게시 결과 표시를 검증한다.
- 기존 DB 로그인/비밀번호와 Auth0 계정·설정은 유지한다. 앱 프로필/세션 초기화 뒤에는
  다시 로그인·기기 연결이 필요하다. R2/PDF/키체인은 이번 초기화 범위 밖이다.
- 실제 삭제 전 writer 중지, 백업/복구 방법, 정확한 초기화 대상과 역할 권한 변경안을
  사용자에게 제시하고 승인받는다. 사용자 승인 전 실제 staging에는 적용하지 않는다.
