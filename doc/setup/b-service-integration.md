# B 서비스 연결 안내

## 책임 경계

`infrastructure.bootstrap.build_services()`는 호스트가 제공하는 DB 경로,
Chroma 클라이언트, 생성·번역 설정으로 객체만 조립한다.
조립 중 DB 초기화, 환경 파일 로딩, 모델 다운로드, API 호출은 하지 않는다.
설정은 기존 `OpenAIGenerationSettings.from_env(values)`와
`OpenAITranslationConfig.from_env(values)`로 구성할 수 있다.
실제 호출의 외부 API 허용 여부는 기존 제공자가 검사한다.

호스트(C)는 저장소 초기화·수명 관리, 환경 로딩, MCP 등록과 응답 변환,
파일 접근 제한, 등록 작업 실행·상태 조회, request_key 정책을 담당한다.
서비스는 동기식이므로 비동기 MCP 실행기에서는 블로킹 작업을 분리해야 한다.

## 사용

```python
services = build_services(
    db_path, chroma_client, generation=generation_settings,
    translation=translation_config,
)
# 등록 함수의 기존 키워드 인자로 전달한다.
translation_service = services.translation
translation_settings = services.translation_settings

response = services.answer.answer(question, version_id=version_id)
payload = serialize_answer_response(response)

# 이후 별도 요청에서도 검색·생성 없이 저장된 근거를 조회한다.
result = services.evidence.get(response.context_id, evidence_ids)
evidence_payload = serialize_stored_evidence_result(result)
```

`build_services`는 `infrastructure.bootstrap`, 두 직렬화 함수는
`application.evidence_qa.serialization`에서 가져온다.
근거 조회는 같은 SQLite 트랜잭션에서 context 소속, 리비전, 청크와 인용문을
검증한다. 하나라도 누락되거나 손상되면 부분 성공으로 반환하지 않는다.
새 직렬화 함수는 이 조회 결과 전용이며 자체적으로 DB 출처를 검증하지 않는다.
기존 검색 결과 기반 직렬화 함수의 검증은 그대로 유지한다.

## 오류 연결

| 서비스 오류 | 권장 MCP 분류 |
|---|---|
| InvalidArgumentError | INVALID_ARGUMENT |
| ResourceNotFoundError | NOT_FOUND |
| ContextNotReadyError | PAPER_NOT_READY, 기존 job_id·status로 안내 |
| AnswerGenerationError | UPSTREAM_UNAVAILABLE |
| DataIntegrityError 및 기타 예상하지 못한 오류 | INTERNAL_ERROR |

새 입력·조회 오류는 ValueError의 하위 타입으로 기존 호출 호환성을 유지한다.
일반 ValueError를 일괄 INVALID_ARGUMENT로 변환하면 안 된다. 기존 내부 검증의
ValueError도 있으므로 분류되지 않은 오류는 내부 오류로 처리한다.
호스트는 예외 문자열·스택·절대 경로를 그대로 반환하지 않고 고정 안내문을 사용한다.
근거 부족은 예외가 아니라 기존 답변 status와 reason_code로 전달한다.

## C와 합의할 항목

- 현재 답변 payload의 context_id·verification_level·answer 구조와 MCP 문서의 최상위 답변 필드 차이.
- context_id 생략 시 version_id로 기본 맥락을 생성하는 입력 확장 여부.
- 구조 검증만 수행하는 현재 구현의 structural_only 표시 유지.
- start_learning의 요약과 get_learning_guide는 이번 연결에 포함하지 않는다.

MCP 서버나 기존 도구 계약을 이 변경으로 추가·변경하지 않는다.
