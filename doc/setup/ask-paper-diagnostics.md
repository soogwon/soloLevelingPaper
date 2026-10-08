# ask_paper 단계별 진단

1800초 호스트 종료의 원인은 아직 확정하지 않았다. 모델 로딩뿐 아니라
스레드 대기, DB 조회, 전체 Chroma 검증, 임베딩 계산, 네트워크, 근거 저장을 구분한다.
이 변경은 계측이며 시간 제한·자동 재시도·작업 중단 정책을 바꾸지 않는다.

단, 아래의 미완결 주장 보완은 별도 기능으로, 조건에 맞을 때 생성 호출을 최대 한 번 추가한다.

## 출력

활성 ask_paper/AnswerService 요청에서 stderr로 JSON 한 줄씩 즉시 출력한다.
stdout에는 진단을 쓰지 않는다. 요청별 무작위 request_id와 단계별 span_id를 사용한다.
추가 검색은 같은 request_id, 새로운 span_id로 기록한다.
질문·원문·번역·주장 본문·키·모델 경로·예외 메시지는 단계 로그에 기록하지 않는다.
시간 로그의 필드는 request_id, span_id, stage, event, elapsed_ms, error_code다.
근거 선택 진단은 아래에 설명한 청크·근거 ID와 고정 메타데이터를 추가한다.
시간은 단조 시계 기준이며 start는 0, end/error는 해당 단계의 소요 밀리초다.
실패는 STAGE_FAILED, 취소·중단은 CANCELLED로 기록하고 기존 예외를 다시 전달한다.
이 코드는 외부 라이브러리가 자체 출력하는 로그까지 정제하지 않는다.

## 단계

| stage | 범위 |
|---|---|
| ask_paper | MCP 함수 진입부터 응답 변환까지 |
| answer | 작업 스레드의 답변 서비스 진입부터 반환까지 |
| context_lookup / default_context_lookup | context 조회 또는 기본 context 준비 |
| index_lookup | SQLite 색인·청크 스냅샷 조회와 검증 |
| chroma_validation | 컬렉션 열기 및 전체 벡터·본문·메타데이터 검사 |
| query_embedding | 주입된 임베딩 함수 전체 |
| embedding_model_load | 기본 임베더의 라이브러리 import·모델 준비, 캐시 적중 포함 |
| embedding_library_import | sentence_transformers 및 의존 라이브러리 import |
| embedding_model_construct | SentenceTransformer 생성: 디스크 캐시·원격 확인·가중치 초기화 등 |
| embedding_memory_cache_hit | 이미 같은 프로세스에 준비된 모델 재사용 |
| embedding_encode | 실제 임베딩 encode 계산 |
| vector_search | Chroma query 호출 |
| generation | 생성 제공자 전체, 응답 파싱 포함 |
| generation_repair | 미완결 주장 보완 호출·구조 검사·품질 재판정 |
| generation_api_call | HTTP 요청부터 응답 본문 수신까지, HTTP 성공 판정은 아님 |
| evidence_validation | 최종 인용문 조립·구조 검증 |
| evidence_save | SQLite 근거 검증 및 저장 트랜잭션 |

## 후보·선택·품질 판정 진단

`stage=evidence_trace`는 시간 측정이 아닌 단일 사건 기록이다(`elapsed_ms=0`).
같은 `request_id` 안에서도 추가 검색 후 재생성은 다른 `attempt_id`를 사용한다.
다음 기록은 MCP 응답이나 DB 스키마를 바꾸지 않고 stderr에만 남는다.

| event | 기록 내용 |
|---|---|
| candidate | 생성 직전 모든 후보의 chunk_id, evidence_id, pdf_page, supplemental, follows_evidence_id |
| claim_selection | 구조 검증을 통과한 주장 번호(claim_number), 선택한 evidence_id, 해당 근거의 support_count |
| quality | 주장 번호, 처음 발견한 위험 사유(reason_code), 특정 근거에 해당하면 evidence_id |

`supplemental=true`는 다음 청크 조회로 보충된 후보다. `follows_evidence_id`는
생성기에 전달한 앞뒤 연결 관계다. `support_count=0`이면 구절 정보 없이 청크 전체로
보수적으로 평가했다. 실제 supports 문구는 기록하지 않는다.

