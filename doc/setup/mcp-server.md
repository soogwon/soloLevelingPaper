# 로컬 MCP 서버 실행

저장소 없이 설치하는 경우 [Claude Desktop 설치·배포](desktop-installation.md)를 참고한다.

현재 연결 범위는 로컬 PDF 등록·상태 조회와 등록된 논문 질문·근거 재조회다.

입출력 필드·기본값·nullable 값·도구 속성·호환성 기준은
[MCP 인터페이스 명세](../architecture/10-mcp-contracts.md)를 기준으로 한다.
이 문서는 실행과 호스트 사용 안내에 집중한다.

실제 자료를 등록하기 전에 [외부 전송 데이터·시점 안내](external-data-transfer.md)를 확인한다.
`LOCAL_ONLY`는 서버의 OpenAI 호출을 차단하지만 Claude 호스트 통신까지 차단하지 않는다.

## 설치와 실행

PowerShell에서 저장소 루트로 이동하고 로컬 의존성을 설치한다.

```powershell
cd D:\fd\soloLeveling
.\.venv\Scripts\python.exe -m pip install -e ".[local]"
.\.venv\Scripts\python.exe -m solo_leveling.interfaces.mcp
```

기본 설정 파일은 실행 디렉터리의 `.env`다. 다른 파일은 MCP 호스트 프로세스 환경에
`SOLO_LEVELING_ENV_FILE`을 지정한다. 프로세스 환경은 파일의 같은 이름 설정보다
우선한다. `DATA_ROOT` 아래에 SQLite와 Chroma가 생성되며 하위 경로가 루트 밖으로
벗어나면 시작을 거부한다.

stdio의 stdout은 MCP 메시지 전용이다. 서버를 터미널에서 직접 실행하면 입력을
기다리는 것이 정상이며, 일반적으로 MCP 호스트가 자식 프로세스로 실행해야 한다.

## 선택적 진단 로그 수집 (#64)

`python -m solo_leveling.interfaces.cli.mcp_diagnostics` 또는
`python scripts/run_mcp_diagnostics.py` 실행 시 기본 로그에는 시작 메타데이터와
고정 실패 코드와 허용된 단계별 JSON 로그를 저장한다. 단계 로그의 요청·구간 ID,
단계명, 이벤트, 소요 시간, 고정 오류 코드만 검증해 보존한다. 임의 문자열·추가 필드와
네이티브 원본 stderr는 폐기하고 import 지연 스택은 기본 수집하지 않는다.
근거별 상세 추적 및 작업 프로세스 이벤트는 기본 필터의 대상이 아니다.
새 단계명을 추가하면 C의 `safe_diagnostic_stream.py` 허용 목록도 함께 갱신해야 한다.
stdout의 MCP 통신은 유지한다.

문제 재현에 상세 정보가 필요한 경우에만 `--collect-raw-logs`를 추가한다.
이 모드는 원본 stderr, 실행 파일·모듈·로그의 절대 경로, import 지연 스택 수집을
허용한다. 라이브러리가 출력한 키·본문·사용자 경로 등이 포함될 수 있고 자동 마스킹을
보장하지 않는다. 합성 자료로 재현하고 공유 전에 내용을 확인한다. 로그의 자동 삭제는 없다.
다시 기본 모드로 실행해도 이미 수집한 파일은 삭제되지 않는다.

이 정책은 C 진단 실행기를 통해 시작한 부모 프로세스에만 적용된다. 일반 MCP 실행이나
A의 임베딩 자식 프로세스가 별도 파일에 수집하는 로그까지 보호하는 설정은 아니다.
남은 범위는 [임베딩 로그 협의 사항](embedding-log-privacy-coordination.md)을 참고한다.

## 공용 PDF 저장 인터페이스 (#28)

`infrastructure.storage.pdf_store.PdfStore`는 MCP에 의존하지 않는 저장 모듈이다.
기존 `LocalPdfStore`는 import 루트·상대 경로·확장자 검증과 요청 fingerprint 생성을 유지하고,
PDF 내용의 검사·저장은 이 공용 모듈에 위임한다. 기존 MCP 오류 응답은 유지한다.

```python
from solo_leveling.infrastructure.storage.pdf_store import PdfStore

# managed_root에는 공통 설정의 DATA_ROOT/PDF_SUBDIR 경로를 전달한다.
store = PdfStore(managed_root, max_bytes)
stored = store.save_bytes(downloaded_pdf_bytes)
# 파일·동기 바이너리 스트림은 store.save_stream(stream)으로 전달할 수 있다.
# stored.path, stored.file_hash, stored.size_bytes를 후속 등록에서 사용한다.
```

