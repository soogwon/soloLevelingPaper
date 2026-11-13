# 서버 논문 메타데이터 등록

첫 논문 동기화 API는 연결된 로컬 기기가 선택한 논문의 메타데이터만 중앙 서버에 등록한다. PDF, 추출 원문, 번역문, 벡터와 로컬 절대경로는 전송하지 않는다.

```http
POST /v1/mcp/papers
Authorization: Bearer <device access token>
Idempotency-Key: <stable operation key>
```

본문에는 안정적인 `source_instance_id`, `local_paper_id`, nullable `title`, `authors`, `publication_year`, 실제 `original_created_at`을 보낸다. 사용자 ID나 현재 기기 ID는 본문에서 받지 않는다.

서버는 다음 두 경계를 별도로 적용한다.

- `(owner_user_id, source_instance_id, local_paper_id)`가 같으면 같은 서버 논문 ID를 반환한다.
- `(owner_user_id, operation, Idempotency-Key)`가 같고 canonical payload hash도 같으면 저장된 201 응답을 재생한다.

같은 멱등 키에 다른 payload를 보내면 `409 idempotency_conflict`로 거부한다. 다른 사용자는 같은 source/local ID 또는 같은 멱등 키를 사용해도 별도 범위에 저장된다. 제목이나 저자만 같다는 이유로 자동 병합하지 않는다.

등록 제목이 비어 있으면 로컬의 최신 원본 파일명을 제목 fallback으로 전송한다. 이미 등록된 서버 논문의
제목이 NULL인 경우에는 이후의 새 동기화 작업이 이 fallback 제목만 채우고 revision을 증가시킨다.
기존에 값이 있는 제목이나 다른 메타데이터는 등록 API가 자동으로 덮어쓰지 않는다. 일반적인 수정은
서버 revision을 조건으로 검사하는 별도 API에서 구현한다.

로컬 세로 조각은 SQLite의 `server_sync_consents`, `sync_outbox`,
`server_object_mappings`를 사용한다. 명시적인 논문별 승인으로 작업을 만든 뒤
`AuthenticatedServerClient`가 작업 ID를 `Idempotency-Key`로 사용한다. 전체 payload는
outbox에 저장하지 않고 hash만 보존하며, 전송 직전 현재 논문에서 다시 만든 payload가 달라지면
자동으로 새 키를 발급하지 않고 충돌로 남긴다. 성공 응답의 서버 ID·revision 매핑과 outbox 완료는
같은 로컬 트랜잭션으로 확정한다.

`SERVER_API_URL`이 설정된 로컬 MCP는 다음의 명시적 실행 경계만 노출한다.

1. `prepare_paper_server_sync`: 논문 ID·제목과 전송/제외 항목을 카드로 확인하고, 응답 유실 후
   조회에도 재사용할 operation ID를 전송 전에 발급한다.
2. `sync_paper_to_server`: 카드 내부에서만 호출되며 일회성 확인 토큰을 소비하고 동의와 outbox를 만든다.
3. `get_paper_server_sync`: 기존 operation ID의 로컬 상태만 조회하며 자동 재전송하지 않는다.
4. `cancel_paper_server_sync`: 확인 토큰만 폐기하며 동의나 outbox를 만들지 않는다.

계정 연결과 논문 등록은 동기화 동의가 아니다. 런타임 시작, 상태 조회, 안내 카드 열기만으로는
기존 논문을 업로드하지 않는다. 현재는 사용자가 승인한 한 번의 전송만 수행하며 백그라운드 자동
재시도와 이후 기록 자동 동기화는 활성화하지 않는다.
