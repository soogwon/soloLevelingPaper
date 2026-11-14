# 검수 KG 학습 경로

상태: 로컬 MCP 구현 / 2026-11-14

팀이 검수한 개념·선수 관계 시드(`src/solo_leveling/knowledge/seed.json`)로 학습 맥락의
논문·목적에 맞는 개념 학습 순서를 돌려준다. 08번 데이터 모델의 GraphRevision·PrerequisiteRelation·
GoalConceptPlan을 마감 전 범위로 줄인 구현이다.

## 08번 설계와 다른 점

| 08번 설계 | 이번 구현 | 이유 |
|---|---|---|
| SQLite 테이블 + GraphRevision 게시 | 패키지에 포함한 JSON을 시작 시 메모리에 적재 | 사용자와 무관한 정적 데이터. 로컬 MCP와 중앙 서버가 같은 파일을 공유 |
| draft → published 검수 | PR 리뷰가 검수, CI 테스트가 검증 | 검수 기록은 git 이력으로 대체 |
| 논문 키 parse_revision_id | PDF SHA-256(`paper_versions.file_hash`) | 다른 PC에서 같은 PDF를 등록해도 연결 |
| GoalConceptPlan의 required 저장 | target·optional만 저장, required는 계산 | 필수 선수 누락이 구조적으로 생기지 않음 |
| context가 그래프 리비전 고정 | 현재 시드 리비전 사용(`graph_revision_id`로 표시) | 시드는 배포 단위로만 바뀜 |

## MCP 도구

| 도구 | 필수 입력 | 선택 입력 | 출력 |
|---|---|---|---|
| `get_learning_path` | context_id | known_concepts, mode(`compact`·`expanded`) | context_id, graph_status, graph_revision_id, goal, mode, steps, skipped, unresolved_concepts |
| `get_prerequisites` | context_id, concept | 없음 | context_id, graph_status, graph_revision_id, concept, prerequisites, required_by, paper_locations |

- 두 도구 모두 `readOnlyHint=true`, `idempotentHint=true`, `openWorldHint=false`이며 외부 API를 호출하지 않는다.
- `graph_status=no_graph`는 오류가 아니다. 그 논문에 시드가 없다는 뜻이다.
- `start_learning`은 논문에 시드가 있을 때만 `capabilities`에 두 도구를 추가한다.
- 오류는 기존 코드를 쓴다. 없는 맥락·개념은 `NOT_FOUND`, 형식 오류는 `INVALID_ARGUMENT`.
- `known_concepts`를 생략하면 맥락에 저장된 값을 쓴다. 그래프와 맞지 않는 이름은 `unresolved_concepts`로 돌려준다.
- 건너뛴 개념(`skipped`)은 이해의 증거가 아니다.

## 경로 계산

1. known_concepts를 개념 id·한영 이름·별칭과 정규화(대소문자·공백) 후 정확히 대조한다.
2. 목적의 target(`expanded`면 optional 포함)에서 선수 관계를 거꾸로 모은다. known 개념 아래로는 내려가지 않는다.
3. 시드에 적은 선수 순서대로 깊이 우선 후위 순회해 정렬한다. 위상 순서를 지키면서 한 개념의 선수들이 바로 앞에 모인다.
4. required의 `reason_ko`는 경로에서 그 개념을 처음 필요로 하는 개념과의 선수 관계 이유다.

## 시드 작업 순서

1. 기준 PDF를 버전이 박힌 arXiv 주소로 받는다(시드의 `source_url`). 팀 드라이브로 공유하고 저장소에는 올리지 않는다.
2. 해시를 시드와 대조한다.

   ```powershell
   solo-leveling-kg-check --hash .\attention.pdf .\bert.pdf .\vit.pdf
   ```

   출력된 64자 해시를 해당 논문의 `file_sha256`에 넣는다. 앱은 PDF 전체 바이트의 SHA-256을
   소문자로 저장하므로(`storage/pdf_store.py`) 같은 파일이면 그대로 일치한다.
3. 같은 PDF로 각 개념의 `pdf_pages`, `section_hint`, `search_phrase`를 확인한다. `search_phrase`는 사용자가
   자기 PDF에서 Ctrl+F로 찾을 논문 속 짧은 용어·소제목(60자 이하, 한 줄)이고, 문장은 넣지 않는다.
   `solo-leveling-kg-check --verify-pdf .\attention.pdf .\bert.pdf .\vit.pdf`로 각 검색어가 적힌 페이지에 실제로
   나오는지 확인한다(해시로 논문을 찾고, 합자·하이픈·공백·대소문자는 무시). 초안 값은 기억에 의존해 적었으므로 반드시 검수한다.
   2026-11-14에 세 논문 모두 해시를 채우고 pypdf 본문 추출로 페이지를 대조했다(Attention 2건, BERT 1건 수정).
4. `solo-leveling-kg-check`로 검증한다. 문제가 있으면 모두 나열하고 종료 코드 1을 돌려준다.
   - id 형식, 이름·별칭 충돌, 없는 개념 참조, 자기 참조, 중복 관계, 순환
   - 목적마다 target 1개 이상, target·optional 중복 금지, 모든 항목에 reasons
   - `file_sha256`은 64자 16진수 또는 null(null이면 경고만 내고 연결하지 않음)
5. PR로 올린다. 리뷰가 검수 단계다.

개념 설명과 선수 이유는 팀이 직접 쓴 문장만 넣는다. 논문 원문은 넣지 않는다.
다른 시드를 시험할 때는 `SOLO_LEVELING_KG_SEED`에 경로를 지정한다. 시드가 잘못되면 MCP는
`KG_SEED_UNAVAILABLE`을 남기고 두 도구 없이 시작한다.