- SHA-256 기반 `<hash>.pdf`를 저장하며 동일 내용은 기존 파일을 검증한 뒤 재사용한다.
- `%PDF-` 헤더와 크기만 확인한다. 실제 PDF 파싱·암호화·손상 여부 검증을 대신하지 않는다.
- 스트림은 현재 위치부터 읽으며 저장소가 닫거나 되감지 않는다. 블로킹 바이너리 스트림을 전달한다.
- 호출자가 지정한 저장소는 신뢰된 설정 경로여야 한다. 파일명·URL을 저장 경로로 사용하지 않는다.
- 다운로드·네트워크 제한·원본 파일명·요청 키·DB 등록·논문 ID 정책은 호출자 책임이다.
  `save_bytes`는 이미 메모리에 있는 데이터를 받으므로 다운로드 중 용량 제한을 대신하지 않는다.
- 잘못된 헤더·용량은 공용 `UnsupportedDocumentError`, 읽기·저장 오류는 예외로 전달한다.
  실패 시 이번 호출의 임시 파일만 정리하며, 기존 관리 파일은 덮어쓰거나 삭제하지 않는다.
- 동일 내용의 동시 저장은 원자적 하드 링크 생성으로 처리한다. 관리 저장소 파일시스템은
  하드 링크를 지원해야 한다(예: NTFS). 미지원·권한 오류 시 저장에 실패하며 덮어쓰기 방식으로 우회하지 않는다.
  프로세스 강제 종료 후 남은 임시 파일이나 DB 등록 실패 후 남은 파일의 정리는 별도 정책이다.

#28에서는 공용 인터페이스와 로컬 입력 연결까지만 제공한다.
`workers/ingestion.py`의 URL 등록은 아직 변경하지 않았으며, #27에서 A가 위 인터페이스를 연결한다.
기존 UUID 파일 이관·정리와 URL 등록 시 관리 경로 설정도 #27에서 결정한다.

## 선택적 의미 검증 (#36)

기본값은 비활성화다. 사용하려면 실행에 사용하는 env 파일에 다음 값을 설정하고 서버를 재시작한다.
기존 설정 파일을 자동 변경하지 않는다. 검증 모델을 생략하면 `GENERATION_MODEL`을 사용한다.

```dotenv
SEMANTIC_VERIFICATION_ENABLED=true
ALLOW_EXTERNAL_API=true
LOCAL_ONLY=false
# VERIFICATION_MODEL=gpt-5.4-mini
VERIFICATION_TIMEOUT_SECONDS=30
VERIFICATION_MAX_OUTPUT_TOKENS=2048
VERIFICATION_MAX_CLAIMS=10
VERIFICATION_MAX_INPUT_CHARS=60000
```