| reason_code | 의미 |
|---|---|
| NO_EXTRACTION_RISK | 이번 경계·숫자 검사에서 위험을 찾지 못함. 의미 일치 보장은 아님 |
| STANDALONE_NUMBER | 주장 숫자가 인용 원문의 숫자 단독 줄에만 존재함(인용 근거 집합 기준) |
| EMPTY_BODY | 숫자 단독 줄 등을 제외한 원문 본문이 비어 있음 |
| SUPPORT_NOT_FOUND | 생성기가 선택한 구절이 원문에 없음 |
| SUPPORT_LOCATION_UNRESOLVED | 구절 위치가 없거나 중복되어 위치를 특정하지 못함 |
| UNFINISHED_TAIL | 미완결 끝부분을 사용했고 인용된 다음 조각으로 연결을 확인하지 못함 |
| UNRESOLVED_PREFIX | 잘린 시작부분을 사용했고 인용된 앞 조각으로 연결을 확인하지 못함 |

기존 판정 순서를 유지하므로 모든 위험 사유를 수집하지 않고 첫 사유만 기록한다.
잘못된 생성 형식·근거 ID는 기존 구조 검증에서 거절되며 claim_selection/quality가 생기지 않는다.
빈 초안도 두 기록이 없으므로 generation 종료만으로 정상 주장 생성을 단정하지 않는다.

7페이지가 빠졌다면 같은 request_id와 attempt_id에서 확인한다.

1. candidate에 7페이지가 없음: 생성 전 후보 구성·보충 경로를 조사한다.
2. candidate에는 있으나 claim_selection에 해당 evidence_id가 없음: 생성기가 사용하지 않았다.
3. 양쪽을 인용했는데 partial: quality 사유와 follows_evidence_id를 확인한다.

이전 호출의 후보나 supports를 복원하지는 못한다. MCP를 재시작한 다음 호출부터 적용된다.
청크·근거 ID도 운영 메타데이터이므로 로그 공유 시 필요한 요청만 선별한다.

## 미완결 주장 한 번 보완

`UNFINISHED_TAIL`로 판정된 근거에 이어지는 후보가 이미 전달되어 있고,
그 주장이 다음 후보를 인용하지 않았을 때 보완한다. `UNRESOLVED_PREFIX`와
`SUPPORT_NOT_FOUND`도 다음 후보 유무와 관계없이 구절 재선택 대상으로 보완한다.
실제 OpenAI 생성기는 보완을 지원한다. 미지원·실패 시에도 아래 제외 정책을 적용한다.

- 원래 질문·후보·보완 대상만 모은 초안·대상 주장 번호·근거 ID를 같은 모델에 전달한다.
  비대상 주장은 서버에서 원래 문구·근거 ID·supports 그대로 보존한다.
- 근거 ID만 붙이지 않고 누락된 조건을 주장에 반영하거나 불완전한 주장을 제외하도록 요청한다.
- 구조와 품질을 다시 검사한다. 위험이 남은 보완 주장은 사유와 관계없이 제외한다.
- API 사용 불가·시간 초과·응답 형식이나 근거 연결 오류면 보완 대상만 제외하고 비대상은 보존한다.
- 보완 후 대상 claims가 비어도 비대상 주장은 유지한다. 최종 주장이 모두 없으면
  insufficient_evidence / extraction_limited이며 근거를 저장하지 않는다.
- 요청당 보완은 최대 한 번이다. 보완 후 빈 초안이 되어도 추가 검색을 시작하지 않는다.
- 기존 빈 초안 추가 검색 뒤에 처음 보완 조건이 생긴 경우에는 그때 한 번 보완할 수 있다.
- 최종 채택한 초안의 근거만 한 번 저장한다. 보완 전 결과는 저장하지 않는다.
- 사용자 페이지·섹션 범위, 검색 방식, DB 스키마는 바꾸지 않는다.

보완도 기존 연결 제한 5초와 `GENERATION_TIMEOUT_SECONDS` 읽기 제한을 사용한다.
이 값은 전체 요청의 절대 종료 시간이 아니다. 추가 호출만큼 지연·비용이 늘 수 있다.
실패한 HTTP 요청을 자동 재시도하는 것은 아니며, 정상 생성된 초안을 대상으로 하는 별도 호출이다.

진단에서 같은 `attempt_id`의 `round_number=0`은 최초 초안, `1`은 보완 초안이다.
후보 ID는 동일하게 유지한다. `repair_target`은 주장 번호·미완결 evidence_id·next_evidence_id를
기록한다. `repair_outcome`은 REPAIR_ADOPTED, REPAIR_UNAVAILABLE, REPAIR_INVALID 중 하나다.
REPAIR_ADOPTED는 보완 초안 채택이지 ok 보장이 아니다. round_number=1의 quality도 확인한다.
보완 실패 시 round_number=0의 비대상 주장을 유지하고 대상은 제외한다.
대상의 최초 품질 사유는 round_number=0을 참조한다.

