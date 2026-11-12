# 중앙 서버 기기 등록부

중앙 서버는 OIDC 토큰의 `device_id` claim만으로 기기 접근을 허용하지 않는다. `server_devices`의 사용자 소유권과 해제 상태를 요청마다 확인한다.

## PostgreSQL 준비

`DATABASE_URL`에는 중앙 서버 전용 PostgreSQL DSN을 설정한다. 로컬 MCP의 SQLite 파일을 지정하거나 서버 DB를 로컬 데이터 루트에 두지 않는다.

```powershell
$env:DATABASE_URL = "postgresql://app:password@db.example.com/solo_leveling"
psql $env:DATABASE_URL -f migrations/server/0001_server_devices.sql
solo-leveling-api
```

애플리케이션은 마이그레이션을 자동 실행하지 않는다. 배포 과정에서 마이그레이션 성공을 확인한 뒤 새 서버 버전을 시작한다.

## 웹 세션 API

- `POST /v1/devices`: 표시 이름으로 새 서버 기기 ID 등록
- `GET /v1/devices`: 로그인 사용자의 활성 기기 목록
- `DELETE /v1/devices/{device_id}`: 로그인 사용자의 기기 해제

세 API는 `web_session` 자격만 허용한다. 요청 본문에서 사용자 ID를 받지 않으며 모든 SQL은 검증된 `AuthenticatedPrincipal.user_id`를 조건에 포함한다. 다른 사용자의 기기 ID 또는 이미 해제된 ID를 삭제해도 동일하게 `204`를 반환하여 존재 여부를 노출하지 않는다.

등록 API는 기기 ID만 만든다. 아직 기기 access token이나 refresh token을 발급하지 않는다. 다음 단계에서 브라우저 PKCE 연결 완료 후 등록된 기기 ID를 포함하는 단기 access token 발급·갱신·폐기 흐름을 연결한다.