- 최종 초안과 실제 인용 원문을 외부 검증 API로 추가 전송한다. 한 답변에서 검증 호출은 최대 1회이며 자동 재시도·재작성은 없다. 기존 생성·추출 보완 호출은 별도다.
- 입력 한도는 JSON 문자 수이며 토큰 수와 다르다. 한도를 초과하면 잘라 보내지 않고 검증 실패로 답변을 보류한다.
- 제한 시간은 HTTP 읽기 대기 제한이며 연결 제한은 별도 5초다. 전체 요청의 엄격한 벽시계 종료 시각을 보장하지 않는다.
- 실제 인용 원문으로 전체 의미가 지지된 주장만 반환한다. 일부 제외는 `partial/semantic_limited`, 전체 제외는 `insufficient_evidence/semantic_limited`다.
- 검증 API·형식·한도 오류는 `insufficient_evidence/verification_failed`다. 논문에 정보가 없다는 뜻이 아니다.
- `model_checked`는 모델 검증 완료이며 정답 보증은 아니다. 기존 추출 제한도 그대로 유지한다.
- `semantic-v7`는 판정별 허용 사유를 API 스키마와 서버에서 동일하게 제한한다.
  질문 충족은 주제·요구 관계·조건, 보완 보존은 핵심 사실·비교 기준·조건으로 나눠 검사한다.
  답변 구절은 서버가 최종 주장 전체와 문장·줄 단위의 정확한 부분 문자열로 구성하고 ID를 붙인다.
  모델은 `answer_quotes`의 ID를 `quote_ids`로 선택하며, 요청별 스키마도 선택 가능한 ID로 제한한다.
  체크 응답 스키마는 긍정 판정과 누락·변경 판정을 별도 분기로 제한한다.
  긍정 판정에는 구절 ID가 최소 하나 필요하고 누락·변경 판정에는 빈 목록만 허용한다.
  답하지 못한 항목은 `unanswered`와 `missing` 체크로 표현하며 다른 항목의 유효한 답변은
  유지하여 `partial/semantic_limited`로 안내한다. 잘못된 긍정 판정을 서버가 자동 교정하지 않는다.
  서버는 알 수 없는·이전 답변의·중복 ID 및 해당 검사 대상이 아닌 주장의 ID를 거부한다.
  문장 간 조건이 유실되지 않도록 주장 전체 구절도 제공한다. 목록은 기존 입력 크기 제한에 포함한다.
  원문도 `original_quotes`의 ID만 선택한다. 서버가 원문 그대로 만든 문장 구절과 청크 전체를 제공한다.
  선택 ID를 현재 원문에서 재구성하고 해당 주장의 evidence_ids에 속하는지 검사한다.
  없는·이전 원문·중복 ID와 다른 주장의 근거 선택을 거부하며 의미·지표 혼동 방어는 유지한다.
  세부 항목이 누락 또는 기준 변경이면 모델의 종합 통과 판정을 그대로 채택하지 않는다.
  원문에 맞춘 조건 복원은 `corrected`로 별도 처리하고 다른 항목이 유지되면 보존으로 인정한다.
  정확한 구절 인용만으로 의미가 맞음을 보장하지 않으며, 항목별 모델 판정에도 오류가 남을 수 있다.
  출력 구조가 늘어도 기존 토큰 상한은 유지하며 불완전 응답은 실패로 처리한다.
  서버의 좁은 지표 대체 방어는 명시적인 계산 복잡도·총계산량 요구에 순차 연산 설명만
  반환한 경우 `METRIC_SUBSTITUTION`으로 질문 충족을 제한하고 해당 보완 그룹을 제외한다.
  두 기준을 함께 요구·답하거나 표현이 불명확하면 이 규칙으로 단정하지 않는다.
  어휘 기반 추가 방어이지 일반적인 의미 판별기나 정상 답변 승인 규칙은 아니다.
  조건 질문에 '길이가 n일 때 더 빠르다'처럼 변수 이름만 조건으로 제시한 좁은 패턴은
  `VARIABLE_ONLY_CONDITION`으로 기록한다. 모델이 supported로 판정해도
  partially_supported/missing_condition으로 낮춰 해당 주장을 제외한다.
  다른 유효한 답변은 유지하며 제외된 주장을 질문 충족 근거로 사용하지 않는다.
  'n < d', 'n이 d보다 작을 때'처럼 비교 관계가 있는 답변은 이 규칙으로 승인하거나 거부하지 않고
  기존 의미·원문 검사를 유지한다. 숫자 조건·다른 표현의 모든 조건 누락을 탐지하는 규칙은 아니다.
  기본 로그의 `semantic_diagnostic/detail`에는 고정 `reason_code`만 추가한다.
  `PROVIDER_JSON_INVALID` / `PROVIDER_ENVELOPE_INVALID`는 제공자 형식,
  `CLAIM_CHECK_INVALID` / `CLAIM_SET_INVALID`는 주장 판정 계약,
  `ORIGINAL_QUOTE_*`는 원문 구절 검사, `ANSWER_QUOTE_*`는 답변 구절 검사,
  `ANSWER_QUOTE_ID_UNKNOWN` / `ANSWER_QUOTE_ID_DUPLICATE`는 미등록·중복 답변 구절 ID,
  `QUOTE_REQUIRED`와 `ANSWER_QUOTE_REFERENCE_INVALID`에는 `check_item`으로 `coverage.topic`, `coverage.requested_relation`,
  `coverage.qualifiers`, `repairs.core_fact`, `repairs.comparison_basis`, `repairs.qualifiers`
  중 하나만 기록한다. 질문·주장 본문과 항목별 ID는 포함하지 않는다.
  원문 ID·참조·정책·개별 체크 형식 오류에는 `original.content`, `original.direction`,
  `original.conditions`, `original.causality`, `original.metrics` 중 해당 `check_item`을 기록한다.
  새 미등록·중복 원문 구절 코드는 `ORIGINAL_QUOTE_ID_UNKNOWN`, `ORIGINAL_QUOTE_ID_DUPLICATE`다.
  `QUOTE_REQUIRED` / `QUOTE_FORBIDDEN` / `CHECK_NOT_APPLICABLE`은 체크리스트 구절 정책,
  `QUESTION_PARTITION_MISMATCH`는 질문 분할 누락·변형,
  `COVERAGE_REFERENCE_INVALID` / `REPAIR_REFERENCE_INVALID` / `REPAIR_COVERAGE_MISSING`은 대상 참조를 뜻한다.
  질문 상태 스키마에서 answered/partial은 현재 주장 ID 하나 이상, unanswered는 빈 ID와
  미충족 체크만 허용한다. 모두 unanswered여도 정상 검사 결과이며 유효한 주장이 없으면
  `insufficient_evidence/semantic_limited`로 처리한다. 잘못된 참조는 자동 수정하지 않는다.
  `COVERAGE_REFERENCE_INVALID`에는 `rejection_reason`으로 형식(`ITEM_FIELDS_INVALID`,
  `QUESTION_PART_INVALID`, `STATUS_INVALID`, `CLAIM_IDS_TYPE_INVALID`, `CLAIM_ID_TYPE_INVALID`),
  참조(`UNKNOWN_CLAIM_ID`, `DUPLICATE_CLAIM_ID`), 상태 충돌(`UNANSWERED_HAS_CLAIM_IDS`,
  `ANSWERED_WITHOUT_CLAIM_IDS`, `PARTIAL_WITHOUT_CLAIM_IDS`) 중 첫 위반만 기록한다.
  문제의 ID·질문 조각·모델 응답 본문은 기록하지 않는다.
  질문·주장·원문·근거 ID·예외 문자열은 이 이벤트에 기록하지 않는다.
  이전 `invalid_response` 호출은 당시 세부 코드가 없으므로 원인을 소급 확정할 수 없다.
  기존 의미 검증 한 번에 질문 항목별 답변 여부와 보완 전후 내용 보존 검사도 함께 요청한다.
  질문과 보완 전 주장·결과 대응 관계가 추가 전송되며 기존 입력 크기·시간·출력 토큰 제한에 포함된다.
  추가 호출이나 자동 재시도는 하지 않는다. 비활성화 시에는 이 두 검사도 수행하지 않는다.
  질문 원문을 순서대로 분할한 항목을 받아 전체 질문 누락·변형과 없는 주장 ID를 서버가 거부한다.
  답하지 못한 항목이나 일부만 답한 항목은 `partial/semantic_limited`와 질문 원문 인용으로 안내한다.
  의미 검사에서 제외된 주장은 질문 충족 근거로 인정하지 않는다.
  보완 대상의 핵심 내용·비교 대상·조건은 결과 전체와 비교한다. 원문에 따른 오류 교정은 허용한다.
  결과 주장 ID가 비어 있는 보완 그룹은 모델에 판정을 요청하지 않고 서버가 `partial`로 확정한다.
  다른 유효한 주장은 계속 검증·보존하며, 모델이 요청받지 않은 빈 그룹의 판정을 만들면 거부한다.
  일부 내용 유실은 제한 안내를 추가하고, 다른 내용으로 바뀐 그룹은 제외한다.
  전부 제외되면 `insufficient_evidence/semantic_limited`, 검사 형식·참조 실패는 기존대로
  `insufficient_evidence/verification_failed`다. DB 스키마·이력 저장은 변경하지 않는다.
  항목 분해와 의미 보존은 모델 판정이며, 검사 통과가 정보 유실 없음이나 사실성을 보장하지 않는다.
  근거에 없는 방향성·조건을 추가한 경우는 부분 충족으로, 원문이 반대 내용을 명시한 경우는 모순으로 구분한다.
