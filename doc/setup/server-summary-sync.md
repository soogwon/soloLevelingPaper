# 서버 요약 동기화 계약

사용자가 동기화를 승인한 뒤 로컬 MCP가 저장된 논문 요약을 중앙 서버에 전달하고 외부 사이트 상세
조회에서 사용할 수 있게 한다.

## 쓰기 API

`PUT /v1/mcp/papers/{paper_id}/learning-records/{local_record_id}/summaries/{local_summary_id}`

- device 자격만 사용할 수 있다.
- 요청의 `source_instance_id`와 서버에 등록된 논문 및 학습 기록의 source가 모두 같아야 한다.
- 요청에는 요약의 원문 버전인 `selected_version_id`를 포함한다(버전 미지정 시 `null`). 학습 기록의 현재 버전과 다르면 409를 반환하며 기존 요약 ID를 다른 버전에 재사용할 수 없다.
- 현재 외부 상세 페이지에 필요한 전체 논문 요약만 받으므로 `scope`는 `paper`만 허용한다.
- `author_type`은 현재 MCP 생성 요약을 뜻하는 `mcp`와 향후 사용자 작성 요약을 위한 `user`를 허용한다.
- 각 항목은 `claim`, `method`, `result`, `limitation` 중 하나이며 본문과 하나 이상의 `evidence_ids`를 가진다.
- PDF, 추출 원문, 근거 본문은 이 API로 받지 않는다.
- 동일 source와 local summary ID는 `source_updated_at`이 증가할 때만 갱신한다. 같은 시각의 다른 본문은
  `summary_conflict`로 거부한다.

## 읽기 API

`GET /v1/papers/{paper_id}`는 선택된 학습 기록에 연결된 최신 `paper` 범위 요약을 `summary`에 담는다.
동기화된 요약이 없으면 기존과 같이 `null`이다. 응답에는 `summary_id`, `created_at`, `author_type`,
그리고 근거 ID를 보존한 `items`가 포함된다.

## 로컬 승인과 전송 순서

확인 카드는 요약 본문 및 근거 ID 전송을 별도로 고지하며 승인 스냅샷에는 요약 전체의 해시를 포함한다.
승인 뒤 요약이 바뀌면 해당 child 작업은 `payload_changed` 충돌로 멈춘다. outbox는 논문, 학습 기록,
요약 순서로 전송하며 연결된 학습 기록이 성공하지 않은 요약은 서버로 보내지 않는다.
