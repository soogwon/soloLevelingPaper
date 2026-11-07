# #231 논문 상세의 기록 조회 — B 연결 범위

## A 계약 재사용

#231의 A 확인에 따라 기존 `AnnotationService.list`와 `list_annotation_page`를
그대로 사용한다. DB 스키마·저장소·CRUD·커서 형식은 바꾸지 않는다.

`PaperDetailService`에 Annotation 조회 서비스를 주입하면 `get_paper_detail`에
`annotation_page`가 추가된다. 주입하지 않은 기존 서비스는 이 필드를 생략하며
MCP 어댑터는 텍스트와 구조화 응답 모두에서 `null`로 정규화한다.
실제 MCP runtime에는 기존 Annotation 서비스를 주입한다.

```json
{
  "annotation_page": {
    "context_id": "선택한 학습 맥락",
    "items": [],
    "next_cursor": null
  }
}
```

- `items`는 기존 Annotation 모델 그대로이며 본문·종류·revision·저장 구간을 포함한다.
- 첫 페이지는 기본 20건이다. 빈 목록은 정상이며 미연결 상태와 다르다.
- 범위는 **선택한 context 전체**다. 상세의 `scope`, `limit`, `offset`, `summary_id`는
  요약 조회에 적용하며 기록의 섹션 필터나 페이지 크기로 사용하지 않는다.
- 다음 기록 페이지는 기존 `list_annotations`에 같은 `context_id`와
  `cursor=next_cursor`를 전달한다. 이 커서는 step/kind 필터 없는 목록 기준이다.
  필터 변경 시 cursor 없이 첫 페이지부터 다시 조회한다.
- 요약 페이지를 다시 조회하면 기록도 첫 페이지부터 읽는다. 두 목록의 페이지 진행은 별개다.
- 상세 메타데이터·요약 조회와 기록 조회는 각각의 읽기 스냅샷이다.
  둘을 한 DB 트랜잭션에서 읽었다고 보장하지 않는다. 기록 페이지 내부의
  목록·저장 구간은 기존 저장소의 동일 스냅샷 계약을 따른다.
- 조회 오류를 빈 기록 목록으로 바꾸지 않는다. 없는 context나 상세 출처 연결 오류에서는
  기록 조회를 시작하지 않는다.

## 저장 구간과 현재 위치의 구분

목록의 `quote_snapshot`은 저장 당시 인용문이며 현재 원문과의 일치를 확인한 값이 아니다.
상세 조회는 기록마다 `get_annotation`을 호출하지 않는다.

사용자가 원문 이동을 선택하면 C가 기존 `get_annotation`에 context·기록 ID·선택한
`source_position`을 전달한다. 해당 `segment_available`이 참이고 `source`가 있을 때만
원문 구간으로 이동한다. 현재 위치 확인에 실패해도 다른 청크나 번역문에서 추측하지 않는다.

## C의 남은 작업

#228 상세 카드는 현재 근거와 원문 구간이 정확히 일치하는 기록을 표시하고 작성한다.
상세 조회는 이 연결을 반영해 `annotations_not_connected` 제한을 제거한다.
근거별 SQL은 요약과 같은 읽기 트랜잭션에서 해당 구간의 건수와 미리보기 1건을 가져온다.
추가 3건 조회에는 현재 context와 evidence에 묶인 기존 Annotation 방식의 불투명 커서를 사용한다.
저장 응답을 확인하지 못하면 해당 근거의 작성이 잠긴다. 상세를 다시 조회해 기록을 확인한다.

아래 항목은 #235의 context 전체 기록 목록을 카드에서 제공할 때의 범위다.

1. 기록 첫 페이지 표시와 명시적인 다음 페이지/필터 동작을 연결한다.
2. 기록 선택 시에만 단건 원문 위치 확인을 연결한다.
3. 생성·수정·삭제는 사용자 명시 동작으로만 기존 도구를 호출한다.
4. 수정·삭제는 조회한 revision을 사용한다. `CONFLICT`는 재조회 안내하며 자동 재시도하지 않는다.
5. 성공 후 목록을 다시 읽는다. 생성 응답 유실은 자동 재생성하지 않고 목록부터 확인한다.
6. 도구 텍스트·구조화 응답을 카드 미지원 환경의 폴백으로 유지한다.

목록 전체의 현재 위치 상태를 미리 제공하는 batch projection이나 원격 API #126/#214의
새 계약은 이 변경에 포함하지 않는다. #231과 #62는 전체 UI 완료 전까지 열어둔다.

## 오프라인 테스트

```powershell
python -m pytest tests/unit/test_paper_detail.py tests/unit/test_annotation_service.py tests/unit/test_annotation_page.py tests/unit/mcp/test_detail_app.py tests/unit/mcp/test_annotations.py tests/unit/mcp/test_runtime.py tests/unit/mcp/test_runtime_lifecycle.py tests/integration/test_mcp_dashboard.py -q
```

합성 DB에서 context 격리, 요약과 독립된 기록 페이지·커서, 빈 목록, 원문 변경 후
스냅샷/위치 상태 분리, MCP 출력의 텍스트·구조화 데이터 일치와 조회 전후 DB 불변을 확인한다.
실제 API 호출·Desktop 설치/수동 검증은 수행하지 않는다.

## 2026-11-07 검증 결과

- 상세·MCP 출력 회귀: 최종 수정 후 27 passed.
- 메모 서비스·페이지 조회·MCP 메모 도구·런타임/종료 회귀: 143 passed.
- 초기 회귀에서 발견한 선택 필드의 텍스트/구조화 출력 불일치는 MCP 어댑터에서
  기본값을 명시하는 방식으로 수정하고 상세·MCP 전체를 다시 확인했다.
- Windows 제한 환경의 MCP 대기와 공용 pytest 임시 경로 접근 문제를 피해
  일반 실행 환경 및 전용 테스트 임시 경로에서 검증했다.
- DB 파일의 조회 전후 바이트 일치, 다른 context 기록 격리, 원문 변경 후 위치 이동 거부를 확인했다.
- 실제 API·사용자 데이터 변경·Desktop 재설치·커밋·푸시는 수행하지 않았다.

### 계약 재검토에서 추가한 확인

- A의 최신 코멘트(`6705264763`)와 기존 context·구간·revision 계약을 대조했다.
- 저장 요약이 있는 경우에도 연결 전후 요약 페이지·근거·리비전이 동일하고,
  교체된 요약의 이전 페이지 요청이 거부됨을 확인한다.
- 실제 MCP 메모리 전송에서 상세의 다음 커서를 기존 `list_annotations`에 전달하고,
  필터를 바꾼 커서는 기존 `INVALID_CURSOR` 계약으로 거부됨을 확인한다.
- 메모 조회 실패는 빈 목록으로 오인하지 않으며 본문·경로 없는 `INTERNAL_ERROR`를
  반환한다. 같은 서버에서 후속 정상 상세 조회가 복구되는지 함께 확인한다.
- 실제 stdio runtime의 대시보드 → 상세 조회에서 기록이 연결되는지 합성 DB로 확인한다.
- 초기 카드 표시·편집/삭제·재조회와 `annotations_not_connected` 제거는 여전히 C 작업이다.
- 재검토 회귀는 총 173개 사례다. 전체 실행에서 172개가 통과했고, 나머지 1개는
  SDK의 고정 도구명 접두어를 기대값에 반영한 뒤 추가 요약 회귀와 함께 재실행하여
  2 passed를 확인했다. 제품의 오류 처리 기준은 완화하지 않았다.