- 모델은 내용·방향·조건·인과·지표를 따로 검사하고, 지지된 항목마다 서버 원문 구절 ID를
  최대 4개 선택한다. 서버는 실제 원문의 정확한 위치·본문·근거·페이지를 목록으로 제공하고
  해당 주장이 인용한 근거 범위만 허용한다. 여러 청크의 구절은 합치지 않고 ID별로 분리한다.
  원문 구절 목록도 기존 입력 크기 제한에 포함하며 추가 API 호출은 하지 않는다.
- 없는 구절·다른 주장의 근거·필수 검사 누락은 검증 실패로 처리한다. 항목에 미충족이 있는데
  최종 판정만 `supported`이면 부분 충족으로 낮춰 해당 주장을 제외한다.
  검사 구절은 내부 처리에만 사용하며 DB·진단 로그·MCP 응답에 추가하지 않는다.
  구절의 존재는 의미 일치의 증명이 아니며, 항목 누락·잘못된 의미 판단 등 모델 오판은 여전히 가능하다.
- DB 이력 저장은 #83 합의에 따라 별도 PR로 분리했다. 최종 근거만 기존 방식으로 저장하고 제외된 주장·판정은 DB에 저장하지 않는다.
- 의미 검증의 기본 안전 로그에는 단계·시간이 보존된다. 모델 판정별 상세 이벤트는 기본 필터에서 제외된다. 상세 로그를 위해 원본 수집을 켜면 다른 라이브러리의 민감정보도 남을 수 있으므로 주의한다.
- 추출 검사의 최소 진단은 `stage=extraction_diagnostic`으로 기본 로그에 보존한다.
  `verification_failure`도 같은 단계에 기록하며 초안 형식·근거 연결 실패를 구분한다.
  `GENERATION_JSON_INVALID` / `GENERATION_SCHEMA_INVALID` / `GENERATION_FORMAT_INVALID`는
  생성 응답 형식 오류, `DRAFT_INVALID`는 기타 초안 검증 오류다.
  `EMPTY_EVIDENCE_IDS` / `DUPLICATE_EVIDENCE_ID` / `UNKNOWN_EVIDENCE_ID` /
  `SUPPORT_REFERENCE_INVALID`는 각각 근거 누락·중복·미등록 ID·supports 참조 불일치다.
  특정 주장의 오류이면 번호를 기록하고, 초안 전체 오류이면 번호는 `null`이다.
  실제 잘못된 ID나 예외 메시지는 기록하지 않는다. 이 이벤트는 의미 불일치 판정이 아니다.
  최초 생성 요청은 JSON Schema의 `evidence_ids`와 `supports.evidence_id`에
  해당 요청 후보 ID만 `enum`으로 허용한다. 요청별 스키마를 만들어 이전 후보를 재사용하지 않는다.
  서버의 ID·구절·의미 검증은 그대로 수행하며, 알 수 없는 ID를 임의로 보정하지 않는다.
  이 제약은 후보 밖 ID를 막기 위한 것이며 필요한 후보의 인용이나 주장 정확성을 보장하지 않는다.
  보완 입력의 `repair_contexts`에는 대상 주장과 문제 근거(`left`)·다음 근거(`next`)의
  연결 메타데이터를 제공한다. 원문을 합치거나 새 근거를 만들지 않으며, 없는 후보나 잘못된 인접
  연결은 호출 전에 거부한다. 다음 후보가 없는 구절 재선택 대상은 `next=null`이다.
  보완 입력의 `repair_quote_options`에는 서버가 ID를 붙인 원문 구절 선택지를 제공한다.
  보완 응답은 `support_ids`로 선택지 ID만 반환하고, 서버가 정확한 원문과 근거 ID를 복원한다.
  앞·뒤 청크의 미완결 문장이 연결되는 선택지는 양쪽 구절을 한 항목으로 제공하므로,
  선택하면 두 근거가 함께 복원된다. 각 근거의 ID·페이지·원문은 따로 유지한다.
  미완결 꼬리 보완은 앞 청크의 마지막 조각과 뒤 청크의 첫 문장까지만 쌍으로 선택한다.
  같은 청크의 다른 완결 문장이나 전체 청크 선택지는 제외하고, 쌍을 확인할 수 없으면 보류한다.
  다른 보완 사유에서는 기존 추출 검사를 통과한 완결 구절도 선택할 수 있다. 선택지는 전달된 후보 안에서만
  만들며, 사용자 지정 범위 밖 청크를 새로 조회하지 않는다. 알 수 없거나 중복된 선택 ID는 거부한다.
  복원 후 기존 추출·참조·의미 검사는 그대로 수행한다. 구절 선택 자체가 의미 일치를 보장하지 않는다.
  보완에는 전체 복합 질문을 다시 전달하지 않고 대상 초안의 사실·비교 기준만 보완하도록 요청한다.
  선택지는 대상의 기존 인용 및 지정된 연결 청크로 제한하며, 대상 번호별 허용 ID를
  스키마와 서버 파서에서 모두 검사한다. 대상과 무관한 후보로 다른 질문을 답하지 못하도록 한다.
  같은 근거 내부의 의미 이탈은 ID만으로 판별할 수 없으므로 기존 의미 검증을 유지한다.
  정상 완료된 의미 검증의 최종 판정과 실패 체크를 기본 로그의 `semantic_diagnostic/assessment`로 기록한다.
  `claim.verdict`는 주장별 판정·사유, `original.*`는 원문 검사 항목,
  `repairs.*`는 보완 내용 보존, `coverage.*`는 질문 항목 충족 결과다.
  `claim.final`은 실제 유지(`RETAINED`), 의미 미지원 제외(`SEMANTIC_NOT_SUPPORTED`),
  보완 내용 변경 제외(`REPAIR_CHANGED`)를 구분한다. 판정 기준은 바꾸지 않는다.
  `item_number`는 claim/original은 의미 검증 입력 주장 순서, repairs는 보완 검사 순서,
  coverage는 질문 항목 순서다. 요청·실행 ID로 연결하며 본문·인용문·주장 ID는 기록하지 않는다.
  정상 세부 체크와 정상 coverage/repairs 종합 판정은 `--detailed-diagnostics`에서만 기록한다.
  Desktop 설정의 `serve` 인자에도 같은 옵션을 추가할 수 있다. 원본 로그 수집 옵션과는 다르며,
  상세 모드에서도 본문·임의 stderr는 안전 필터가 폐기한다.
  코드·PR 분리 기준과 제외 자료는 [변경 범위](semantic-verification-change-scope.md)에 정리한다.
  원문은 `repair_quote_options`로만 전달한다. `evidence`·`repair_contexts`의 전체 원문·번역문과
  초안의 기존 supports는 보완 요청에서 제외하여 제한 밖 설명이 다른 필드로 전달되지 않게 한다.
  보완 응답은 `source_claim_number`로 원래 보완 대상에 연결한다. 한 대상을 여러 주장으로
  분할할 수 있으며 각 결과의 근거를 개별 검사한다. 없는 번호·번호 누락은 거부한다.
  정상 주장은 그대로 보존하고 보완 결과는 원래 대상 위치에 넣는다. 빈 claims도 허용한다.
  번호 없는 기존 제공자는 단일 대상의 단일 결과만 호환하며 여러 대상의 대응을 추측하지 않는다.
  번호 연결은 의미 보존의 증명이 아니다. 의미 검증은 별도이며 대상 내부의 사실 누락까지 보장하지 않는다.
  `quality`는 검사 사유, `claim_excluded`는 명시적인 주장 제외,
  `repair_outcome`은 보완 결과다. 모든 대상 번호가 남고 반환된 결과가 모두 추출 검사를 통과하면 `REPAIR_ADOPTED`,
  일부만 남으면 `REPAIR_PARTIAL`, 남은 주장이 없으면 `REPAIR_NO_USABLE_CLAIMS`다.
  호출 실패는 `REPAIR_UNAVAILABLE`, 형식·참조 오류는 `REPAIR_INVALID`로 구분한다.
  요청·시도 ID, 해당 라운드의 주장 번호, 라운드(초안 0 / 보완 1), 고정 사유 코드만 추가하며
  본문·질문·근거 ID·청크 ID·경로는 포함하지 않는다. 라운드 간 주장 번호는 같은 주장을 보장하지 않는다.
  `REPAIR_ADOPTED`는 추출 검사 통과이지 의미 검증 통과나 최종 답변 성공이 아니다.
  생성기가 보완 주장을 모두 생략해도 `REPAIR_NO_USABLE_CLAIMS`로 기록한다.
  같은 근거 안의 연속·겹침 구절은 인용 범위로 함께 검사하지만, 미인용 단어는 채우지 않는다.
  다음 청크를 이미 인용한 경우에도 구절 연결이 불완전하면 기존 1회 한도 안에서 보완한다.
  답변 후보 보충에서는 완결 청크의 다음 청크도 확인한다. 원문 끝과 다음 원문 시작이
  공백 정규화 후 20자·4단어 이상 겹치고 새 완결 문장이 있을 때만 문맥 후보로 추가한다.
  미완결 연결을 먼저 보충하며 기존 개수 상한·페이지·섹션·리비전 범위를 유지한다.
  재귀적으로 확장하거나 본문을 합치지 않고, 새 후보의 ID·페이지와 별도 인용 검증을 유지한다.
  `quality`의 `UNFINISHED_TAIL`에는 본문 없는 `detail_code`도 기록한다.
  여러 조건이 실패하면 다음 표 순서의 첫 원인만 기록한다. 후보 부재는 현재 후보 집합 기준이며,
  논문 전체에 이어지는 문장이 없다는 뜻은 아니다.

  | detail_code | 의미 |
  |---|---|
  | `NEXT_CANDIDATE_ABSENT` | 서버가 연결한 다음 후보 없음 |
  | `NEXT_NOT_CITED` | 다음 후보는 있으나 해당 주장이 인용하지 않음 |
  | `LEFT_TAIL_START_MISSING` | 앞 구절이 미완결 문장의 시작을 덮지 못함 |
  | `LEFT_TAIL_END_MISSING` | 앞 구절이 청크 끝까지 덮지 못함 |
  | `NEXT_NOT_CONTINUATION` | 다음 청크가 이어지는 문장 조각으로 판정되지 않음 |
  | `NEXT_SENTENCE_END_ABSENT` | 다음 청크에도 문장 끝 부호가 없음 |
  | `RIGHT_PREFIX_START_MISSING` | 뒤 구절이 다음 청크 시작을 덮지 못함 |
  | `RIGHT_PREFIX_END_MISSING` | 뒤 구절이 첫 문장 끝까지 덮지 못함 |

  세부 코드는 진단 전용이며 MCP 응답의 상태·사유나 주장 채택 기준을 바꾸지 않는다.