구조·구절 경계 검사는 의미 검증이 아니므로 verification_level은 structural_only를 유지한다.
조건이 올바르게 주장에 반영되었는지는 실제 호출 결과로 별도 확인해야 한다.

### 잘린 앞부분·원문 불일치 보완과 제외

- UNRESOLVED_PREFIX: 주장과 무관한 잘린 앞 조각까지 복사했다면 실제 근거 문장으로
  supports를 다시 선택한다. 그 조각에 주장이 의존하면 기존 후보에서 연결 근거를 찾거나 주장을 제외한다.
- SUPPORT_NOT_FOUND: 지정된 원문에 실제 있는 구절로 수정하거나 정확한 후보 ID를 인용한다.
  존재하지 않는 문장·조건은 생성하지 않도록 지시한다.
- 필요한 조건절을 잘라 검사를 피하지 않도록 지시하며 기존 원문 일치·경계 판정은 느슨하게 바꾸지 않는다.
- 두 사유의 보완도 기존 끝부분 보완과 합쳐 요청당 최대 한 번이다. 보완 응답에는 모든 인용 ID의
  supports가 있어야 하며 비어 있거나 일부 누락되면 형식·연결 실패로 처리한다.
- 최종 평가에 UNRESOLVED_PREFIX 또는 SUPPORT_NOT_FOUND가 남은 주장은 답변·claims에서 제외한다.
  해당 주장만 사용한 근거도 저장·반환하지 않는다. 보완 미지원이나 API 실패 때도 적용한다.
- 일부만 제외되면 partial / extraction_limited와 제외 안내를 반환한다. 모두 제외되면
  insufficient_evidence / extraction_limited를 반환하고 추가 검색이나 근거 저장은 하지 않는다.
- 생성기가 보완 대상 claims 수를 줄인 경우에도 일부 답변이 누락됐을 가능성을 표시한다.
  이는 질문의 각 항목이 모두 답변됐는지 의미적으로 검증한 것은 아니다.
- 보완 대상이 아닌 숫자 단독 줄·미완결 끝부분 등 다른 사유의 partial 정책은 유지한다.
  단, 보완을 시도한 대상은 재검사에서 어떤 위험이라도 남으면 제외한다.

`repair_target.reason_code`로 보완 사유를 구분한다. 앞부분·원문 불일치는
next_evidence_id가 null일 수 있다. `claim_excluded`는 서버가 제외한 초안의 주장 번호·근거 ID·
사유·round_number를 남기며 본문을 출력하지 않는다. 번호는 제외 전 초안 기준이다.
생성기가 스스로 제거한 주장은 이 이벤트에 나타나지 않으며, 수정 전후 claim_selection을 비교한다.

보완 입력의 주장 번호는 대상만 모은 초안에서 1부터 다시 부여한다. 최초 round_number=0과
repair_target의 번호는 전체 최초 초안 기준이고, round_number=1의 claim_selection·quality·
claim_excluded 번호는 보완 응답 기준이다. 근거 ID로 연결해 확인한다.
보완 응답이 대상 수보다 많은 주장을 반환하면 형식 오류로 거절한다. 보완은 최대 한 번이며,
통과한 대상 묶음을 최초 대상 위치에 넣고 비대상 주장의 상대적 순서는 유지한다.

검사가 첫 위험 신호만 반환하는 휴리스틱이라는 한계는 그대로다. 정상 판정이 의미적 근거 충족을
보장하지 않으며, 이 기능은 #20의 과거 verification_failed 원인이 해결됐다는 증거가 아니다.

## 복합 질문 verification_failed 진단 (#20)

외부 응답의 reason_code는 기존 verification_failed를 유지한다. 서버 로그의
`stage=evidence_trace`, `event=verification_failure`에서 다음을 구분한다.

| 로그 reason_code | 실패 지점 |
|---|---|
| GENERATION_JSON_INVALID | 생성 본문 JSON 해석 실패, 중복 키·비표준 상수 포함 |
| GENERATION_SCHEMA_INVALID | JSON 해석 후 필드·자료형·값 계약 검사 실패 |
| GENERATION_FORMAT_INVALID | 그 밖의 제공자 응답 형식 오류(예: 응답 봉투 구조) |
| DRAFT_INVALID | 생성기가 반환한 내부 초안 객체 형식 오류 |
| EMPTY_EVIDENCE_IDS | 주장에 인용 근거가 없음 |
| DUPLICATE_EVIDENCE_ID | 같은 주장 안에서 근거 ID가 중복됨 |
| UNKNOWN_EVIDENCE_ID | 후보에 없는 근거 ID를 인용함 |
| SUPPORT_REFERENCE_INVALID | supports가 해당 주장에 인용되지 않은 근거를 참조함 |

