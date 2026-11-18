# #502 인증된 메모 복구 HTTP 준비

## 범위

기존 생성 원장의 exact 조회·초기 입력 복구·수신 확인을 웹 세션 인증 경계에 연결했다.
`create_app(remote_annotation_recovery=AnnotationRecoveryService(store, service_id))`로
명시적으로 주입한 경우에만 경로를 등록한다. 기본 앱·환경변수 조립은 연결하지 않는다.
새 메모 생성, 실제 staging 변경, AI 호출은 포함하지 않는다.

- A: owner는 검증된 principal, service는 서버 조립 시 고정한 논리 서비스 ID다.
  생성 시 썼던 ID와 반드시 같아야 한다. 기기·모델·요청 본문에서 받지 않는다.
- B: 기존 저장소의 원자적 조회·현재 revision·삭제 마스킹·확정 계약을 그대로 사용한다.
  초기 입력의 공백·유니코드·배열 순서를 바꾸지 않는다. 자동 생성/재시도가 없다.
- C: 웹 세션만 허용하고 쿠키 POST에는 기존 CSRF 검사를 적용한다.
  수신 확인은 반환된 annotation ID와 revision이 현재 값과 일치해야 한다.
  제품 카드가 응답 범위와 세션을 확인한 뒤 confirm을 호출하는 실제 연결은 후속이다.

## 경로

접두사: `/v1/remote-annotations/contexts/{context_id}`

| 메서드·경로 | 입력 | 결과 |
| --- | --- | --- |
| GET `/requests/{request_key}` | UUID context·기존 키 | 현재 annotation, state, recovery=exact, reused=true |
| POST `/recover` | 초기 kind/body/segments/step/creation_origin | 유일한 미확정 결과 또는 404/409 |
| POST `/requests/{request_key}/confirm` | annotation_id·양의 정수 revision | confirmed=true 또는 409 |

scope를 요청 body로 주입하면 422다. 확인 실패는 누락·범위 불일치·오래된 revision을
같은 409로 반환하여 존재 여부를 구분하지 않는다. 복구 모호성은 409, 조회 누락은 404,
형식 오류는 422, 저장소 장애는 503이다. 오류에는 본문·인용문·예외 메시지를 넣지 않는다.
성공 및 라우터 오류는 no-store다. 삭제 기록은 state=deleted와 비워진 본문/구간을 반환한다.

## 로컬 검증

격리 PostgreSQL과 합성 데이터로 HTTP→저장소 복구, 확정 재전송, 다른 계정 404,
삭제 마스킹, 오래된 revision 거절, 단일 기록 유지 및 기존 저장소 회귀를 검증했다.
단위 테스트는 인증, 기기 거절, 쿠키 CSRF, 신뢰할 수 없는 scope 거절,
고정 오류 코드, 기본 앱의 경로 미등록을 확인한다.

## 남은 승인·연결

- 원격 생성·저장 동의·write scope와 실제 제품 카드 송수신 계약 확정
- 인증된 원격 MCP 전송 어댑터 연결 (이 HTTP 경계는 웹 세션 전용)
- 전용 DB 역할의 조회·부모 잠금·확정 UPDATE 최소 권한 실측
- staging 조립·배포 승인 및 실제 두 계정/재로그인/늦은 응답 검증

따라서 #502 종료나 원격 제품 연결 완료를 의미하지 않는다.
