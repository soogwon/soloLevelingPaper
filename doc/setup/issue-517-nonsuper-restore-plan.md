# #517 Render 비슈퍼유저 복구·백업 보관 및 실행 승인안

기준: main b1aa924, 2026-11-17. 실제 staging에는 실행하지 않았다.
기존 검증: [웹 제한 계정·백업 승인안](issue-517-web-backup-approval.md).

## 복구 SQL과 실행 조건

[복구 전처리 SQL](issue-517-restore-prelude.sql)은 재구성 후 `slp_staging`의
42개 앱 테이블과 알려진 앱 함수를 제거하고, 기존 vector extension을 `public`으로
이동하여 **초기화 직전 백업으로 되돌리는 용도**다. 초기화 실행 SQL이 아니다.
`DROP SCHEMA CASCADE`, extension 삭제·재설치, 슈퍼유저 권한, trigger 비활성화를 쓰지 않는다.

사전 읽기 전용 확인에서 Render 관리자는 비슈퍼유저이며 DB·앱 객체·vector의 소유자이고
CREATEROLE·CREATEDB 권한이 있었다. 실행 직전 소유권·객체 목록·연결을 다시 확인한다.
새 앱 소유자 역할로 SET ROLE할 수 있는 멤버십도 필요하다. 이를 임의로 가정하지 않는다.

복원 자료는 다음과 같이 준비한다. 아래는 실행 순서 설명이며, 실제 환경에 바로 실행하지 않는다.

1. 웹·worker·cleanup 등 모든 쓰기를 중지하고 신규 연결도 차단한다. 기존 세션 종료는 승인 범위에 포함한다.
2. 기존 관리자 연결로 전체 DB custom-format 백업을 만든다. 역할·환경 설정은 별도 기록한다.
3. 백업에서 `pg_restore --schema=public --no-owner --no-acl --file=restore-app.sql backup.dump`로 앱 SQL을 추출한다.
4. 추출본에 extension 생성·삭제·변경이 없는지 검사하고, 테이블·함수 목록 및 migration 체크섬을 확인한다.
5. 초기화 직전 catalog와 일치하는 `restore-acl.sql`, `verify.sql`을 생성·검토한다.
6. 복구 시 관리자로 아래 파일을 **단일 psql 호출·단일 transaction**에 묶는다.

```text
psql -X -1 -v ON_ERROR_STOP=1
  -v restore_database=<검증한 DB명>
  -v restore_admin=<기존 관리자>
  -v restore_owner=<새 앱 소유자>
  -f issue-517-restore-prelude.sql
  -f restore-app.sql
  -f restore-acl.sql
  -f verify.sql
```

접속 비밀번호는 명령·문서·로그에 넣지 않는다. 전처리 SQL 단독 실행은 금지한다.
대상 DB·계정, vector 소유자·위치, 테이블 목록이 다르면 중단한다.
종속 객체가 남으면 RESTRICT로 실패하며 CASCADE로 우회하지 않는다.
실패 시 전체 transaction을 rollback하고 서비스를 정지 상태로 유지한다.

`--no-acl`이므로 백업 데이터만 복원해서 권한 복구가 끝난 것은 아니다.
실행 직전 snapshot에는 schema/table/sequence/function ACL, default ACL,
역할 속성·멤버십, DB/역할 search_path와 CONNECT, 서비스 환경을 포함한다.
현재 시점의 실제 ACL을 추측해 고정 SQL로 작성하지 않는다.
**실제 snapshot 기반 restore-acl.sql 및 검증 SQL 확정은 삭제 전 필수 중단 지점**이다.
그 파일들이 없거나 검토되지 않았으면 삭제하지 않는다.
기존 로그인·비밀번호는 유지하며 새 역할·schema 삭제는 복구 검증 후 별도 확인한다.

## 백업 보관 제안 — 아직 승인·실행되지 않음

- 이 Mac의 클라우드 동기화·Git 대상 밖에 암호화 디스크 이미지로 보관한다.
  내부 폴더 0700, 백업·manifest 0600. 암호는 사용자 입력으로 정하고 백업과 함께 저장하지 않는다.
- 전체 custom archive, SHA-256, TOC, migration 체크섬, 객체·권한 snapshot,
  비밀값을 제외한 서비스 설정 목록과 복구 절차를 함께 보관한다.
