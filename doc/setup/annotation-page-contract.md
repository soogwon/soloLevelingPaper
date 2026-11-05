# 메모·하이라이트 목록·입력 제한 계약 (저장소 쪽, #56)

저장소 함수 `list_annotation_page`와 `create_annotation`의 제한값·오류 코드. B 서비스와 C의 MCP 오류 매핑이 이 코드를 그대로 쓴다.
오류는 `AnnotationValidationError.code`(고정 문자열)로 구분한다. code가 None이면 기존 형식 오류다.

## 목록
`list_annotation_page(db_path, context_id, step=None, kind=None, limit=None, cursor=None) -> AnnotationPage(items, next_cursor)`

- 정렬은 `(created_at, annotation_id)` 오름차순이다. 기본 20건, 최대 50건. 범위 밖(0·음수·51 이상·정수 아님)은 `INVALID_LIMIT`.
- `next_cursor`가 None이면 마지막 페이지다. LIMIT+1로 다음 페이지 유무를 판단해 빈 페이지가 따로 나오지 않는다.
- 커서는 불투명 문자열이며 발급한 context·step·kind의 고정 길이 SHA-256 해시가 들어 있다. 기존 형식의 유효한 커서도 같은 범위에서 이어 읽을 수 있다. 형식 오류이거나 다른 context·필터에서 쓰면 `INVALID_CURSOR`.
- 커서는 서버가 발급한 형식(패딩 없는 Base64 URL)만 받는다. 알파벳 밖 문자가 붙거나 섞인 입력, 패딩·끝 비트가 다른 변형,
  UTF-8로 쓸 수 없는 위치 문자열(단독 서로게이트)은 모두 `INVALID_CURSOR`이며 오류에 입력값을 담지 않는다.
- 커서는 기준 기록의 위치 값(created_at, annotation_id)으로 이어 읽는다. 기준 기록이 지워져도 이어진다.
- 뒤쪽 페이지도 앞쪽과 비슷한 비용이다: `(created_at, annotation_id) > (기준시각, 기준ID)`로
  인덱스 `idx_annotations_page(context_id, created_at, annotation_id)`의 커서 위치에서 탐색을 시작한다(같은 시각 경계의 누락·중복 없음).
- 한 페이지의 기록과 구간은 같은 읽기 스냅샷에서 읽는다. 이미 지나간 위치에 새로 생긴 기록은 첫 페이지부터 다시 읽을 때 보인다.
- context가 없으면 `ResourceNotFoundError`(커서 검사는 그보다 먼저다).
- 기존 `list_annotations`(전체 목록)는 호환을 위해 남아 있다. 공개 인터페이스는 페이지 함수를 쓴다.

## 입력 제한 (`create_annotation`, 저장 전에 검사)
| 항목 | 값 | 위반 시 |
|---|---|---|
| 본문 | 5,000자 | 기존 형식 오류(code=None) |
| 구간당 인용문 | 2,000자 | 기존 형식 오류 |
| 기록당 구간 수 | 20개 | 기존 형식 오류 |
| 기록당 인용문 합계 | 8,000자 | `QUOTE_TOTAL_TOO_LONG` |
| 중복 구간 | `(chunk_id, target, translation_revision_id, start, end)`가 모두 같음 | `DUPLICATE_SEGMENT` |

- 원문 구간의 번역 리비전은 None으로 비교한다. 일부만 겹치는 구간은 허용한다. 중복은 한 기록 안에서만 본다.
- 위반하면 아무것도 저장하지 않는다.
- 번역 구간(`target='translation'`) 거부(`TRANSLATION_NOT_SUPPORTED`)는 B 서비스가 저장소 호출 전에 한다.
  저장소는 번역 리비전별 구간을 계속 지원한다.
- 본문·인용문은 오류 메시지와 로그에 담지 않는다.

## B 서비스에서 C가 사용할 계약

- `AnnotationService.list(context_id, *, step, kind, limit, cursor)`의 키워드 인자는 모두 선택입니다. 응답은 `AnnotationPage`이며 목록은 `items`, 다음 페이지 기준은 `next_cursor`에서 읽습니다.
- 생성·수정에 번역 구간이 포함되면 저장소를 호출하기 전에 요청 전체를 거부합니다. 수정 가능한 필드는 본문과 학습 단계뿐입니다. 수정에 원문 구간 목록을 명시하는 것도 일반 형식 오류입니다. 위치를 바꾸려면 새 기록을 만듭니다.
- C는 `AnnotationValidationError.code`로 위의 고정 사유를 구분합니다. 일반 형식 오류는 `code=None`이며, 누락된 맥락의 목록 조회는 `ResourceNotFoundError`입니다.
