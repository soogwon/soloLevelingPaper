# 원격 MCP 공개 Host 경계 — #409

#413의 제품 HTTP 스파이크 위에 적용하는 방어다. 공개 TLS·Claude 연결 완료를 뜻하지 않는다.

- SDK DNS rebinding 보호를 유지하며 allowed_hosts/allowed_origins를 명시한다.
- 인증보다 먼저 Host 하나를 확인한다. 비허용 Host, 중복 Host, 사용자 정보·와일드카드·비허용 포트 설정을 거부한다.
- X-Forwarded-Host는 없거나 원래 Host와 같아야 하며, X-Forwarded-Proto는 없거나 https여야 한다.
  이 헤더로 원래 Host나 인증 resource를 덮어쓰지 않는다.
- RFC Forwarded는 신뢰 계약이 없어 거부한다. 프록시가 이를 사용한다면 A와 실제 전달 방식을 먼저 확인한다.
- Origin은 설정된 정확한 origin만 허용한다. 실제 Claude가 보내는 값을 확인하지 않고 허용 범위를 넓히지 않는다.
- 잘못된 Host/forwarded는 421, Origin은 403의 고정 응답이다. 원본 헤더를 거부 로그에 남기지 않는다.
- 보호 리소스 metadata도 같은 경계를 적용한다. 기존 /health·웹·API에는 이 추가 검사를 적용하지 않는다.

스테이징 후보 resource는 https://solo-leveling-staging.soogyung.com/mcp 이다.
프록시는 원래 공개 Host와 Authorization을 보존하고 외부 입력 forwarded 헤더를 정리해야 한다.
배포 환경에서 proxy header 자동 신뢰가 설정돼 있다면 허용 프록시 범위를 별도 점검한다.

롤백은 REMOTE_MCP_SPIKE_ENABLED=false로 재배포하여 MCP 경로를 닫는 방식이다.
허용 도메인 변경 시 resource·audience·metadata·Claude 등록 URL을 함께 대조하고 회귀 테스트한다.
보호를 꺼서 연결 실패를 우회하지 않는다.

자동 검증: 공개 Host의 정상 tools/list, 토큰 유효/무효 모두에 대한 Host 거부,
중복 Host·위조 forwarded·metadata 경계, 기존 웹 OIDC 회귀를 포함한다.
실제 Render 헤더·HTTPS 443·Claude initialize 및 로그 표본 검증은 A·C의 후속 배포 검증이며 #409는 열린 상태로 유지한다.