근거 연결 실패에는 1부터 시작하는 claim_number가 기록된다. 다른 주장끼리 같은 근거를
인용하는 것은 허용된다. 알 수 없는 ID 자체와 본문·예외 메시지는 로그에 남기지 않는다.
`round_number=0`은 최초 초안 실패, `1`은 보완 초안 실패다. 보완 실패는 최초 partial을
유지하므로 외부 응답의 verification_failed와 혼동하지 않는다.

### 재현 절차와 현재 한계

이슈 첨부 기록에는 실제 복합 질문의 도구 입력 JSON이 없다. 아래는 재구성한 질문이며,
당시 장애와 같은 원인이라고 단정할 수 없다. 과거 코드·청크와 현재 상태도 다를 수 있다.

1. MCP를 종료한 뒤 이 문서의 래퍼 설정으로 재시작한다.
2. 등록된 논문의 유효한 context_id를 사용한다. 재등록은 하지 않는다.
3. 당시 정확한 입력을 확보했으면 그것을 사용하고, 없으면 아래 재구성 입력으로 한 번만 호출한다.

```text
question: "Self-attention이 recurrent 층보다 계산 복잡도 측면에서 더 빠른 조건은 무엇이며, 최대 경로 길이는 장거리 의존성 학습에 어떤 영향을 주는가?"
top_k: 5
focus: {"pdf_pages": [6, 7]}
```

4. status·reason_code·answer_ko와 반환 근거 ID를 보존하고 같은 요청 시간대의 request_id로 로그를 확인한다.
   실패 응답에는 근거가 없을 수 있으므로 진단 중에는 다른 요청을 동시에 보내지 않는다.
5. verification_failure가 있으면 코드·주장 번호로 실패 경로를 특정한다. 생성 모델의 원본 JSON은 수집하지 않는다.
6. 성공하거나 partial이면 이번 호출에서는 해당 실패가 재현되지 않은 것이다. 원인 해결로 기록하지 않는다.

일반 테스트는 합성 초안으로 각 오류 분기와 정상 복합 초안 처리를 검증한다. 실제 모델에서
과거 장애를 재현했다는 의미는 아니다. 유료 API 자동 재호출·이슈 생성은 수행하지 않는다.

## 재현·해석

서버를 재시작해 새 코드를 로드한 뒤 동일한 요청을 한 번 실행한다.
Claude 호스트의 MCP stderr 로그를 확인하거나, 서버 실행 명령 뒤에
`2> ask-paper-diagnostics.log`를 붙여 별도 파일에 보관한다. stdout과 합치지 않는다.
요청 하나의 request_id로 묶고 start만 있고 end/error가 없는 span을 확인한다.
중첩 단계가 있으므로 가장 안쪽의 미완료 단계를 먼저 본다.

- ask_paper만 시작: 작업 스레드 진입 전 대기 등을 조사한다.
- chroma_validation 미완료: 전체 컬렉션 읽기·검증 단계에 머무른 상태다.
- embedding_model_load 미완료: import·캐시 접근·다운로드·모델 초기화 중일 수 있다.
- embedding_library_import 미완료: 모델 생성 전 라이브러리 import 구간이다.
- embedding_model_construct 미완료: 라이브러리 import는 끝났지만 모델 생성 내부에서 대기 중이다.
  디스크 캐시가 존재해도 완전성이나 원격 확인 생략을 보장하지 않는다.
- generation_api_call 미완료: 네트워크·제공자 응답 대기를 조사한다.
- evidence_save 미완료: SQLite 잠금·저장 등을 조사한다.

시작 로그만으로 근본 원인을 확정할 수는 없다. 강제 프로세스 종료 시 종료 로그가
없을 수 있으며, 호스트가 응답 대기를 끝냈어도 작업 스레드는 계속 실행될 수 있다.
이 로그는 MCP progress 알림이나 주기적 heartbeat가 아니며 호스트 timeout을 연장하지 않는다.
근거 없음·잘못된 생성 결과는 기존 정책대로 처리되므로 저장 단계가 생략될 수 있다.
실제 운영 데이터·유료 API를 자동 재호출하지 않는다.

## 호스트와 독립적으로 stderr를 파일에 저장하기

