# 외부 웹 논문 상세 API

```http
GET /v1/papers/{paper_id}?learning_record_id={record_id}&version_id={version_id}
Cookie: slp_session=<opaque session token>
```

서버는 로그인 사용자가 소유한 논문만 조회한다. 목록에서 받은 `learning_record_id`를 전달하면
그 기록을 선택하며, `version_id`도 전달한 경우 기록의 원문 버전과 같아야 한다. 둘 다 없으면
목록과 동일하게 마지막 학습일 최신순, 기록 ID 오름차순으로 기록 하나를 선택한다.

현재 서버에 동기화된 것은 논문 기본정보와 학습 진행뿐이다. 따라서 응답은 저장된 제목·저자·
연도·파일명·위치·학습일·상태를 제공하되 다음 값을 명시적으로 반환한다.

- `summary: null`: 조회 중 새 요약을 만들거나 다른 버전의 요약을 대신 사용하지 않는다.
- `file_access.available: false`, `reason: not_uploaded`: PDF 원문은 아직 서버에 없다.
- `resume.available: false`: 기록이 있으면 `local_client_required`, 없으면
  `learning_record_missing` 사유를 반환한다. 서버 기록만으로 현재 PC의 준비 상태를 추정하지 않는다.

요청한 논문·기록·버전이 사용자 범위에 없으면 존재 여부를 노출하지 않고 `404 paper_not_found`를
반환한다. 기록과 버전이 서로 다르면 `409 paper_selection_conflict`를 반환한다.
