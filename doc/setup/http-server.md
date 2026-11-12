# 중앙 HTTP 서버 실행 골격

이 단계는 로컬 MCP를 원격 MCP로 바꾸지 않는다. 기존 `solo-leveling-mcp` 프로세스는 계속 `stdio`로 실행하고, 중앙 API는 별도의 `solo-leveling-api` 프로세스로 실행한다.

## 설치와 실행

```powershell
python -m pip install -e ".[server]"
$env:APP_ENV = "development"
$env:API_HOST = "127.0.0.1"
$env:API_PORT = "8000"
solo-leveling-api
```

서버가 시작되면 `GET /health`가 다음 형태로 응답한다.

```json
{
  "status": "ok",
  "service": "solo-leveling-api",
  "environment": "development"
}
```

## 현재 범위

- FastAPI 애플리케이션 팩터리와 Uvicorn 실행 진입점
- 환경 변수 기반 host, port, environment 설정
- 프로세스 상태 확인 API

사용자 인증, 장치 등록, PostgreSQL 저장소, 동기화 API, External site용 조회 API는 아직 구현하지 않는다. 특히 임시 사용자 ID를 요청에서 받아 인증을 우회하는 엔드포인트를 추가하지 않는다. DB 연결이 추가되면 애플리케이션 lifespan과 별도의 readiness 검사를 함께 도입한다. 다음 단계에서는 인증 주체와 서버 소유권 모델을 먼저 확정한 뒤 저장소 및 API 계약을 구현한다.
