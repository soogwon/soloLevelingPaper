# 외부 웹 논문 목록 API

외부 웹의 논문 아카이브는 웹 세션 Bearer 자격으로 서버에 확정된 자기 논문만 조회한다.
기기 access token은 이 경로를 사용할 수 없다.

현재 HTTP 서버의 웹 주체 전달 방식은 검증된 Bearer access token이다. External site의 실제
client는 HttpOnly 쿠키 세션을 전제로 작성돼 있으므로, 운영 연결 전에 로그인 callback·쿠키
세션 adapter를 추가해야 한다. 이 목록 API가 임의 쿠키 값이나 요청 본문의 사용자 ID를 인증
대체물로 신뢰하지는 않는다.

```http
GET /v1/papers?q=transformer&reading_status=unknown&sort=recent&page=1&limit=20
Authorization: Bearer <web session access token>
```

`q`는 앞뒤 공백을 제거한 뒤 제목에 대소문자 구분 없이 부분 일치시킨다. `%`, `_`, `\`는
와일드카드가 아니라 검색 문자로 처리한다. `page`는 1부터 시작하고 `limit`은 1~100이며,
범위를 넘는 페이지도 요청한 페이지 번호와 전체 개수를 유지한 빈 `items`를 반환한다.

```json
{
  "items": [
    {
      "paper_id": "server-paper-id",
      "title": "Paper title",
      "authors": null,
      "file_name": null,
      "published": null,
      "selected_learning_record_id": null,
      "selected_version_id": null,
      "last_position": null,
      "last_studied_at": null,
      "reading_status": "unknown",
      "collection_id": null
    }
  ],
  "page": 1,
  "page_size": 20,
  "total_count": 1,
  "total_pages": 1
}
```

현재 첫 동기화 조각은 논문 메타데이터만 서버에 저장한다. 원문 버전, 학습 기록, 위치, 읽기
상태와 컬렉션을 로컬 값에서 추정하지 않는다. 따라서 해당 필드는 `null`, 읽기 상태는
`unknown`으로 반환한다. `reading_status=unknown`에는 현재 논문이 포함되지만 `reading`,
`completed`, `not_started` 필터에는 명시 상태가 없는 논문이 포함되지 않는다.

모든 학습일이 없는 현재 데이터는 논문 ID 오름차순으로 고정한다. 학습 기록 동기화가 추가되면
같은 API에서 `last_studied_at DESC NULLS LAST, paper_id ASC`로 확장한다. 검색·필터·페이지의
count와 item 조회에는 항상 같은 소유자 조건을 적용한다.
