# OpenAI 답변 생성 연결

## 구현 범위

`OpenAIClaimGenerator`는 Responses API에 질문과 검색된 한국어 근거·원문을 전달하고,
구조화 응답을 기존 `GeneratedAnswerDraft`로 변환한다. 기본 모델은 `gpt-5.4-mini`다.
SDK 대신 기존 프로젝트에서 쓰는 requests로 공식 HTTP API를 호출한다.
번역 API·MCP 도구 연결·의미 검증은 이 작업의 범위가 아니다.

참조: [구조화 응답](https://developers.openai.com/api/docs/guides/structured-outputs),
[모델 사양](https://developers.openai.com/api/docs/models/gpt-5.4-mini).

## 설치 및 환경 설정

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[generation]"
```

환경 변수는 `.env.example`을 참고한다. 생성기 자체는 `.env`를 자동으로 읽지 않는다.
아래 로컬 실행 스크립트가 명시한 파일을 읽는다. 다른 진입점에서는 환경 변수를
주입하거나 별도 로더를 사용해야 한다.

### 로컬 설정 확인 (외부 호출 없음)

저장소 루트에서 실행한다.

```powershell
.\.venv\Scripts\python.exe scripts/check_generation.py
```

- 스크립트 위치를 기준으로 저장소 루트의 `.env`를 읽는다. 상위 폴더를 자동 탐색하지 않는다.
- 다른 파일은 `--env-file 경로`로 명시한다. 상대 경로는 현재 작업 폴더 기준이다.
- 기존 환경 변수가 `.env`보다 우선하며, 빈 기존 환경 변수도 임의로 덮어쓰지 않는다.
- 프로세스 환경 자체는 변경하지 않고 합친 설정을 생성기에 전달한다.
- UTF-8 및 UTF-8 BOM 파일을 지원한다. `${변수}` 치환은 하지 않으므로 값을 직접 입력한다.
- 기본 실행은 설정 형식만 확인한다. 키 값이나 설정 전체를 출력하지 않는다.
- 인증·잔액·모델 접근 권한은 확인하지 않는다. 외부 호출을 허용해도 기본 실행은 요청하지 않는다.

### 실제 호출 (선택 사항, 비용 발생 가능)

```powershell
.\.venv\Scripts\python.exe scripts/check_generation.py --live
```

`--live`와 `ALLOW_EXTERNAL_API=true`, `LOCAL_ONLY=false`가 모두 필요하다.
사용자 논문이 아닌 스크립트에 포함된 합성 한국어·영문 근거 하나로 생성기를 호출한다.
JSON 변환과 근거 ID를 검사하고 주장 내용을 출력한다. DB 저장이나 검색 통합·의미 검증은 하지 않는다.
자동 테스트는 생성기/HTTP를 대체하므로 이 옵션의 테스트에서도 외부 호출은 없다.

| 변수 | 기본값 및 역할 |
|---|---|
| GENERATION_PROVIDER | openai. 현재 다른 제공자는 거부 |
| GENERATION_MODEL | gpt-5.4-mini |
| OPENAI_API_KEY | 필수. Git·로그·채팅에 올리지 않음 |
| GENERATION_TIMEOUT_SECONDS | 30. 읽기 대기 시간이며 전체 실행 시간 제한은 아님 |
| GENERATION_MAX_OUTPUT_TOKENS | 2048 |
| ALLOW_EXTERNAL_API | false. 실제 호출하려면 명시적으로 true |
| LOCAL_ONLY | false. true이면 외부 호출을 차단 |

이전 예제의 미사용 `GENERATION_API_KEY` 대신 `OPENAI_API_KEY`를 사용한다.
키는 설정 객체의 repr에서 제외한다. .env는 기존 Git 제외 규칙을 유지한다.
출력 형식이 호환되는 모델로만 설정을 바꿔야 하며, 사용 권한·한도는 계정에서 확인한다.

## 기존 답변 서비스에 주입

```python
from solo_leveling.application.evidence_qa.answer import AnswerService
from solo_leveling.infrastructure.generation.openai_generator import (
    OpenAIClaimGenerator, OpenAIGenerationSettings,
)
from solo_leveling.infrastructure.generation.evidence_store import SQLiteEvidenceWriter

# search_service와 db_path는 기존 검색 서비스 구성과 동일하게 준비한다.
generator = OpenAIClaimGenerator(OpenAIGenerationSettings.from_env())
service = AnswerService(search_service, generator, SQLiteEvidenceWriter(db_path))

# 아래 호출은 허용 설정과 유효한 키가 있으면 실제 외부 전송·과금이 발생한다.
response = service.answer("이 방법의 장점은 무엇인가?", version_id=version_id)
```

생성기 구성만으로 네트워크 요청이 발생하지 않는다. 기존 fake 생성기를 자동 교체하지도 않는다.
조립하는 쪽에서 명시적으로 주입한다. 테스트용 한국어 근거로 생성 기능만 확인할 수 있으나,
실제 논문 전체 흐름에는 번역 제공자 연결이 별도로 필요하다.

## 요청 및 실패 정책

AnswerService의 근거 부족 시 추가 검색(기본 5개 → 10개)과 최대 2회 생성 정책은
[evidence-search-expansion.md](evidence-search-expansion.md)를 참고한다.
이는 아래 HTTP 자동 재시도 0회 정책과 별개다.

- instructions와 질문·근거 데이터를 분리한다. 문서 속 지시를 실행하지 않도록 지시한다.
- 모델은 주장·근거 ID만 생성한다. 페이지·인용문은 기존 서버 코드가 구성한다.
- strict JSON Schema 요청 후에도 기존 공통 파서·참조 검증을 수행한다.
- `store=false`로 요청한다. 이것이 제공자의 모든 보관·로그 정책을 없앤다는 뜻은 아니다.
- 공식 HTTPS 주소로만 전송하며 HTTP 리다이렉트는 따라가지 않는다.
- 연결 timeout은 5초, 읽기 timeout은 설정값이다. 자동 재시도는 0회다.
- 429, 인증 오류, 서버 오류, 연결 오류, timeout을 `OpenAIGenerationError.kind`로 구분한다.
- 거절과 미완료 응답도 생성 오류로 처리한다. 부분 JSON을 사용하거나 빈 근거로 위장하지 않는다.
- JSON/응답 구조 오류는 `GenerationFormatError`이며 AnswerService에서 verification_failed가 된다.
- AnswerService는 제공자 오류를 기존 공개용 AnswerGenerationError로 바꾼다.
  상세 kind는 어댑터 경계에서만 제공되며 공개 API 오류 코드 확장은 하지 않았다.
- 실제 비용이 발생한 뒤 응답 수신에 실패했을 수도 있으므로 재시도는 자동 수행하지 않는다.
- 키·요청 본문·제공자 오류 본문을 출력하지 않는다.

## 검증과 남은 작업

자동 테스트는 HTTP를 대체한다. 키 없이 정상 응답, 요청 스키마, 설정, 차단 정책,
형식 오류, timeout, 호출 제한, 인증 실패, 거절·미완료를 확인한다.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_openai_generator.py -q
```

실제 모델 호출과 품질 평가는 별도다. 먼저 공개·합성 자료만 사용하고, 외부 전송과 비용을
확인한 뒤 소량 실행한다. generation-evaluation.md의 기준으로 내용과 참조를 평가한다.
요청별 토큰·비용 기록, 전체 실행 기한, 재시도 정책 확장은 후속 작업이다.