- DB에는 테스트 세션·기기 자격의 해시 등이 있으므로 테스트 데이터도 민감 자료로 취급한다.
  DB URL·비밀번호·Auth0 secret·R2 key는 manifest나 GitHub에 기록하지 않는다.
- 백업 완료만으로 삭제하지 않는다. 해시 확인과 격리 DB 복원 리허설이 통과해야 한다.
- 보관 기간 제안: **전환 검수 완료 후 7일**, 장애 미해결이면 연장한다.
  기간이 지나도 자동 삭제하지 않고 사용자에게 삭제 승인을 받는다.
- 별도 매체 사본은 필요하면 사용자가 정한다. 외부 업로드·유료 백업 서비스는 이번 범위에 없다.
  Mac 한 곳만 보관하면 기기 손실에 대한 복구는 보장되지 않는다.

## 실제 삭제 전에 승인받을 최종 실행 범위

| 단계 | 승인 대상 | 조건 / 제외 |
| --- | --- | --- |
| 백업 | staging DB 전체를 로컬 암호화 저장소로 내보내기, 보관 기간 | 로컬 복원 성공 전 삭제 금지; 외부 공유 없음 |
| 서비스 중지 | staging 웹·worker·cleanup 쓰기 중지, 기존 연결 종료 | 공개 서비스·Auth0 변경 없음; 중지 시간 별도 합의 |
| 초기화 | 확인한 42개 앱 테이블의 테스트 데이터 및 8개 앱 함수 초기화·재구성 | DB 자체·기존 로그인·Auth0·R2/PDF·키체인 유지 |
| 권한 재구성 | 앱 schema, 소유자/웹/worker 역할, 최소 GRANT, 관리자의 SET ROLE 멤버십 | 기존 관리자 비밀번호 유지; 슈퍼유저 요구 안 함 |
| extension | 기존 vector를 앱 schema로 이동 | 삭제·재설치 금지; 소유자·의존성 재확인 |
| 시작 설정 | 별도 migration 실행 후 웹 check 모드, cleanup/worker는 검증 완료까지 정지 | 실제 worker/API 유료 호출은 포함하지 않음 |
| 검증 | 제한 계정 웹/worker DB 확인, 차단 권한·migration 확인 | 실제 사용자 데이터·PDF 전송·AI 호출 없음 |
| 실패 복구 | 승인된 백업으로 위 단일 transaction 복구, ACL/search_path/서비스 설정 원복 | 새 데이터 보존 여부 확인; 불명확하면 정지 후 사용자에게 보고 |

최종 승인에는 대상 DB 식별값, 백업 경로·보관 기간, 중지 시간,
삭제 목록·SQL 체크섬, 실제 ACL 복구 파일, 실패 시 복구 범위를 함께 제시한다.
**이 문서 작성은 삭제·설정 변경 승인으로 간주하지 않는다.**

## 로컬 검증

`test_nonsuper_restore_postgres.py`는 PostgreSQL 18.7 / pgvector 0.8.7 격리 DB에서
비슈퍼유저 복원, 42개 테이블 데이터 일치, migration 잔여 없음,
vector OID 유지, 복원 후 오류 주입 시 전체 rollback, 예상 밖 테이블 및 같은 이름의 다른 함수 시그니처 발견 시 차단을 검증한다.
로컬 웹 권한·기존 백업·비슈퍼유저 복구 테스트 총 7개 통과.
CI에서도 이 7개를 별도로 실행하고 건너뛰기가 없음을 검사한다.
fixture 초기 extension 소유 상태를 만들 때만 로컬 superuser를 쓰고,
덤프·재구성·복원 전 관리자 SUPERUSER를 회수한다. Render에서 권한을 올리는 절차가 아니다.

남은 범위: 실제 Render 18.6 환경 실행, 실행 직전 ACL/서비스 설정 snapshot,
암호화 백업 생성·별도 복원, 서비스 정지·재기동 실측. 모두 승인 후 진행한다.

참고: [psql 단일 transaction](https://www.postgresql.org/docs/18/app-psql.html),
[pg_restore 옵션](https://www.postgresql.org/docs/18/app-pgrestore.html).
