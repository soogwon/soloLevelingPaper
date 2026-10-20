# 10. MCP 인터페이스 명세

상태: #47 로컬 M1 MCP 구현 기준선 / 2026-10-19

현재 `interfaces/mcp/server.py`에서 노출하는 5개 도구가 기준이다.
초기 초안의 미구현 항목은 아래 '후속 계약'으로 분리한다.

## 공통 규칙

stdio로 호출한다. 서비스 로그인·HTTP 업로드는 M1에 없다. 사용자 식별은 서버의 로컬 프로필에서 얻고 user_id 입력은 받지 않는다. context·version·evidence가 현재 로컬 데이터와 일치하는지 매 호출 검사한다. 파일 내부 지시나 모델 출력은 실행 명령이 아니다.

도구 결과는 structuredContent와 간단한 text로 제공한다. 등록에는 request_key를 사용하고 동일 로컬 프로필·도구·키에 같은 입력은 기존 결과, 다른 입력은 CONFLICT를 반환한다. 조회 도구는 readOnlyHint를 표시한다.

## 도구

| 도구 | 필수 입력 | 선택 입력 | 출력 |
|---|---|---|---|
| add_paper | source, request_key | 없음 | paper_id, version_id, job_id, status |
| get_paper_status | job_id | 없음 | job_id, version_id, status, stage, capabilities, limitations, result_available |
| start_learning | version_id | goal, known_concepts | context_id, version_id, goal, known_concepts, status, capabilities |
| ask_paper | context_id, question | standalone_question, focus, top_k | context_id, verification_level, status, answer_ko, claims, citations, reason_code |
| get_evidence | context_id, evidence_ids | 없음 | evidence (저장 근거 목록) |

이름은 기존 프로젝트 계약을 유지하며 C 문서의 register_paper/evidence_qa 등과 기능상 대응한다. 호스트 Skill이 없어도 핵심 도구로 접근 가능하다.

### 필드·응답 규칙

- 표의 출력 키는 모두 필수다. 값이 `null`인 것과 키를 생략하는 것은 다르다.
- `goal`: `understand`(기본), `implement`, `skim`. `null`은 허용하지 않는다.
- `known_concepts`: 문자열 목록. 생략·`null`은 빈 목록으로 처리한다. 항목은 공백을 제거하고 빈 값·중복을 거부한다.
- `request_key`: 공백만 있는 값은 거부하며 최대 200자다.
- `top_k`: 기본 5, 1~20의 정수. 문자열·실수·불리언은 허용하지 않는다.
- `standalone_question`: 생략·`null`이면 `question`을 사용한다. 지정하면 검색·생성 질문을 대체하되 원래 `question`도 비어 있으면 안 된다.
- `focus`: 생략·`null`이면 추가 범위 제한이 없다. `pdf_pages`, `section_ids`는 각각 기본 빈 목록이며 함께 지정하면 교집합이다. 페이지는 양의 정수, 섹션은 비어 있지 않은 문자열을 전달한다.
- `source`, `focus` 내부의 알 수 없는 필드는 거부한다. 최상위 알 수 없는 인자의 처리나 SDK의 암묵적 형 변환에 의존하지 않는다.
- `evidence_ids`: 비어 있지 않은 문자열 목록이며 빈 항목·중복을 거부한다. 같은 context에 저장된 근거만 조회한다.
- `get_paper_status.stage`는 문자열 또는 `null`, `result_available`은 불리언이다. `capabilities`, `limitations`는 문자열 목록이다.
- `start_learning.status`는 `ready`, `capabilities`는 `ask_paper`, `get_evidence`다.
- `ask_paper` 응답은 `answer` 객체로 감싸지 않고 최상위에 답변 필드를 둔다. `reason_code`는 사유가 없으면 `null`이다.
- `claims` 항목: `claim_id`, `text`, `evidence_ids`.
- `citations` 항목: `evidence_id`, `chunk_id`, `version_id`, `parse_revision_id`, `translation_revision_id`, `printed_page_label`, `pdf_page`, `quote_ko`, `quote_original`.
- `translation_revision_id`, `printed_page_label`, `quote_ko`는 `null`일 수 있다. 나머지 인용 필드는 문자열이며 `pdf_page`는 양의 정수다.
- `get_evidence.evidence` 항목은 citation 필드에 `file_display_name`을 추가한다.
- 정상 도구 결과는 `structuredContent`의 객체를 기준으로 읽는다. 현재 SDK는 같은 객체를 JSON text 콘텐츠로도 반환한다. text의 들여쓰기·키 순서는 계약이 아니다.

### 도구 속성

| 도구 | readOnlyHint | idempotentHint | openWorldHint |
|---|---|---|---|
| add_paper | false | true | true |
| get_paper_status | true | true | false |
| start_learning | false | true | false |
| ask_paper | false | false | true |
| get_evidence | true | true | false |

