# 등록 처리 상태 계약 (#60)

등록 응답(상태 조회 `get_ingestion_status`, 등록 결과)에는 다섯 키가 **항상** 들어가며 값이 없으면 JSON `null`이다.
값은 A(`solo_leveling.domain.ingestion_failure.describe_outcome`)가 계산하고 MCP·UI는 그대로 전달한다.
`limitations` 문자열을 다시 해석해 행동을 정하지 않는다.

| 키 | 값 |
|---|---|
| failed_stage | download / parse / translate / index / null |
| failure_code | 아래 고정 코드 또는 `TranslationFailureCode`에 정의된 번역 실패 코드(`provider_unavailable`, `empty_translation`) / null |
| retryable | true / false / null |
| next_action | register_again / replace_file / null |
| outcome | complete / partial / failed / null(처리 중) |

`retryable`: 같은 입력으로 새 작업을 시작하는 것이 허용되는가(성공 보장 아님). `null`은 현재 제공되는 방법이 없거나 판단하지 않음, `false`와 다르다.

## 매핑

| 상태 | failed_stage | failure_code | retryable | next_action | outcome |
|---|---|---|---|---|---|
| ready, 번역 실패 없음 | null | null | null | null | complete |
| ready, 일부 청크 번역 실패 | translate | 첫 번역 실패 코드. 항목은 `translation_failed:<청크 ID>:<코드>` 세 필드여야 하고(청크 ID는 영문·숫자·밑줄·하이픈 1~64자, 코드는 `TranslationFailureCode` 값), 필드 수·청크 ID·코드가 하나라도 어긋나거나 미등록 값이면 고정 코드 `translation_failed` | null | null | partial |
| failed: pdf_encrypted / pdf_unreadable / extraction_empty | parse | 해당 코드 | false | replace_file | failed |
| failed: translation_failed(전체 번역 실패) | translate | translation_failed | true | register_again | failed |
| failed: EMBEDDING_TIMEOUT / EMBEDDING_UNAVAILABLE | index | 해당 코드 | true | register_again | failed |
| failed: EMBEDDING_INVALID_RESULT | index | 해당 코드 | false | null | failed |
| failed: 그 밖(미분류) | 작업 단계 | ingestion_failed | true | register_again | failed |
| interrupted | 작업 단계 | SERVER_SHUTTING_DOWN, 없으면 interrupted | true | register_again | failed |
| queued / processing | null | null | null | null | null |

- 같은 단계에서 코드가 여럿이면 `limitations`의 첫 번째 고정 코드를 대표로 쓴다(번역 실패는 chunk_id 순). 전체 목록은 `limitations`에 그대로 남는다.
- `empty_pages:…` 같은 품질 경고는 failure_code도 outcome도 바꾸지 않는다.
- 알 수 없는 `limitations` 문자열은 failure_code로 쓰지 않고 fallback으로 처리한다. 예외 메시지·경로·원문은 응답에 담지 않는다.

## 재등록 키

- 의도적 재등록은 **새 request_key**로 한다. 같은 키로 다시 보내면 새 작업을 만들지 않고 이전 작업을 그대로 보여 준다
  (실패 작업은 `failed`와 위 계약값으로 보이며 처리 중으로 보이지 않는다. 테스트로 확인함).
- 새 키의 등록은 실패·중단 이력을 남기고 새 작업을 시작하며, 이미 번역된 청크는 재사용한다.
- 자동 재등록은 하지 않는다. 부분 번역 누락(`partial`)은 공개 복구 도구가 없어 `next_action=null`이다.
