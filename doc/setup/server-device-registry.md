# 중앙 서버 기기 등록부

중앙 서버는 OIDC 토큰의 `device_id` claim만으로 기기 접근을 허용하지 않는다. `server_devices`의 사용자 소유권과 해제 상태를 요청마다 확인한다.

## PostgreSQL 준비

`DATABASE_URL`에는 중앙 서버 전용 PostgreSQL DSN을 설정한다. 로컬 MCP의 SQLite 파일을 지정하거나 서버 DB를 로컬 데이터 루트에 두지 않는다.

```powershell
$env:DATABASE_URL = "postgresql://app:password@db.example.com/solo_leveling"
psql $env:DATABASE_URL -f migrations/server/0001_server_devices.sql
psql $env:DATABASE_URL -f migrations/server/0002_device_credentials.sql
psql $env:DATABASE_URL -f migrations/server/0003_server_papers.sql
solo-leveling-api
```

애플리케이션은 마이그레이션을 자동 실행하지 않는다. 배포 과정에서 마이그레이션 성공을 확인한 뒤 새 서버 버전을 시작한다.

## 웹 세션 API

- `POST /v1/devices`: 표시 이름으로 새 서버 기기 ID 등록
- `GET /v1/devices`: 로그인 사용자의 활성 기기 목록
- `DELETE /v1/devices/{device_id}`: 로그인 사용자의 기기 해제

세 API는 `web_session` 자격만 허용한다. 요청 본문에서 사용자 ID를 받지 않으며 모든 SQL은 검증된 `AuthenticatedPrincipal.user_id`를 조건에 포함한다. 다른 사용자의 기기 ID 또는 이미 해제된 ID를 삭제해도 동일하게 `204`를 반환하여 존재 여부를 노출하지 않는다.

## 기기 자격

웹 세션으로 `POST /v1/devices/{device_id}/credentials`를 호출하면 15분 access token과 최대 30일 refresh token을 한 번만 응답한다. 응답에는 `Cache-Control: no-store`가 포함된다. 서버는 토큰 원문을 저장하지 않고 SHA-256 hash만 저장한다. 토큰은 256비트 이상의 무작위 값을 사용하므로 DB hash만으로 원문을 실용적으로 추측할 수 없다.

`POST /v1/device-token/refresh`는 refresh token을 매번 교체하며 최초 만료 시각을 연장하지 않는다. 이미 사용된 refresh token이 다시 제시되면 같은 token family를 모두 폐기한다. 이는 public client의 refresh token에 회전 또는 sender constraint를 요구하는 OAuth 2.0 Security BCP(RFC 9700)의 방향을 따른다.

기기를 해제하면 access token 조회와 refresh가 모두 즉시 실패한다. access token과 refresh token은 URL, 일반 로그, MCP tool 입출력, SQLite 본문에 기록하지 않는다.

다음 단계에서 로컬 CLI가 시스템 브라우저 Authorization Code + PKCE(S256) 로그인을 완료하고, 웹 access token으로 기기를 등록한 뒤 발급된 자격을 운영체제 자격 증명 저장소에 보관하도록 연결한다.
