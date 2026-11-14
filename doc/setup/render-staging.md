# Render 스테이징 배포

이 구성은 하나의 Render Web Service가 FastAPI와 `web/` 정적 파일을 같은 origin으로 제공합니다.
브라우저의 `/v1` 요청과 OIDC 세션 쿠키가 별도 CORS·쿠키 예외 없이 동작하도록 하기 위한 구성입니다.

## 저장소에 포함된 배포 구성

- `render.yaml`: 무료 Web Service와 무료 Render Postgres Blueprint
- `Dockerfile`: 서버 의존성과 API·웹 파일을 포함한 이미지
- `deploy/render-start.sh`: 서버 시작 전 PostgreSQL 마이그레이션 실행
- `WEB_STATIC_ROOT=/app/web`: API route 등록 후 외부 사이트를 `/`에 mount
- Render가 주입하는 `PORT`: `API_PORT`가 없을 때 자동 사용

무료 Web Service에는 Render의 pre-deploy command가 제공되지 않습니다. 따라서 스테이징의 단일 인스턴스가
시작할 때 마이그레이션을 먼저 실행합니다. 마이그레이션은 PostgreSQL advisory lock과 적용 이력으로
중복 실행을 방지합니다. 나중에 유료 Web Service로 전환하면 Dashboard의 Pre-Deploy Command를
`solo-leveling-server-migrate`로 지정하고 시작 명령을 `solo-leveling-api`로 분리하는 편이 좋습니다.

## 최초 배포

1. Render Dashboard에서 **New > Blueprint**를 선택하고 이 GitHub 저장소를 연결합니다.
2. Blueprint 경로로 저장소 루트의 `render.yaml`을 사용합니다.
3. 생성 화면에서 `sync: false`로 표시되는 OIDC 값을 모두 입력합니다. 비밀값은 저장소에 커밋하지 않습니다.
4. Blueprint를 적용합니다. DB 생성, Docker 이미지 빌드, 마이그레이션, API 시작 순서로 진행됩니다.
5. Web Service의 Events와 Logs에서 `applied:` 출력과 `/health` 성공을 확인합니다.

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

## PDF 객체 저장소

PDF는 Render Web Service의 임시 파일시스템에 저장하지 않습니다. 별도의 비공개 S3 호환 버킷을 만들고
위 환경 변수를 Render에 입력합니다. 자격정보는 해당 버킷의 객체 읽기·쓰기·삭제에만 제한합니다.

브라우저 또는 로컬 MCP가 presigned PUT 주소로 업로드할 수 있도록 버킷 CORS에는 스테이징 origin의
`PUT`과 `Content-Type` 헤더만 허용합니다. 버킷 자체를 공개하지 않으며,
객체 목록 권한이나 공개 URL을 사용자에게 제공하지 않습니다. 업로드 준비 API는 파일당 50 MiB,
사용자당 1 GiB를 기본 한도로 사용합니다. 완료 확인·다운로드 주소 발급 전에는 업로드된 것으로
표시하지 않습니다. 완료 확인 시 서버가 객체를 스트리밍으로 읽어 크기·형식·SHA-256을 검증하고,
검증된 객체만 확정 키로 복사합니다. 따라서 Render와 객체 저장소 사이의 읽기 트래픽이 발생합니다.

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
