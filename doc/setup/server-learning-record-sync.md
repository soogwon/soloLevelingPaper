# 서버 학습 기록 동기화 계약

논문 메타데이터 동기화로 받은 서버 `paper_id` 아래에 로컬 학습 맥락의 현재 진행 상태를
멱등 upsert한다. 이 API는 연결된 기기 Bearer 자격만 허용한다.

```http
PUT /v1/mcp/papers/{paper_id}/learning-records/{local_learning_record_id}
Authorization: Bearer <device access token>
Content-Type: application/json
```

```json
{
  "source_instance_id": "로컬 설치 ID UUID",
  "selected_version_id": "로컬 원문 버전 ID",
  "file_name": "paper.pdf",
  "last_position": {
    "chunk_id": "chunk-3",
    "pdf_page": 5,
    "section_id": "method",
    "section_number": "3.2",
    "section_title": "Method"
  },
  "last_studied_at": "2026-11-12T13:00:00Z",
  "completed_steps": ["preview", "structure"],
  "source_updated_at": "2026-11-12T13:00:00Z"
}
```

서버는 요청 사용자, `paper_id`, `source_instance_id`가 기존 서버 논문의 소유권과 모두 일치할
때만 저장한다. 같은 로컬 학습 기록 ID를 다시 보내면 갱신하며, 더 오래된
`source_updated_at` 요청은 최신 서버 상태를 덮어쓰지 않는다. 같은 갱신 시각과 같은 저장 값은 기존 리비전을 반환하고,
같은 갱신 시각에 저장 값이 다르면 `409 learning_record_conflict`를 반환한다. 상태는 서버가 다음처럼 확정한다.

- 학습일 없음: `not_started`
- 네 단계(`preview`, `structure`, `deep_read`, `review`) 완료: `completed`
- 그 외 학습 기록: `reading`

서버 논문 목록은 논문별 가장 최근 학습 기록 하나를 사용한다. 이 변경은 수신·조회 계약만
추가하며, 로컬 outbox에서 이 endpoint를 호출하는 작업은 다음 구현 조각에서 연결한다.
