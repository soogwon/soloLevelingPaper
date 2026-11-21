# Render 스테이징 배포

이 구성은 하나의 Render Web Service가 FastAPI와 `web/` 정적 파일을 같은 origin으로 제공합니다.
브라우저의 `/v1` 요청과 OIDC 세션 쿠키가 별도 CORS·쿠키 예외 없이 동작하도록 하기 위한 구성입니다.

## 저장소에 포함된 배포 구성

- `render.yaml`: 무료 Web Service와 복구용 이전 DB 선언
- `Dockerfile`: 서버 의존성과 API·웹 파일을 포함한 이미지
- `deploy/render-start.sh`: 서버 시작 전 migration 이력 확인; 관리자 migration·정리 작업은 별도 실행
- `WEB_STATIC_ROOT=/app/web`: API route 등록 후 외부 사이트를 `/`에 mount
  - 웹 파일(HTML·JS·CSS)은 `Cache-Control: no-cache`로 응답한다. 브라우저는 매번 ETag로 재확인하고, 바뀌지 않은 파일은 304로 재사용한다. 배포 직후에도 새로고침하면 새 파일을 받는다(이미 열린 탭은 다시 불러와야 한다).
- Render가 주입하는 `PORT`: `API_PORT`가 없을 때 자동 사용

