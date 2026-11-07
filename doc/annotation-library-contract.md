# 전체 메모·하이라이트 저장소 계약 (#206)

`list_annotation_library`는 단일 로컬 DB에 저장된 모든 학습 맥락의 메모와 하이라이트를 읽기 전용으로 조회한다.
M1에는 사용자 계정 테이블이 없으므로 DB 파일 하나가 로컬 프로필의 격리 경계다. 서버 다중 사용자 범위는 이 계약에
암묵적으로 추가하지 않는다.

## 입력

- `kind`: `note` 또는 `highlight`
- `query`: 본문, 저장된 인용문 스냅샷, 논문 제목의 부분 검색
- `created_from`: 포함하는 ISO 8601 시각
- `created_before`: 제외하는 ISO 8601 시각
- `limit`: 기본 20, 최대 50
- `cursor`: 이전 응답의 불투명 커서. 모든 필터에 묶이므로 다른 조건에서 재사용하면 `INVALID_CURSOR`다.

## 출력과 정렬

`(created_at DESC, annotation_id DESC)`의 결정적 최신순이다. 항목에는 기존 `Annotation` 전체와 `paper_id`,
`paper_title`, `version_id`, 경로를 제거한 `file_display_name`, 중복을 제거한 `pdf_pages`가 포함된다. 저장 당시 `body`와
`quote_snapshot`은 원문이 바뀌어도 보존한다.

`segment_available`은 각 저장 구간이 현재 원문 또는 해당 번역 리비전과 여전히 같은지 나타낸다.
`location_status`는 구간이 없으면 `not_applicable`, 모두 유효하면 `available`, 하나라도 다르면 `unavailable`이다.
따라서 C의 전체 기록함 UI는 저장 내용과 “원문으로 이동 가능” 상태를 혼동하지 않아야 한다.
