# #229 B — 원문 키워드·문장 추출 v1

## 실행 시점과 담당 범위

등록·상세 조회·상태 조회에 추출을 연결하지 않는다. 사용자가 명시적으로 실행하는
미리보기와 저장만 제공한다. API·임베딩·번역·요약 생성기를 호출하지 않는다.
A의 `save_paper_insights`·`get_paper_insights`와 원자적 묶음 교체 계약을 재사용한다.
DB 스키마·MCP 입력 계약은 변경하지 않는다. 재검토에서 발견한 경쟁 저장 방어를 위해
A 저장 함수에 선택적 예상 묶음·context 검사만 추가했다. 이 확장은 B의 보완 제안으로 A 리뷰 대상이며,
조건을 생략하는 기존 호출의 교체 동작은 유지한다.

## 선정 규칙

1. context가 고정한 version/parse/translation/index 연결을 확인하고 해당 파싱의 원문을 읽는다.
   section scope는 정확한 section_id로 제한한다. 새 파싱이나 인접 섹션으로 확장하지 않는다.
   번역 실패 여부와 무관하게 원문 후보를 읽으며 번역문을 원문으로 대체하지 않는다.
2. 문장 끝 부호와 기존 조각 판정으로 후보를 찾는다. 소문자로 시작하는 조각, 문장 부호 없이
   끝나는 꼬리, 숫자만 있는 줄을 포함하는 후보는 사용하지 않는다. 다른 청크/페이지와 합치지 않는다.
   길이 30~800 코드 포인트, 단어 5개 이상의 후보를 사용한다.
3. 실제 섹션 제목이 References/Bibliography/Acknowledgments/참고문헌 등인 구간을 제외한다.
   섹션 제목이 없으면 ID의 slug로 제목을 추측하지 않는다.
4. 공백·대소문자를 비교용으로만 정규화해 중복 문장을 제거한다. 원문 문자열은 변경하지 않는다.
5. 단어 3자 이상(하이픈/아포스트로피 포함 가능), 영어 기능어 제외 후 서로 다른 후보 문장에
   2회 이상 등장하는 용어를 빈도순으로 최대 8개 선정한다. 동점은 첫 출현 순이다.
6. 문장별 용어 빈도 합계를 점수로 사용해 최대 5개를 선정한다. 동점은 원문 순이다.
   이 결과는 규칙 기반 대표 후보이며 논문의 핵심/중요도를 의미 검증한 결과가 아니다.

`max_keywords`, `max_sentences`, `min_sentence_frequency`를 producer_version에 함께 기록한다.
기본값은 `extractive-v1:k8:s5:f2`다. 규칙 자체 변경은 v1을 변경해 추적한다.

출력은 원문의 정확한 부분 문자열이며 `text == quote_original`, Unicode 코드 포인트 기준
`[start_offset, end_offset)`를 유지한다. 각 항목은 `content_kind=extracted`,
`producer=extractor`, `translation_revision_id=None`이다. 저장소가 원문·소속·구간을 다시 검증한다.
요약 claim을 중요 문장으로 재사용하거나 한국어로 임의 재작성하지 않는다.

## 상태와 저장

미리보기의 `available`은 두 종류의 후보가 있다는 뜻이며 중요도·사실성 보증이 아니다.
한 종류만 있으면 `partial`, 적격 후보가 없으면 `not_available`이다. DB 연결/출처 오류를
적격 후보 없음으로 숨기지 않는다.

- `PaperInsightService.preview`는 읽기 전용이며 DB 파일을 생성하거나 이관하지 않는다.
- `save(preview)`는 별도 명시 작업이며 빈 결과를 저장하지 않는다.
- 이미 저장된 결과는 기본적으로 교체를 거부한다. `replace_existing=True`로만 묶음 전체를 교체한다.
  과거 model/user 작성 항목도 같은 묶음이라 교체 대상임을 유의한다.
- 미리보기의 원문 스냅샷은 불변이며 저장 전에 같은 규칙으로 선정 결과를 재계산한다.
  선택 항목을 수정한 뒤 extractor 결과인 것처럼 저장하지 않는다.
- 미리보기 시점의 기존 collection_id도 같은 읽기 스냅샷에 고정한다. 미리보기 이후 다른
  작성자가 만든 결과는 `--replace`만으로 덮어쓰지 않으며, 새 미리보기부터 다시 시작해야 한다.
- 저장 전에 원문·scope·제목·context 스냅샷을 다시 읽어 비교한다. 그 다음 실제 저장 트랜잭션에서
  선택 구간/인용문과 고정된 context 연결을 다시 확인한다. 전체 원문에 대한 장기 쓰기 잠금은 유지하지 않는다.