- 서버가 서비스를 구성하면 기본 로그에도 `semantic_startup`이 기록된다.
  `enabled`는 해석된 활성화 설정, `connected`는 실제 검증 서비스 연결 여부,
  `prompt_version`은 연결된 검증기의 지침 버전이다. 비활성화 시 버전은 `null`이다.
  키·모델명·설정 경로·본문은 기록하지 않는다. 이 이벤트는 시작 구성을 확인하는 것이며 실제 검증 호출 성공을 뜻하지 않는다.

활성화된 최신 설치본의 시작 로그 예:

```json
{"event": "semantic_startup", "enabled": true, "connected": true, "prompt_version": "semantic-v7"}
```

설정 파일을 수정해도 실행 중인 서버에는 적용되지 않는다. 새 wheel을 실제 Desktop 가상환경에
재설치하고 완전히 종료·재시작한 뒤 `DATA_ROOT/diagnostics`의 새 `mcp-*.log`를 확인한다.
이벤트가 없으면 최신 설치본·실제 실행 경로·시작 실패 여부를 먼저 확인한다.

수동 검증은 기존 context로 질문을 한 번 호출하고 status·verification_level·reason_code·claims·citations를 확인한다.
조건 누락·방향성 확대·계산량 혼동 사례와 여러 페이지에 걸친 정상 근거를 비교한다.
실제 호출에는 비용이 발생한다. 모의 테스트 통과가 모델 정확도 검증을 뜻하지 않으며 소량 실측과 #81 평가가 별도로 필요하다.