모두 `destructiveHint=false`다. 속성은 호스트용 힌트이며 권한 강제 장치가 아니다.
`get_paper_status`에는 선택적 화면 연결 메타데이터 `_meta.ui.resourceUri`가 있다.
`ui://solo-leveling/paper-status.html`을 제공하되 기존 출력 객체·text 응답과 도구 개수는 유지한다.
UI가 없는 호스트에서도 같은 계약을 사용한다. 실제 호스트 검증은 [#57 안내](../setup/mcp-app-ui.md)를 따른다.
등록은 요청 키와 입력을 기준으로, 학습 시작은 같은 논문·색인·학습 설정을 기준으로 재사용한다.
`ask_paper`는 외부 API 호출과 근거 저장을 수행하므로 읽기 전용·멱등 도구가 아니다.

## 파일 등록

로컬 입력 예:

```json
{"kind":"local_file","relative_path":"sample.pdf"}
```

사용자가 설정한 import 루트 기본 runtime/imports 기준이다. 실제 경로를 해석해 루트 안의 일반 PDF인지 확인하며 절대 경로·상위 탈출·symlink/junction 탈출을 거부한다. 폴더 전체 자동 스캔이나 다른 경로 탐색을 허용하지 않는다. 관리되는 runtime/pdfs로 복사해 해시·버전을 기록하며 사용자 제공 원본은 수정·삭제하지 않는다.

현재 `source.kind`는 `local_file`만 허용한다. URL·제목 검색·HTTP 업로드는 후속 계약이다.

파일 크기는 실행 설정의 `MAX_UPLOAD_BYTES`로 제한한다. 초기 제안인 30MB·150페이지를 현재의 고정 MCP 제한으로 간주하지 않는다. PDF 추출 불가 구간은 limitations를 확인한다.

## 작업·컨텍스트

등록 시 SQLite에 작업을 기록하고 프로세스 내부 실행기가 처리한다. 상태 모델은 queued/processing/ready/failed/interrupted, 단계 모델은 download/parse/translate/index다. 로컬 등록은 최대 한 개의 백그라운드 작업으로 진행하며 현재 응답에 retry_after_seconds는 없다. 번역 실패 구간을 limitations에 기록하고 정상 번역·색인 구간이 없으면 ready로 게시하지 않는다. ready는 전체 번역 완료를 보장하지 않는다.

재시작 시 미완료 processing 작업은 interrupted로 표시한다. 자동 이어 실행을 보장하지 않는다. 같은 PDF의 완료·진행 중 작업은 재사용한다. 재등록을 게시 후 번역 누락 복구 요청으로 해석하지 않는다. 고아 벡터의 자동 정리나 게시 후 복구는 이 도구 계약이 보장하지 않는다.

goal은 understand/implement/skim, 생략 시 understand다. 목적이 이미 있으면 재질문하지 않는다. known_concepts는 자기보고 문자열이며 개념 그래프와의 이름 매칭을 보장하지 않는다. context는 논문 버전·파싱·번역 리비전·임베딩 세트를 고정한다.

## 답변·근거

status는 ok/partial/insufficient_evidence/needs_clarification이다. claims는 주장 ID·텍스트·유효한 evidence_ids를 가진다. citations에는 원본·파싱·번역 리비전, printed_page_label, pdf_page, quote_ko, quote_original을 연결한다. 한국어 발췌는 '한국어 번역'으로 표시하며 원문 그대로의 인용으로 표현하지 않는다.

현재 구조 검증에서 ok·partial은 claims가 필요하고 insufficient_evidence·needs_clarification은 claims가 비어 있다. reason_code는 partial·insufficient_evidence에서 필수이며 ok·needs_clarification에서는 null이다. 상태·사유는 MCP 출력 스키마상 문자열이므로 의미상 허용값과 조합은 서비스 검증 및 테스트로 확인한다.

printed_page_label은 인쇄 번호를 확인한 문자열이며 사용자 표시의 기본이다. 없거나 불확실하면 null로 두고 'PDF 기준 N페이지'로 대체한다. pdf_page는 첫 페이지=1인 실제 파일 위치이며 이동·검증에 사용한다. 인쇄 번호 중복을 허용하고 단독 ID로 사용하지 않는다. focus는 물리 pdf_page 또는 section_id로 해석하며, 인쇄 번호만 받은 호스트는 중복 여부를 확인해 물리 위치로 변환하거나 확인 질문을 한다.

청크의 text는 한국어 번역문, original_text는 추출 원문이다. 생성에는 번역과 원문 근거를 제공한다. quote_ko는 번역문, quote_original은 추출 원문을 기준으로 연결한다. 원문과 번역문 사이 문자 offset이 같다고 가정하지 않는다. 자세한 필드와 재번역 정책은 [22번 합의](22-local-m1-baseline.md)를 참고하되 미구현 계획과 현재 동작을 구분한다.

서버가 실제 근거로 ID와 페이지를 연결한다. 현재 verification_level은 structural_only다. 구조·발췌 품질 검사는 의미 일치 검증이 아니며 수치·비교·인과의 정확성을 보장하지 않는다. 최종 claims를 기준으로 answer_ko를 구성하고 추출 한계 안내가 추가될 수 있다. ok도 질문 전체에 대한 완전한 답변을 보장하지 않는다.

후속 질문은 원 질문을 보존하고 지시 대상이 명확할 때만 standalone_question을 보충한다. 근거 부족 사유는 evidence_not_found/extraction_limited/conflicting_evidence/unsupported_scope/verification_failed로 구분한다. 외부 API 장애는 UPSTREAM_UNAVAILABLE이며 근거 없음으로 숨기지 않는다.

get_evidence는 해당 context에 저장된 번역문·추출 원문 근거를 반환한다. 재검색이나 끊긴 문장의 복구는 하지 않는다. 호스트가 파일 열기를 지원하지 않으면 파일명·인쇄 번호(불명 시 명시적 PDF 기준 번호)·인용문으로 안내하며 임의 파일 열기 명령을 생성하지 않는다.

## 주요 오류

서비스 오류는 다음 코드와 안전한 메시지를 사용한다. stdio에서는 `isError=true`와 text 콘텐츠로 반환하며 정상 출력 스키마를 요구하지 않는다.

| 코드 | 사용자 메시지 |
|---|---|
| INVALID_ARGUMENT | 입력값을 확인해주세요. |
| NOT_FOUND | 요청한 자료를 찾을 수 없습니다. |
| PAPER_NOT_READY | 등록 작업 상태를 확인해주세요. job_id와 status 포함 |
| UNSUPPORTED_DOCUMENT | 지원하는 PDF 파일인지 확인해주세요. |
| CONFLICT | request_key가 이전 요청과 일치하지 않습니다. |
| UPSTREAM_UNAVAILABLE | 답변 생성 서비스를 사용할 수 없습니다. |
| INTERNAL_ERROR | 저장된 데이터의 연결을 확인하지 못했습니다. 또는 요청을 처리하지 못했습니다. |

임베딩 오류는 `UPSTREAM_UNAVAILABLE: <안전한 임베딩 오류 코드>: 임베딩 처리를 완료하지 못했습니다.` 형식이다.
원래 서비스 예외의 메시지·절대 경로·키·스택은 전달하지 않는다.
필수 인자 누락·타입 오류 등 함수 실행 전 SDK 검증 실패도 `isError=true`지만, 현재는 위 코드로 통일되지 않은 SDK 검증 문구다. 그 문구는 버전별로 달라질 수 있고 입력값을 포함할 수 있으므로 호스트는 오류 전문을 무조건 로그에 남기지 않는다. 코드 파싱에 의존하지 말고 입력 스키마를 확인한다.
`partial`·`insufficient_evidence` 등 답변 상태는 도구 실행 오류와 구분한다.

원격 인증·upload 만료 오류는 후속 계약에서 다시 정의한다. M2 퀴즈·인용 그래프·메모 도구는 M1에 노출하지 않는다.

## 후속 계약과 호환성

- 요약 생성·저장과 읽기 가이드 내부 서비스는 존재하지만 현재 5개 MCP 도구에 연결되어 있지 않다.
- `start_learning.request_key`, `summary`, `summary_status`, `get_learning_guide`, URL 등록, 검수 그래프 고정, 의미 검증은 확정된 현재 인터페이스가 아니다.
- 초기 제안의 DOWNLOAD_FAILED, EXTRACTION_FAILED, REQUEST_IN_PROGRESS, RATE_LIMITED를 현재 독립 MCP 오류 코드로 가정하지 않는다.
- 기존 필수·선택 인자, 기본값, 최상위 응답과 nullable 키를 유지한다. 필드 제거·이름 변경·새 필수 인자·기본값 변경은 호환성 검토 대상이다.
- 선택 필드·도구 추가도 문서·스키마 테스트와 호스트 영향 검토를 함께 갱신한다.

## 계약 검증

`tests/unit/mcp/test_server.py`는 5개 도구의 필드·속성, 중첩 출력, nullable 필드, 최소 입력과 기본값, 잘못된 입력의 서비스 진입 차단을 검사한다.
`tests/integration/test_mcp_workflow.py`는 등록부터 근거 조회까지 ID 전달을 검사한다.
`tests/integration/test_mcp_stdio.py`는 실제 프로세스의 연결·도구 목록·오류 응답과 모의 서비스의 정상 structuredContent를 검사한다.
모의 응답 테스트는 실제 모델 품질이나 번역·색인 완료를 증명하지 않는다. 실행 명령은 [설치·실행 안내](../setup/mcp-server.md)를 따른다.