- 기존 출처가 손상됐으면 실패하며 기존 묶음을 지우지 않는다.
- `expected_collection_id=None`은 신규 저장만 허용하고, 기존 묶음 ID는 확인한 묶음일 때만
  교체한다. 이 확인은 `BEGIN IMMEDIATE` 쓰기 트랜잭션 안에서 삭제 전에 수행한다.
  경쟁 저장이 발견되면 `PaperInsightConflictError`로 중단하며 자동 재시도하지 않는다.
- B의 저장은 version/parse/translation/index 연결도 같은 쓰기 트랜잭션에서 예상값과 대조한다.
  context의 번역 리비전은 연결 확인용이며 원문 추출 항목에 번역 출처로 붙이지 않는다.
- 조건을 생략하는 기존 A 호출은 기존의 원자 교체 계약을 유지한다. 이 확장이 기존 호출을
  강제로 create-only로 바꾸거나 원격/MCP 동시 실행 계약까지 확정하는 것은 아니다.

## 로컬 테스트

서버를 종료하고 복사한 테스트 DB의 기존 context_id를 사용한다. CLI 기본 출력은 건수·상태·
규칙 버전뿐이며 원문·본문·경로·식별자를 로그에 출력하지 않는다.

경쟁 저장은 고정 코드 `INSIGHT_EXTRACTION_CONFLICT`, 그 밖의 실행 실패는
`INSIGHT_EXTRACTION_FAILED`로 안내한다. 실패 시 자동 재실행하지 않는다.

```powershell
# 읽기 전용 미리보기 — 저장 없음
python -m solo_leveling.interfaces.cli.extract_paper_insights --db <테스트DB> --context-id <context_id>

# 명시 저장 — 이미 결과가 있으면 실패
python -m solo_leveling.interfaces.cli.extract_paper_insights --db <테스트DB> --context-id <context_id> --save

# 기존 결과 전체 교체를 의도한 경우에만 지정
python -m solo_leveling.interfaces.cli.extract_paper_insights --db <테스트DB> --context-id <context_id> --save --replace
```

섹션 제한은 `--scope <section_id>`다. 실제 항목 검토는 Python에서 `service.preview(...).items`로
확인할 수 있고, 저장 후에는 기존 `get_paper_detail`의 구조화 응답에서 확인한다.
현재 카드의 키워드·중요 문장 렌더링은 C의 후속 작업이다.

```powershell
python -m pytest tests/unit/test_paper_insight_service.py tests/unit/test_paper_insights.py tests/unit/test_paper_detail.py -q
```

## 한계와 후속

구두점·소문자 경계 판정은 형식 추정이다. 완결성이나 의미 일치를 보장하지 않으며 PDF의
표·수식·제목 노이즈를 모두 제거하지 않는다. 영어 기능어 기반이라 한국어 형태소 분석·
전문 용어 묶음 추출은 하지 않는다. 기본 개수는 선정량이며 논문의 모든 정보를 뜻하지 않는다.
원문 청크는 삭제하거나 줄이지 않는다.

모델 생성·한국어 해석, 모델 선택/실제 API 호출, MCP 생성 도구, 카드 렌더링·Desktop 검증은
이 변경에 포함하지 않는다. #229와 #62는 이 서비스 추가만으로 종료하지 않는다.

## 초기 검증

Windows 합성 DB에서 새 서비스·CLI 및 기존 출처 저장·상세 조회 테스트 **63 passed**.
원문 구간/Unicode/공백 보존, 범위 고정, 중복 제거, 빈 결과 보호, 출처 변경과 버전 불일치,
명시 교체 및 CLI 본문 비출력을 확인했다. 실제 API·사용자 DB·Desktop은 사용하지 않았다.

## 재검토 보완 및 검증

- 미리보기 이후 변경된 결과와 확인/저장 사이의 경쟁 저장을 구분해 모두 덮어쓰지 않는다.
- A 저장 함수의 선택적 collection/context 조건을 실제 쓰기 트랜잭션에서 검증한다.
  신규 저장 2개를 동시에 실행해 결과 한 묶음만 남는 것을 확인했다.
- 미리보기 항목 변조·선정에 쓰인 원문 변경·context 변경을 거부하고 기존 묶음을 보존한다.
- 기존 인자만 사용하는 A의 원자 교체와 과거 스키마 읽기·이관 동작도 회귀 검증했다.
- 최종 **85 passed**: 새 서비스·조건부 저장·출처 검증·기존 상세·영속성·MCP·실제 stdio.
  추가 조건은 기존 A 합의의 변경이 확정된 것으로 간주하지 않으며 PR에서 A의 검토 범위를 명시해야 한다.
- 실제 API 호출·커밋·푸시는 수행하지 않았다.