2026-10-24 소량 실측(`gpt-5.4-mini`, `semantic-v3`, 사용자 원문 전송 승인 후 총 3회):
저장된 경로 길이 구절에 방향성을 추가한 주장은 `partially_supported/unsupported_direction`,
같은 구절의 중립적 주장은 `supported/full_support`, 두 합성 청크에 나뉜 조건을 보존한 주장은
`supported/full_support`로 반환됐다. 호출별 약 2.44초·1.99초·1.70초였다.
각 사례 1회이며 실제 MCP 전체 흐름 재검증이나 반복 정확도 평가를 대체하지 않는다.

## 도구

### list_paper_jobs

기존 작업 ID를 모르면 인자 없이 호출하거나 `query`로 제목·파일명을 검색한다.
`limit`은 기본 20·최대 100이며 `next_cursor`가 있으면 같은 query와 cursor로 다음 페이지를 요청한다.
실패·재처리 작업을 포함한 작업별 목록이며, 논문별 하나로 합친 목록은 아니다.
사용자가 원하는 항목을 선택하면 그 job_id로 `get_paper_status`를 호출한다.
준비 상태를 확인한 뒤 해당 version_id로 `start_learning`을 호출하고,
반환된 context_id를 후속 `ask_paper`에 사용한다.
이 흐름으로 #21의 작업 ID 찾기와 #23의 학습 논문 목록 조회·선택을 지원한다.
읽기 전용이므로 논문 재등록·번역·모델 호출은 필요하지 않다.

