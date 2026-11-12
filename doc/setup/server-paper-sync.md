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

현재 API는 최초 등록만 수행하며 기존 논문의 메타데이터를 자동 갱신하지 않는다. 수정은 서버 revision을 조건으로 검사하는 별도 API에서 구현한다.

다음 단계에서는 로컬 SQLite에 `server_object_mappings`와 `sync_outbox`를 추가하고, `AuthenticatedServerClient`로 이 API를 호출해 성공 응답과 outbox 완료를 같은 로컬 트랜잭션에 저장한다.
