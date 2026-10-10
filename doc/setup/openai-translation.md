# OpenAI 번역 연결

## 범위

`OpenAITranslationProvider`는 기존 `TranslationProvider.translate(request, settings)`를 구현한다.
청크 하나의 원문을 Responses API에 보내고 한국어 번역문을 반환한다.
기존 TranslationService, 등록 작업자, DB 스키마는 변경하지 않는다.
답변 생성 설정과 별도이며 초기 기본 모델은 `gpt-5.4-mini`다. 번역 품질 평가는 별도로 필요하다.

참조: [OpenAI 구조화 응답](https://developers.openai.com/api/docs/guides/structured-outputs).

## 설정 및 안전한 확인

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[translation]"
.\.venv\Scripts\python.exe scripts/check_translation.py
```

기본 실행은 저장소 루트 .env를 읽고 설정만 확인한다. 인증·잔액·모델 접근은 확인하지 않는다.
`--env-file`로 다른 파일을 지정할 수 있다. 기존 환경 변수가 파일보다 우선하며 프로세스 환경은
변경하지 않는다. UTF-8 BOM을 지원하고 변수 치환은 하지 않는다. 키는 출력하지 않는다.

| 설정 | 기본값 |
|---|---|
| OPENAI_API_KEY | 필수, 답변 생성과 공유 |
| TRANSLATION_PROVIDER | openai |
| TRANSLATION_MODEL | gpt-5.4-mini |
| TRANSLATION_PROMPT_VERSION | ko-translation-v1 |
| TRANSLATION_TARGET_LANGUAGE | ko |
| TRANSLATION_TIMEOUT_SECONDS | 30 |
| TRANSLATION_MAX_OUTPUT_TOKENS | 4096 |
| ALLOW_EXTERNAL_API | false |
| LOCAL_ONLY | false |

답변의 GENERATION_MODEL 등을 변경해도 번역 설정은 바뀌지 않는다.
프롬프트 버전은 실제 구현된 ko-translation-v1만 허용하며 이름만 임의로 바꿀 수 없다.
프롬프트를 수정할 때는 새 버전과 그에 대응하는 구현을 함께 관리해야 한다.

## 기존 등록 흐름에 연결

아래 코드는 환경 변수가 준비된 실행 프로세스에서 사용한다. 생성기처럼 config 자체는
.env를 읽지 않으며 파일 로딩은 실행 진입점의 책임이다.

```python
from solo_leveling.application.translation.service import TranslationService
from solo_leveling.infrastructure.translation.openai_provider import (
    OpenAITranslationConfig, OpenAITranslationProvider,
)
from solo_leveling.workers.ingestion import register_and_ingest

config = OpenAITranslationConfig.from_env()
service = TranslationService(OpenAITranslationProvider(config))

# 아래 호출은 허용된 경우 원문 청크를 외부로 보내며 API 비용이 발생한다.
result = register_and_ingest(
    db_path, chroma_dir, pdf_path,
    translation_service=service,
    translation_settings=config.translation_settings,
)
```

실제로 호출하는 모델·제공자·프롬프트 버전과 서비스가 리비전에 기록하는 설정이 다르면
호출 전에 ValueError로 거부한다. 원문 외의 파일 경로·청크 ID·DB 식별자는 보내지 않는다.
외부 호출을 막으려면 ALLOW_EXTERNAL_API=false 또는 LOCAL_ONLY=true를 사용한다.

## 번역 요청과 실패 처리

- 구조화 응답은 `{"text": "한국어 번역문"}` 하나다. 원문·한국어 언어 설정을 데이터로 전달한다.
- 요약·해설 추가를 금지하고 수치·단위·수식·부정 표현·실험 조건 보존을 지시한다.
- 원문 안의 명령은 실행하지 않고 번역할 자료로 취급하도록 지시한다.
- 정상 완료된 단일 응답만 사용한다. 거절·잘린 응답·형식 오류·HTTP 오류·timeout은 실패다.
- 제공자 장애는 기존 `provider_unavailable`, 정상 JSON의 빈 번역은 `empty_translation`으로 기록한다.
- 실패한 원문을 번역문으로 복사하지 않는다. 수식처럼 동일하게 유지되는 정상 번역은 허용한다.
- 제공자 설정 불일치 같은 프로그래밍/설정 오류는 배치 실패 코드로 숨기지 않고 예외로 전달한다.
- 연결 timeout은 5초, 읽기 timeout은 설정값이다. 전체 작업 기한은 별도로 구현하지 않았다.
- 제공자는 한 번만 호출하며 서비스가 일시적 연결·timeout·확인된 호출 제한·일부 서버 오류만
  최초 호출 포함 최대 3회 시도한다. 인증·권한·사용량 소진·불명확한 오류·출력 오류는 반복하지 않는다.
  상세 정책과 안전한 진단은 [복구 계획](translation-failure-recovery-plan.md)의 구현 현황을 참조한다.
  전역 장애에도 기존 서비스는 나머지 청크를 계속 처리하므로
  큰 논문을 실행하기 전에 소량 호출로 인증·한도를 확인해야 한다. 배치 중단 정책은 후속 과제다.
- store=false로 요청하지만 이는 제공자의 모든 보관·로그 정책을 없앤다는 뜻은 아니다.
- 기존 성공 번역 보존·부분 번역 색인·최초 설정으로 미게시 실패 재시도 정책은 유지한다.

## 실제 호출은 별도 실행

```powershell
.\.venv\Scripts\python.exe scripts/check_translation.py --live
```

이 명령과 외부 호출 허용 설정이 모두 있어야 합성 영문 청크 하나를 실제 번역한다.
비용이 발생할 수 있다. DB 저장·PDF 파싱·색인은 수행하지 않는다.
사용자 논문이 아니라 수치·부정 표현을 포함한 고정 합성 문장을 사용한다.

현재 자동 테스트는 HTTP를 대체하고 요청 계약·실패·리비전 일치 및 실제 PDF·SQLite·Chroma
연결을 검사한다. 임베딩 계산도 고정 벡터로 대체한다. 실제 번역 품질·수치 보존·누락·환각은
자동 보장하지 않으며 소량 실제 호출과 사람이 읽는 평가가 다음 단계다.