수동 확인 예:

> 등록한 논문 작업을 list_paper_jobs로 찾아줘. 논문 재등록이나 번역은 하지 마.
> 후보가 여러 개면 선택할 수 있도록 파일명·상태·작업 ID를 보여줘.

DB가 비어 있으면 빈 목록이 정상이다. 커서 형식과 응답 필드는 MCP 인터페이스 명세를 참고한다.

### add_paper

`source={"kind":"local_file","relative_path":"paper.pdf"}`와 `request_key`를 받는다.
파일은 `DATA_ROOT/IMPORT_SUBDIR` 아래의 상대 경로여야 한다. 절대 경로, `..`, 루트
밖으로 향하는 링크, PDF가 아닌 파일과 `MAX_UPLOAD_BYTES` 초과 파일은 거부한다.
검증한 파일은 `DATA_ROOT/PDF_SUBDIR`에 해시 이름으로 복사하며 원본은 변경하지 않는다.

입력 경로는 OS와 무관하게 Windows 드라이브·UNC·루트 기준 경로와 역슬래시로
표현한 `..`도 거부한다. NUL 문자와 콜론(`:`)도 허용하지 않는다. 콜론 차단은
Windows 대체 데이터 스트림 접근을 막기 위한 것으로 POSIX 파일명에도 동일하게 적용한다.
일반 하위 폴더와 한글 파일명은 지원한다. PDF 헤더 검사는 문서 전체의 유효성이나
악성 콘텐츠가 없음을 보장하는 검사가 아니다.

현재 경계 검사는 신뢰할 수 있는 로컬 사용자가 관리하는 폴더를 전제로 한다.
다른 프로세스가 경로 검사와 파일 열기 사이에 파일·디렉터리를 교체하는 경쟁 상황이나
하드링크의 원본 위치까지 차단하는 보장은 아니다. 신뢰하지 않는 사용자에게 데이터
폴더 쓰기 권한을 제공하지 않아야 한다. 이 경계까지 강화하려면 별도 설계와 공통
저장소 담당 A의 검토가 필요하다. #66의 저장 경로 설정 검증과 이번 PDF 입력 경로
검증은 별개의 검사다.

요청은 관리 복사와 DB 작업 확보가 끝나면 반환하고, 파싱·번역·색인은 최대 한 개의
백그라운드 작업으로 진행한다. 동일 `request_key`와 동일 입력은 기존 작업을 반환하며,
다른 입력에 같은 키를 사용하면 `CONFLICT`다.

### get_paper_status

MCP Apps 지원 호스트에는 읽기 전용 상태 카드를 연결한다. 기존 입력·응답은 그대로이며
UI 미지원 호스트도 텍스트로 조회할 수 있다. [Claude Desktop UI 검증](mcp-app-ui.md)을 참고한다.

`job_id`로 `processing`, `ready`, `failed`, `interrupted` 상태와 현재 단계,
제한 사항을 조회한다. `ready`일 때만 `start_learning`을 capabilities에
표시한다. 서버 재시작 시 끝나지 않은 processing 작업은 interrupted가 된다.

`ready`는 성공한 번역 구간의 검색 준비가 끝났다는 뜻이며 전체 번역 완료를 보장하지 않는다.
`limitations`에 번역 실패가 있으면 해당 구간은 검색에서 제외된다.
`provider_unavailable`만으로 연결 실패·시간 초과 등의 세부 원인을 단정하지 않는다.

### start_learning

`version_id`와 선택 입력 `goal`, `known_concepts`를 받는다.
목적은 `understand`(기본값, 이해), `implement`(구현), `skim`(훑어보기)다.
기존 지식은 사용자의 자기보고로 전달하며 생략하면 빈 목록이다.
같은 논문·색인·설정의 context를 재사용하고 다른 설정이면 별도 context를 만든다.
응답의 `context_id`, `goal`, `known_concepts`를 확인하고 후속 질문에 같은 ID를 사용한다.
목적 저장이 생성 프롬프트에 목적별 설명 방식을 적용한다는 보장은 아니다.
이 도구의 `request_key`, 요약 및 `summary_status` 출력은 아직 연결하지 않았다.
별도 요약·읽기 가이드 내부 서비스의 존재와 MCP 도구 노출은 구분한다.

### ask_paper