현재 웹은 `solo-leveling-staging-db-v2`의 제한 웹 계정으로 연결한다.
`autoDeployTrigger: "off"`를 명시해 Blueprint 동기화 후에도 수동 배포를 유지한다.
`DATABASE_URL`은 `sync: false`로 두고 Dashboard에서 직접 관리한다. Blueprint 업데이트는
이 값을 덮어쓰지 않는다. DB 관리자의 `connectionString`이나 worker 자격을 입력하지 않는다.
[Render 공식 동작](https://render.com/docs/blueprint-spec#environment-variables).

`SERVER_MIGRATION_MODE=check`로 migration 이름·체크섬·누락만 확인한다. 누락 또는 오류가 있으면
웹 시작을 거부한다. migration 적용은 웹과 분리된 관리자 실행 단계에서 먼저 완료해야 한다.
`REMOTE_UPLOAD_CLEANUP_ON_START=false`, `REMOTE_DELETION_CLEANUP_ON_START=false`를 유지한다.
제한 웹 계정은 정리 담당 계정이 아니며, 별도 scheduler/정리 실행 환경은 아직 준비되지 않았다.
따라서 현재 자동 정리는 실행되지 않는다. R2 lifecycle만으로 DB 정리까지 완료됐다고 판단하지 않는다.

`databases`의 이전 무료 DB 선언은 복구용 보존을 위해 남겨둔다. 새 v2 DB는 수동 생성·관리한다.
이 Blueprint를 새로운 환경에 그대로 적용하면 복구용 DB도 생성되므로 새 환경 설치 전에
DB 선언을 검토해야 한다. 기존 DB 삭제나 새 유료 자원 생성은 이 변경에 포함하지 않는다.

## 전환 및 이후 동기화

1. 새 DB의 schema·pgvector·migration·제한 계정을 먼저 구성한다.
2. 웹 계정의 실제 접속, migration check, 필요한 조회·쓰기 및 금지 권한을 검증한다.
3. Render 웹 Environment에 웹 전용 `DATABASE_URL`을 직접 입력한다. 비밀값은 커밋하지 않는다.
4. **Save only는 배포가 아니다.** 동일 검증 커밋을 배포해 저장된 환경변수를 반영한다.
5. Live, `/health`, Auth0 재로그인과 새 DB 목록 조회를 확인한다. 기존 DB는 복구용으로 보존한다.
6. Blueprint Auto Sync는 검토 중 `No`로 유지한다. 병합 후 Manual Sync의 변경 계획에서
   DB 생성·삭제·관리자 URL 재연결이 없는지 확인하고 적용한다. 이는 웹 Auto-Deploy와 별도 설정이다.
7. 실패하면 기존 DB URL을 사용자 입력으로 복원하고 검증 커밋을 재배포한다. DB 교체 후
   새로 생긴 데이터는 자동으로 이전 DB에 옮겨지지 않는다.

필수 OIDC 값은 다음과 같습니다.

| 변수 | 용도 |
|---|---|
| `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL` | API access token 검증 |
| `WEB_OIDC_AUTHORIZATION_URL`, `WEB_OIDC_TOKEN_URL`, `WEB_OIDC_USERINFO_URL` | 웹 로그인 흐름 |
| `WEB_OIDC_CLIENT_ID`, `WEB_OIDC_CLIENT_SECRET` | confidential web client |
| `OBJECT_STORAGE_ENDPOINT`, `OBJECT_STORAGE_BUCKET` | 비공개 S3 호환 PDF 저장소 |
| `OBJECT_STORAGE_ACCESS_KEY_ID`, `OBJECT_STORAGE_SECRET_ACCESS_KEY` | 객체 저장소 전용 자격정보 |
| `OBJECT_STORAGE_REGION` | 저장소 리전. Cloudflare R2는 `auto` |

OIDC 공급자에는 `https://solo-leveling-staging.soogyung.com/v1/auth/callback`을 허용 callback으로 등록합니다.
Auth0를 사용할 때 `OIDC_AUDIENCE`에는 Custom API의 Identifier를 입력하고, 해당 API는 RS256과
RFC 9068 access-token profile을 사용해야 합니다. 자세한 설정은 [Auth0 설정](auth0.md)을 따릅니다.

아래 예시의 `"실제_웹_연결_역할"`은 자리표시자입니다. 그대로 실행하면 역할이 없어
`SQLSTATE 42704`(undefined object)로 실패합니다. 웹 `DATABASE_URL`의 사용자 이름
(스테이징은 `slp_staging_web`)으로 바꿔 실행합니다.

### 원격 읽기 상태와 전역 메모 조회 권한

마이그레이션 `0065` 적용 후 새 웹 버전을 시작하기 전에, 실제 웹 연결 역할에
`server_remote_learning_records`, `server_context_annotations`,
`server_execution_contexts`의 `SELECT`를 부여합니다. 스키마 소유자로 접속해
스키마와 역할 이름을 다음 예시의 값과 대조합니다.

```sql
GRANT SELECT ON TABLE
    slp_staging.server_remote_learning_records,
    slp_staging.server_context_annotations,
    slp_staging.server_execution_contexts
TO "실제_웹_연결_역할";
```

웹 역할에서 논문 목록·상세와 전역 메모 목록·상세를 조회하고, 다른 계정의 기록이
노출되지 않는지 확인합니다. 롤백 시 이전 웹 버전을 복원한 뒤 이 배포에서 새로
추가한 조회 권한만 회수합니다.

### 계정 단위 동의 권한

마이그레이션 `0072` 적용 후 새 웹 버전을 시작하기 전에, 실제 웹 연결 역할에 계정 동의 표의
조회·기록·철회 권한을 부여합니다. 기능별 MCP 역할에는 이 표 권한을 주지 않습니다. MCP 역할은
`server_account_consent_current` 함수로 현재 동의 여부만 확인합니다.

```sql
GRANT SELECT, INSERT, UPDATE(revoked_at) ON TABLE slp_staging.server_account_consents
TO "실제_웹_연결_역할";
```

웹에 로그인하면 첫 동의 화면이 나타나는지, 동의 후 계정 화면에서 OpenAI 전송 동의를 철회·재동의할 수
있는지 확인합니다. 롤백 시 이전 웹 버전을 복원한 뒤 이 권한만 회수합니다.

## PDF 객체 저장소

PDF는 Render Web Service의 임시 파일시스템에 저장하지 않습니다. 별도의 비공개 S3 호환 버킷을 만들고
위 환경 변수를 Render에 입력합니다. 자격정보는 해당 버킷의 객체 읽기·쓰기·삭제에만 제한합니다.

브라우저 또는 로컬 MCP가 presigned PUT 주소로 업로드할 수 있도록 버킷 CORS에는 스테이징 origin의
`PUT`과 `Content-Type` 헤더만 허용합니다. 버킷 자체를 공개하지 않으며,
객체 목록 권한이나 공개 URL을 사용자에게 제공하지 않습니다. 업로드 준비 API는 파일당 50 MiB,
사용자당 1 GiB를 기본 한도로 사용합니다. 완료 확인·다운로드 주소 발급 전에는 업로드된 것으로
표시하지 않습니다. 완료 확인 시 서버가 객체를 스트리밍으로 읽어 크기·형식·SHA-256을 검증하고,
검증된 객체만 확정 키로 복사합니다. 따라서 Render와 객체 저장소 사이의 읽기 트래픽이 발생합니다.

### PDF 보존·정리 정책

- 업로드 준비 주소는 15분 동안 유효하다. 준비 중 객체는 `private/tmp/` 아래에만 저장한다.
- R2 **Settings > Object Lifecycle Rules**에 `private/tmp/` prefix를 1일 후 삭제하는 규칙을
  추가한다. R2 수명주기 삭제는 만료 시각 이후 실제 반영까지 시간이 걸릴 수 있으므로 15분보다 긴
  1일을 사용한다. 이 규칙은 중단되거나 완료 확인에 실패한 임시 객체만 정리한다.
- 새 형식의 검증 완료 PDF는 `private/files/`로 복사한 뒤 임시 객체를 즉시 삭제한다. 이전 형식으로
  예약된 업로드는 호환 경로인 `private/<owner>/<paper>/<version>/files/`에 완료될 수 있다.
  두 완료 경로 모두 `private/tmp/` 수명주기 규칙에서 제외되어야 한다.
- 현재 확정 PDF는 사용자가 명시적으로 삭제할 API와 tombstone 정책이 마련될 때까지 보존한다.
  동기화 중지, 로컬 파일 삭제, 기기 연결 해제를 서버 PDF 삭제로 해석하지 않는다.
- 현재 R2 버킷은 별도 백업본을 만들지 않는다. 스테이징에는 공개 테스트 PDF만 사용하며, 운영 전에는
  객체 버전 관리 또는 별도 버킷 복제, 백업 만료 기간, 계정 탈퇴 시 삭제 기한을 별도 결정한다.

새 규칙을 적용한 뒤에는 테스트용 미완료 객체 하나를 만들어 `private/tmp/`에 저장되는지 확인하고,
완료된 객체가 `private/files/`에 있으며 수명주기 대상에서 제외되는지 확인한다. 기존 배포의 객체는 경로만 보고 삭제하지 않는다. 이전 형식의 완료 PDF도 `private/files/` 밖에
있을 수 있다. 수동 정리 전 서버 DB의 완료 파일 참조와 업로드 상태·만료 시각을 대조하고,
활성 예약이 없으며 완료 파일로 참조되지 않는 임시 객체만 개별 후보로 검토한다.
DB 상태를 확인할 수 없거나 완료 여부가 불분명하면 보존한다. `private/` 전체나 이전 owner
prefix에 일괄 삭제·수명주기 규칙을 적용하지 않는다.

## 도메인 연결

1. Render Web Service의 **Settings > Custom Domains**에서 도메인이 등록됐는지 확인합니다.
2. Render가 화면에 표시하는 CNAME 대상을 복사합니다.
3. Hostinger DNS Zone에서 `solo-leveling-staging` 호스트의 CNAME을 그 대상으로 지정합니다.
4. 기존에 같은 호스트로 만든 A/AAAA/CNAME 레코드가 있다면 충돌하지 않도록 하나만 유지합니다.
5. Render가 도메인을 검증하고 TLS 인증서를 발급할 때까지 기다립니다.

## 배포 후 확인

1. `https://solo-leveling-staging.soogyung.com/health`가 `status: ok`를 반환하는지 확인합니다.
2. 루트 주소가 External site를 표시하는지 확인합니다.
3. 실제 테스트 계정으로 로그인하고 callback 이후 논문 목록이 표시되는지 확인합니다.
4. 테스트 PDF를 로컬 MCP에서 동기화한 뒤 웹 상세·메모·이어하기 흐름을 확인합니다.
5. Render Postgres의 복구 수단을 준비한 뒤 마이그레이션 재배포를 시험합니다.

무료 Render Postgres는 장기 운영용이 아니며 생성 후 만료 제한이 있습니다. 이 구성은 종단 간 검증용
스테이징에만 사용하고, 만료 전에 백업하거나 유료 DB로 전환해야 합니다. 실제 사용자 데이터나 비공개
논문 원문은 테스트 환경에 넣지 않습니다.