진행 중인 호출은 이 설정으로 바뀌지 않는다. 종료를 확인한 뒤 다음 실행부터 적용한다.
기존 MCP 설정의 env 등 다른 항목은 유지하고 command·args만 다음으로 바꾼다.
설정 파일은 자동 수정하지 않는다.

```json
{
  "command": "D:\\fd\\soloLeveling\\.venv\\Scripts\\python.exe",
  "args": ["D:\\fd\\soloLeveling\\scripts\\run_mcp_diagnostics.py"]
}
```

이 래퍼는 자식 서버를 따로 띄우지 않고 같은 프로세스에서 서버를 실행한다.
stdin·stdout은 그대로 유지하고 운영체제 stderr 파일 설명자만 실행별 파일로 연결한다.
기존 실행 환경 변수와 작업 디렉터리는 유지하며 소스는 래퍼가 있는 저장소의 src를 사용한다.
로그는 `D:\fd\soloLeveling\runtime\diagnostics\mcp-시각-UUID.log`에 저장된다.
`--log-dir`로 다른 위치를 지정할 수 있다. 파일은 덮어쓰지 않고 자동 삭제하지 않는다.

서버나 API를 실행하지 않고 파일 수집 경로만 점검하는 명령:

```powershell
.\.venv\Scripts\python.exe scripts/run_mcp_diagnostics.py --check
Get-ChildItem .\runtime\diagnostics\*.log | Sort-Object LastWriteTime -Descending
```

시작 기록 diagnostic_startup에는 Python·진단 모듈·서버 모듈 경로와 PID가 담긴다.
이는 소스 선택 확인용이며 서버 초기화 성공을 뜻하지는 않는다.
check 실행은 diagnostic_probe 시작·종료만 기록한다. 실제 재연결 후 생성된 별도 로그에서
ask_paper 단계를 확인한다. 로그를 읽을 때 `Get-Content <로그경로> -Tail 30`을 사용할 수 있다.

**주의:** 단계별 JSON은 본문을 기록하지 않지만, 이 파일은 외부 라이브러리의 stderr도
그대로 수집하므로 민감한 정보나 로컬 경로가 들어갈 수 있다. 전체 파일을 공유하지 말고
request_id·span_id·stage·event·elapsed_ms·error_code 필드만 선별한다.
로그 저장 실패 시 서버 실행을 시작하지 않는다. 로그 폴더는 Git 제외 대상이다.

## import 지연 스택 수집

진단 래퍼로 실행하면 embedding_library_import가 30초 이상 걸릴 때 faulthandler가
모든 Python 스레드의 스택을 한 번 기록한다. 같은 로그 폴더의
`import-stack-요청ID-진단ID.log`이며 요청 ID로 단계 로그와 연결한다.
정상 완료·예외 종료 시 예약을 취소한다. 파일은 예약 시 생성되므로 빠르게 종료하면
빈 파일이 남을 수 있다. 서버나 요청을 강제 종료하지 않는다.

변수 값·소스 본문은 수집하지 않지만 경로·줄 번호·함수명이 포함된다.
네이티브 라이브러리 내부 원인까지 보장하지는 않는다.
전역 타이머이므로 동시 import 중 하나만 감시하고 나머지는 WATCHDOG_BUSY를 기록한다.
다른 dump_traceback_later 예약과 병용하지 않는다. 예약 실패는 WATCHDOG_SETUP_FAILED로
기록하고 import는 계속한다. 현재 호출 종료 후 MCP를 재시작해야 적용된다.

## 메인 스레드 선행 import 비교 옵션

래퍼에 `--preload-embedding-library`를 지정하면 MCP 서버 실행 전에 메인 스레드에서
sentence_transformers를 import한다. 기본값은 꺼짐이다. 모델 생성·다운로드는 요청하지 않는다.
`startup_embedding_import` 시작·종료 시간을 기록하고 30초 지연 시 같은 스택 감시를 적용한다.
`--check`가 함께 있으면 선행 import도 수행하지 않는다.

비교 실행 시 MCP args 예:

```json
["D:\\fd\\soloLeveling\\scripts\\run_mcp_diagnostics.py", "--preload-embedding-library"]
```

시작 시간이 늘어나므로 MCP 연결 제한 시간에 걸릴 수 있다. 먼저 연결과 선행 import 종료 로그를
확인한 뒤 질문을 한 번 호출한다. 성공하더라도 초기화 순서 변경의 우회 효과일 뿐,
DLL 잠금 등 근본 원인을 확정하는 증거는 아니다. BLAS 설정 등 다른 조건은 동시에 바꾸지 않는다.
