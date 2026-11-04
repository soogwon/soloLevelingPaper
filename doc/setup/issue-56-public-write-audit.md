# #56 C 공개 쓰기 경로 점검

## 검토 기준과 결론

- main: `ea84aa9cd8645959e7adb7201bdbcb157e29fb14`
- PR #150: `3e4b80f8e3227f6391cfc3366cae9739a601ff3b` (미병합)
- 환경: macOS 26.7.1 arm64 / Python 3.12.14
- 요청 근거: https://github.com/soogwon/soloLevelingPaper/issues/56#issuecomment-6605362444

현재 main과 PR #150에는 메모·하이라이트의 공개 MCP 생성·수정·삭제 도구가 없다.
따라서 모든 공개 쓰기가 B 서비스를 경유한다고 검증한 상태는 아니다.
현 시점의 공개 우회 경로는 발견하지 못했으며, 공개 도구 연결 시 검증을 추가해야 한다.
제품 코드·DB·Desktop 설정은 변경하지 않았다.

## 경로 확인

| 경계 | 확인 결과 |
| --- | --- |
| MCP 등록·런타임 조립 | AnnotationService 및 메모 저장소 연결 없음 |
| 공개 도구 목록 | PR #150의 실제 stdio 회귀에서 기존 16개 도구 확인. 메모 CRUD 없음 |
| UI 소스 | 메모 생성·수정·삭제 호출 경로 발견하지 못함 |
| 상세 조회 | annotations_not_connected 제한을 반환 |
| PR #150 서비스 | create/update/delete가 각각 저장소 함수를 호출 |
| 서비스 밖 제품 호출 | 저장소 자체를 제외한 src 검색에서 해당 쓰기 함수 호출은 위 서비스 세 곳뿐 |

제품 소스에서 함수명 및 annotations 테이블 쓰기 SQL을 검색하고 MCP 등록·런타임·UI를
대조했다. 이는 검토한 코드의 경로 확인이며 임의 동적 코드나 미래 변경의 보증은 아니다.

## 실행 결과

PR 커밋을 별도 임시 디렉터리에 추출하고 아래 기존 검사를 실행했다.

```sh
PYTHONPATH=src python -m pytest tests/unit/test_annotation_service.py tests/unit/test_annotations.py tests/integration/test_mcp_stdio.py -q
```

서비스·저장소 50개, stdio 2개가 통과했다. 추가 임시 검사 3개와 합쳐 **55개 통과**다.
추가 검사는 store.create_annotation을 호출 기록용 대역으로 바꿔 다음을 확인했다.

1. 번역 구간 요청: AnnotationValidationError, 저장소 호출 0회.
2. 원문 뒤에 번역 구간이 섞인 요청: AnnotationValidationError, 저장소 호출 0회.
3. 원문 구간 요청: context·구간 순서를 유지해 저장소 호출 1회, 결과 그대로 반환.

추가 검사는 공개 MCP 호출이 아니라 B 서비스 경계 검사다. 임시 보조 테스트이며
저장소 회귀 테스트에 포함하지 않았다. 기존 서비스 테스트는 번역 요청 거부 후 DB가
비어 있는 것과, 저장소로 만든 기존 번역 구간의 조회 결과가 unavailable인 것도 확인한다.
실제 논문·외부 API·사용자 DB는 사용하지 않았다. 신규 공개 CRUD의 실제 Desktop·wheel·
Windows/Linux 검증을 의미하지 않는다.

## 공개 연결 전에 남은 계약

| 항목 | 현재 상태 | 다음 작업 |
| --- | --- | --- |
| 생성 경로 | 서비스는 번역 구간을 저장 전에 거부 | C의 모든 공개 생성은 B 서비스만 호출. 저장소 직접 호출 금지 |
| 번역 오류 | 현재는 일반 AnnotationValidationError 문구 | B의 TRANSLATION_NOT_SUPPORTED 제안 확정 후 고정 코드 매핑. 문구 파싱으로 판별하지 않음 |
| 목록 | 전체 목록 반환 | A/B가 SQL 페이지 조회·정렬·필터/맥락에 묶인 커서 계약 제공 후 연결 |
| 입력 제한 | 중복·총 20,000자 제한은 미구현 | A/B 공통 검증 확정 후 C 입력과 일치 확인. C만 별도 규칙을 구현하지 않음 |
| 부분 수정 | 생략과 명시적 null 구분 | C는 전달된 필드만 서비스에 넘김. step 생략을 null로 바꾸지 않음 |
| 충돌·삭제 | expected_revision 사용 | 충돌 시 재조회 안내, 최신 revision으로 자동 재시도하지 않음 |
| 위치 | 상세의 구간별 가용성 제공 | 목록만으로 이동 가능 표시 금지. 코드 포인트 오프셋·구간 순서 보존 |
| 생성 응답 유실 | 멱등 생성 키 없음 | 자동 재생성 금지. 같은 본문으로 동일 기록을 추정하지 않음 |

페이지 기본 50/최대 100, created_at·annotation_id 정렬, INVALID_CURSOR,
DUPLICATE_SEGMENT, QUOTE_TOTAL_TOO_LONG은 B의 제안이며 A와 합의된 최종 계약으로
기록하지 않는다. 저장소 신규 번역 쓰기 제한은 B가 A/#30 후속으로 제안한 범위다.

## C 후속 통합 검증

서비스 계약 인계 후 공개 도구 호출에서 B 서비스 호출과 저장소 직접 우회 없음을
확인한다. 원문/번역 혼합, 다른 context, revision 충돌, 생략/null, 페이지 커서,
입력 오류의 본문 비노출, 재연결 후 보존을 함께 검증한다.
이번 결과로 #56을 종료하거나 공개 MCP 구현이 완료됐다고 판단하지 않는다.
