# #60 C 상태 응답 계약 검토 및 검증 계획

2026-11-03 · 기준 main `2230865` · macOS · C 검토안, 미합의

## 목적과 변경 경계

A의 실패 정보 구조화 제안이 MCP와 상태 카드까지 전달되기 위한 계약을 확인한다.
제품 코드·DB·A/B 파일은 수정하지 않았다. 이 폴더의 실험과 문서만 추가했다.
실제 API·모델·사용자 논문·Desktop 설정은 사용하지 않았다.

근거: [A 설계안](https://github.com/soogwon/soloLevelingPaper/issues/60#issuecomment-6601645798).
설계안의 “4개 필드”와 달리 표에는 outcome을 포함해 5개가 있다.

## 현재 경로와 재현 결과

`workers/ingestion.py:get_ingestion_status` →
`interfaces/mcp/ingestion.py:IngestionManager.get_status` →
`interfaces/mcp/server.py:get_paper_status` → `ui/paper-status/status.js`

1. 현재 worker는 job 상태·단계·limitations 및 게시 결과를 조회한다. 신규 5개 필드는 없다.
2. MCP는 결과를 그대로 전달하지 않고 기존 7개 필드로 다시 구성한다.
   PaperStatusOutput 스키마에도 신규 필드가 없다.
3. A의 제안 필드를 가진 합성 서비스 응답을 넣어도 **5개 모두 MCP 응답과 스키마에서 빠진다.**
   JSON 텍스트와 structuredContent는 같은 기존 응답이다.
4. 상태 카드는 status·stage·limitations와 고정 안내를 사용한다.
   MCP에 필드만 추가해도 실패 원인·다음 행동의 화면 표시가 자동으로 생기지 않는다.
5. 부분 번역 성공 후 색인으로 넘어가는 worker 경로에서 stage는 index가 된다.
   따라서 ready/partial의 failed_stage를 job.stage에서 그대로 가져오면 잘못된 실패 단계를 표시한다.

재현 명령(저장소 루트, 개발 의존성 설치 환경):

```sh
PYTHONPATH=src python mini-projects/c-status-contract/probe_status_fields.py
PYTHONPATH=src python -m pytest tests/unit/mcp/test_status_app.py tests/unit/mcp/test_ingestion_shutdown.py tests/integration/test_mcp_stdio.py -q
```

- 실험: missing_in_schema와 missing_in_response에 failed_stage, failure_code,
  next_action, outcome, retryable 모두 출력됨. ready/index 및 start_learning은 유지됨.
- 기존 회귀: **16 passed**. 이는 새 계약 구현 완료를 의미하지 않는다.
- 실험은 실제 MCP 서버의 도구 호출·스키마를 사용하고 ingestion만 대역으로 교체한다.
  암호 PDF·실제 타임아웃·Desktop 화면은 이번 실행에서 검증하지 않았다.

## 합의가 필요한 의미

| 항목 | C 의견 및 확인 요청 |
| --- | --- |
| ready와 partial | “ready는 오류 필드가 null”을 ready/complete로 한정하고, ready/partial에서는 번역 실패 정보를 유지하는 것을 제안한다. 검색 가능과 전체 번역 완료를 구분한다. |
| stage와 failed_stage | stage는 현재/마지막 처리 단계, failed_stage는 선택된 대표 실패 원인의 단계로 구분한다. ready/index라도 번역 일부 누락이면 failed_stage=translate가 가능해야 한다. |
| 복수 원인 우선순위 | limitations의 첫 항목은 empty_pages 같은 제한이거나 이전 번역 실패일 수 있다. 번역 일부 실패 뒤 임베딩이 실패했다면 최종 실패를 막은 index 원인이 대표여야 한다. 단순 첫 문자열 대신 실패 상태와 고정 코드 우선순위를 A가 정의해야 한다. |
| retryable 의미 | 같은 입력의 재실행 허용 여부인지, 복구 경로로 해결 가능한지 명확히 한다. true는 성공 보장이나 자동 실행 허가가 아니다. 분류 불명 ingestion_failed를 무조건 true로 둘 근거는 부족하므로 null/contact_maintainer 등 보수적 처리를 협의한다. |
| INVALID_RESULT | false/contact_maintainer를 우선 제안한다. 운영 환경 수정 후 재시도 가능성까지 영구 차단하는 뜻은 아니다. |
| null과 none | next_action의 null과 문자열 none이 같은 뜻인지 정한다. 권장안은 권고 없음=null, 판단 불가도 추가 안내로 구분하고 none의 중복 사용을 피하는 것이다. 최종 값 목록은 A와 합의한다. |
| interrupted | 자동 이어하기 금지에 동의한다. 서버 재시작으로 중단된 경우 실패 코드가 없을 수 있으므로 안전한 고정 코드/null 정책이 필요하다. 호스트 대기 종료만으로 interrupted로 바꾸지 않는다. |
| partial의 범위 | 번역 누락만 partial인지, empty_pages 등 원문 추출 제한도 포함하는지 명시한다. outcome=complete를 논문의 모든 내용이 완전하다는 뜻으로 표시하지 않는다. |
| 복구 도구 가용성 | run_translation_recovery는 현재 MCP에 없다. #141 저장소 기반만으로 사용 가능한 기능이라고 안내하지 않는다. #30 B 후속 연결 전에는 정책상 복구 가능과 현재 실행 가능을 구분해 텍스트로 제한을 알리고 실행 버튼을 만들지 않는다. |
| 재등록 요청 키 | 동일 파일 재등록의 새 작업과 동일 요청의 통신 재시도를 구분한다. 기존 request_key 재사용은 과거 요청 반환일 수 있으므로 실패 후 새 작업의 키 정책을 A와 확인한다. 사용자 의도 없이 키를 바꿔 자동 재등록하지 않는다. |
| 고정 코드와 원본 데이터 | 신규 필드는 허용된 고정 코드만 노출한다. 기존 limitations에는 청크 ID 등이 있으므로 전체 응답이 이미 고정 코드뿐이라고 표현하지 않는다. 기존 제한 문자열의 유지·정제 책임도 별도로 정한다. |

## 합의 후 구현·검증할 사례

아래는 실행 결과가 아닌 인수 검증 계획이다. 정책이 미정인 값은 C가 임의로 확정하지 않는다.

| 입력/상황 | 핵심 기대 및 확인 대상 |
| --- | --- |
| queued / processing | outcome·실패 정보의 null 정책, 현재 단계 유지, 자동 재시도 없음 |
| ready + 번역 누락 없음 | complete 의미 확인, 검색 가능 유지, 권고 없음 표현 |
| ready/index + 일부 번역 실패 | partial, failed_stage=translate, 고정 원인, 학습 시작 가능 유지, 복구 미구현 안내 |
| failed/parse + 암호·손상 PDF | replace_file, retryable=false; 경로·원본 예외·본문 비노출 |
| failed/parse + extraction_empty | 전체 추출 실패와 일부 empty_pages를 구분 |
| failed/translate + 전체 번역 실패 | 대표 원인 및 재등록 정책, 완료 청크 재사용 여부는 A/B 통합으로 검증 |
| failed/index + EMBEDDING_TIMEOUT/UNAVAILABLE | index 실패 원인, 재시도 안내만 표시하고 실행하지 않음 |
| 번역 누락 후 index 실패 | 이전 번역 제한보다 최종 index 실패가 대표 원인인지 확인 |
| INVALID_RESULT / scheduler_failed / 미분류 실패 | 누락 코드의 명시적 fallback, 임의 문자열을 failure_code로 복사하지 않음 |
| 실제 시작 전 Future 취소 성공 | interrupted/SERVER_SHUTTING_DOWN, 시작된 작업과 구분 |
| 실행 중 서버 종료·재시작 | 실제 저장 상태를 기준으로 안내, 취소/중단 완료를 추정하지 않음 |
| 없는 작업·잘못된 입력 | 기존 오류 코드·본문 비노출·읽기 전용 계약 유지 |
| 이전 형식 응답·신규 null 필드 | 기존 사용자 안내로 폴백, 카드가 깨지거나 undefined를 표시하지 않음 |
| 알 수 없는 신규 코드 | 안전한 일반 안내, 값으로 HTML 실행·임의 명령·재등록 유발 금지 |

각 사례에서 도구 목록의 outputSchema, structuredContent, JSON 텍스트 폴백을
함께 비교한다. status 카드 DOM 테스트와 번들 빌드 후 실제 Desktop에서
실패 이유·다음 행동 표시, 새로고침의 조회 전용 동작을 확인한다.
합성 암호 PDF, 대역 번역·임베딩 및 제어 가능한 종료 이벤트를 사용해 외부 비용 없이 검증한다.
기존 성공 응답·부분 성공의 start_learning capability를 회귀 검증한다.

## 담당과 다음 단계

### B 답변 반영과 남은 A 확인

[B의 최신 답변](https://github.com/soogwon/soloLevelingPaper/issues/60#issuecomment-6605195421)을
반영했다. 아래는 B/C의 권고이며 A의 최종 정책 확정이나 제품 구현을 의미하지 않는다.

| 상황 | B/C 권고 | C 표시 방향 |
| --- | --- | --- |
| ready/index + provider_unavailable | outcome=partial, failed_stage=translate, retryable=null, next_action=null | 검색 가능, 일부 구간 번역 누락 |
| ready/index + empty_translation | 위와 동일. 빈 번역이 나온 원인은 코드만으로 알 수 없음 | 검색 가능, 일부 구간 번역 누락 |
| failed/translate + translation_failed | outcome=failed, retryable=null 권고. 재등록 행동·요청 키 정책은 A 확인 필요 | 번역 실패, 재시도 가능 여부 미확인 |

- retryable=null은 false가 아니다. true도 성공 보장이나 자동 재실행 허가가 아니다.
- ready/partial의 stage=index는 마지막 단계이며 failed_stage=translate와 모순되지 않는다.
- run_translation_recovery는 현재 공개 도구가 아니다. #141·#147만으로 복구 버튼을
  제공하거나 동일 파일 재등록으로 게시된 색인의 누락 구간이 복구된다고 안내하지 않는다.
- 전체 번역 실패 실험의 next_action=null은 미확정 행동을 넣지 않기 위한 합성 값이다.
  B가 전체 실패의 next_action까지 null로 확정했다는 의미가 아니다.
- 재현 실험의 기존 retryable=true / run_translation_recovery 값은 초기 A안의
  누락 재현용이었다. 최신 B 권고를 반영한 세 사례로 교체했다.

실험은 MCP ClientSession의 메모리 전송에서 세 응답을 각각 조회한다. JSON 텍스트와
구조화 응답의 일치, ready/failed와 현재 단계 보존, 부분 성공의 start_learning 유지,
각 사례의 조회 1회·그 외 ingestion 호출 0회를 확인한다. 누락된 필드는 관찰 결과로
출력하며, 스크립트 종료 코드 0을 신규 계약 전달 성공으로 해석하지 않는다.

보완 후 PR #148 브랜치(main `2230865` 기반)에서 재실행한 결과, 세 사례 모두
신규 5개 필드가 스키마·응답에서 누락됐다. 기존 status/stage/capabilities와
텍스트 일치, 조회 외 호출 없음은 확인했다. 위 기존 회귀 16개도 다시 통과했다.
최신 main 전체·실제 실패 처리·Desktop 화면을 재검증한 결과는 아니다.

A에게 남은 확인은 대표 원인 우선순위, 미분류·중단 fallback, next_action의 최종
목록/null 정책, 의도적 재등록의 요청 키 정책이다. 합의 전에는 C가 limitations
문자열에서 실패 상태를 추론해 제품 필드를 채우지 않는다.

- A: DB 값 기반 대표 실패 선택·필드 계산·재시도 및 중단 정책과 단위 테스트.
- B: 번역 실패 코드 목록과 #30 복구 서비스 가용성·호출 계약 확인.
- C: 합의된 5개 필드의 TypedDict·응답 매핑·도구 설명·화면 안내 및 계약 테스트.
  변경 예상 위치는 server.py, 상태 UI 소스·생성 자산, MCP/DOM 테스트다.
- 현재 A의 신규 필드 PR은 확인되지 않았으므로 제품 수정은 보류했다.
  합의 후 A PR에 맞춰 C 연결을 구현하고 함께 검증한다.

#60은 아직 완료가 아니다. 이 문서와 실험은 A/B 협의를 위한 검토 자료이며,
게시·병합되더라도 제안 계약의 합의나 제품 구현 완료를 의미하지 않는다.
