# 외부 웹 논문 목록 API

외부 웹의 논문 아카이브는 서버가 발급한 HttpOnly 웹 세션 쿠키로 서버에 확정된 자기 논문만 조회한다.
기기 access token은 이 경로를 사용할 수 없다.

브라우저는 `credentials: include`로 요청하며 API 서버는 쿠키에 대응하는 세션 hash를 DB에서
검증한다. Bearer 자격은 연결된 로컬 기기용이며, 이 목록 API가 임의 쿠키 값이나 요청 본문의
사용자 ID를 인증 대체물로 신뢰하지는 않는다.

```http
GET /v1/papers?q=transformer&reading_status=unknown&sort=recent&page=1&limit=20
Cookie: slp_session=<opaque session token>
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

학습 기록이 동기화된 논문은 `last_studied_at DESC NULLS LAST, learning_record_id ASC`로 가장
최근 기록 하나를 선택해 원문 버전, 파일명, 마지막 위치, 학습일과 읽기 상태를 반환한다.
기록이 아직 동기화되지 않은 논문은 관련 필드를 `null`, 읽기 상태를 `unknown`으로 반환한다.
상태 필터는 이 서버 확정 값을 사용하며 프론트가 위치나 단계에서 상태를 추정하지 않는다.

목록은 `last_studied_at DESC NULLS LAST, paper_id ASC`로 고정한다. 검색·필터·페이지의 count와
item 조회에는 항상 같은 소유자 조건과 같은 repeatable-read snapshot을 적용한다.