필수 입력은 `context_id`, `question`이다. 선택 입력은 `standalone_question`,
`focus`, `top_k`다. `focus.pdf_pages`는 1부터 시작하는 실제 PDF 페이지이며
`focus.section_ids`와 함께 지정하면 두 조건의 교집합에서 검색한다.
`top_k`는 기본 5이며 1~20의 정수다. `focus`, `standalone_question`은 생략하거나
`null`로 보낼 수 있다. 두 값이 없으면 기존 context 범위에서 원 질문을 사용한다.

답변은 `context_id`, `verification_level`, `status`, `answer_ko`, `claims`,
`citations`, `reason_code`를 최상위에 반환한다. 기본 검증 수준은
`structural_only`이며 선택적 의미 검증이 완료되면 `model_checked`다.
이 도구는 최종 근거를 SQLite에 저장하고 외부 생성·검증 API를
호출할 수 있으므로 read-only 도구가 아니다.

### get_evidence

`context_id`와 비어 있지 않은 `evidence_ids`를 받는다. 다시 검색하거나 생성하지
않고 저장된 한국어 번역, 추출 원문, 페이지, 파일 표시 이름을 반환한다. 다른
context의 근거는 조회할 수 없다.

## 오류와 현재 제한

오류는 `INVALID_ARGUMENT`, `NOT_FOUND`, `CONFLICT`, `UNSUPPORTED_DOCUMENT`,
`PAPER_NOT_READY`, `UPSTREAM_UNAVAILABLE`, `INTERNAL_ERROR`로 구분한다. 원래 예외 메시지,
API 키, 절대 경로와 스택은 MCP 오류에 포함하지 않는다.

위 설명은 서비스 예외 변환에 해당한다. 필수 입력 누락·타입 오류는 함수 실행 전에
SDK가 검사하며, 현재 코드 접두사가 통일되지 않은 검증 문구를 반환한다.
호스트는 `isError`를 먼저 확인하고, 입력값을 포함할 수 있는 SDK 오류 전문을
무조건 기록하지 않는다. `partial` 등 답변 상태는 도구 오류와 구분한다.

- 서버 시작 시 DB 스키마를 준비하고 이전 실행의 processing 작업을 interrupted로 바꾼다.
- 서비스 호출은 작업 스레드에서 실행해 비동기 MCP 메시지 처리를 직접 막지 않는다.
- `standalone_question`이 있으면 검색·생성에 사용하지만 원 질문도 비어 있지 않아야 한다.
- URL 등록, 요약·가이드는 아직 노출하지 않는다.
- stdio 서버는 단일 로컬 프로필을 전제로 하며 원격 인증은 구현하지 않는다.

테스트:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/mcp tests/integration/test_mcp_workflow.py tests/integration/test_mcp_stdio.py -v
```

단위 테스트는 스키마·기본값·기존 입력 호환성·도구 속성을 확인한다.
stdio 테스트는 실제 실행 구성의 도구 목록·오류와, 모의 서비스를 연결한 정상 응답을
확인한다. 외부 생성 API를 호출하거나 실제 사용자 DB를 사용하지 않는다.

## 호스트 안내와 근거 확인

서버 초기화의 instructions와 각 도구 설명에 다음 사용 지침을 제공한다.

- 반환 식별자를 이어서 전달해 등록 → 상태 확인 → 학습 시작 → 질문 → 근거 조회를 수행한다.
- `ok`는 답변 완전성 보증이 아니다. `structural_only`는 의미·수식 정확성 검증이 아니다.
- 문장이 끊기면 같은 context와 반환된 evidence_id로 `get_evidence`를 확인한다.
  저장된 청크 자체가 잘렸다면 이 조회로 누락 문장이 복구되지는 않는다.
- 사용자 지정 범위를 유지하며, 필요한 문맥이 범위 밖이면 범위 확장을 안내한다.
- 인쇄 페이지가 없으면 'PDF 기준 N페이지'로 안내한다.
- 호스트 권한 검사 실패와 MCP 서버 오류를 구분한다. DB·PDF 직접 조회로 우회하지 않고
  실패 사실을 안내한다. 재호출할 때는 기존 context를 유지한다.
- 이전 근거 또는 배경지식을 사용하는 경우 이번 호출에서 얻은 근거와 구분한다.

이 지침은 호스트에 전달하는 사용 안내이며 별도 셸·파일 권한을 강제 차단하는 기능은 아니다.
청크 분할과 인접 근거 보충 품질은 A·B가 함께 검토한다.
의미 검증 품질 평가는 #81, 별도 이력 저장 도입 후 보존·정리 정책은 #82에서 다룬다.
상세 계획은 [담당별 진단 문서](evidence-boundary-diagnosis-and-ownership.md)를 참조한다.

Claude Code는 등록된 프로젝트 폴더에서 실행한다. 코드를 갱신한 후 기존 MCP 프로세스를
재연결하거나 Claude Code 세션을 다시 시작해 새 instructions와 도구 설명을 받는다.
등록 작업이 진행 중이면 완료 후 재시작한다.
같은 context로 질문하고 `get_evidence`까지 호출해 페이지·인용문·한계 안내를 확인한다.
