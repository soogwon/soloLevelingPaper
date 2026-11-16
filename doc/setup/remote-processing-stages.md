# #517 B의 파싱·번역·임베딩 처리 단계

기준 main: `f3a9d26`. 원격 처리 worker 전체가 아닌 저장·게시 전 메모리 처리 기반이다.
큐·R2·PostgreSQL·게시·MCP·배포 설정과 기존 로컬 등록 worker는 변경하지 않는다.
새 모듈을 제품 runtime에 자동 연결하거나 worker를 시작하지 않는다. #517은 계속 열어둔다.

## 처리 경계

```text
A가 소유권·업로드·작업·실행 세대를 검증하고 고정 입력을 공급
  → ProcessingSource + PDF bytes + 명시적 설정/제한
  → 해시·크기·헤더 검증
  → 기존 PDF 추출·청킹 → 원문/물리 페이지/섹션 보존
  → 기존 번역 서비스 + 전송별 gate + usage 관측
      ├─ 개별 번역 실패 → translation_incomplete (벡터 없음)
      ├─ unknown/예산/동의 거부 → 예외로 중단
      └─ 전체 번역 성공 → 주입 임베더 → 지문·벡터 검사
  → 마지막 실행 허용 확인 → prepared (미게시 처리 결과)
  → A가 lease·deadline·삭제·동의·출처를 다시 검증한 뒤 저장/게시
```

`prepared`와 `translation_incomplete`는 내부 처리 상태다. 외부 작업의 ready/completed나
검색 가능 상태·게시 성공을 뜻하지 않는다. 결과를 만드는 동안 DB·벡터 저장소에 접근하지 않는다.

## 구성 파일

| 파일 | 역할 |
|---|---|
| `domain/remote_processing.py` | 고정 source·제한·임베딩 설정과 불변 청크/번역/벡터/결과 모델 |
| `application/remote_processing.py` | 입력/출처 연결 검사, 단계 순서, 실행 허용 확인, 결과 검증 |
| `infrastructure/parsing/processing_parser.py` | PDF bytes를 기존 추출기·페이지/섹션 청커에 연결 |
| `infrastructure/translation/processing_translation.py` | 기존 TranslationService와 GatedResponsesTransport를 필수 연결 |
| `tests/unit/test_remote_processing_stages.py` | 합성 PDF·제품 번역/gate·대역 HTTP/임베더 회귀 |

위 파일 경로는 `src/solo_leveling/` 기준이며 테스트 파일은 저장소 루트 기준이다.
모두 B 내부 포트이며 A의 저장 스키마나 C의 공개 입력/응답 계약을 확정한 것이 아니다.

## 입력과 파싱

- `ProcessingSource`는 owner/job/paper/version 및 새 parse/translation/embedding 식별자,
  PDF SHA-256·크기를 받는다. **문자열 ID 자체는 소유권·lease·동의의 증명이 아니다.**
  실제 공급자는 A의 인증·작업 검증 경로여야 한다. 기존 publication/context ID를 최신본으로 대체하지 않는다.
- 사용자 경로·임의 URL·R2 key·사용자 API 키는 이 처리기의 입력이 아니다. 검증된 동일 PDF의
  불변 bytes만 받으며 기대 크기·SHA-256 불일치는 파싱 전에 거부한다.
- 최대 바이트·페이지·청크 수는 `ProcessingLimits`로 필수 주입하며 운영 기본값을 만들지 않는다.
  바이트 제한은 파싱 전, 페이지/청크 제한은 파싱 후 적용된다. PDF bytes를 읽는 메모리 제한이나
  악성 PDF의 파서 CPU/메모리 제한·강제 중단을 대체하지 않는다. 해당 격리는 A worker 범위다.
- 페이지를 합치지 않고 기존 추출/청킹 규칙을 사용한다. 인쇄 페이지는 추측하지 않는다.
  빈 페이지는 `empty_pages`로 기록하고 전체 무텍스트 문서는 번역 전에 거부한다.
- 파싱 리비전·청크 순서·중복 ID·물리 페이지·섹션 참조/범위/순번을 확인한다.
  원문·섹션 모델은 메모리 결과에 보존한다.
- 같은 고정 리비전·PDF·파싱 설정의 반복 실행에서 청크 ID를 유지하기 위해
  `UUID5(NAMESPACE_URL, remote-parse:<parse_revision_id>:<chunk_index>)`를 사용한다.
  본문이 같아도 다른 청크는 다른 ID이며 다른 파싱 리비전과도 구분한다.
  파서 알고리즘·청킹 설정·PDF가 바뀌면 A 조립 계층에서 **새 파싱 리비전**을 공급해야 한다.
  이 ID 규칙의 실제 원격 저장 연결은 A 검토가 필요하다.
- 제품 parser의 청킹 설정은 불변이다. 처리기는 실행 시작 시 설정/제한과 adapter·checkpoint
  참조를 고정한다. 실행 중 객체 재설정으로 현재 작업의 L2·출처·페이지 제한을 바꾸지 않는다.
  이는 메모리 실행 경계이며 A의 영속 정책 snapshot·재claim 복구를 구현한 것은 아니다.

## 번역·전송과 중단

- 서버 조립 계층이 생성한 `OpenAITranslationProvider`와 기존 `TranslationService`를 주입한다.
  서비스 키는 provider 설정에만 있으며 처리 source·결과·기본 로그에 넣지 않는다.
  사용자 개인 키를 읽는 `.env`나 로컬 runtime을 이 모듈에서 자동 사용하지 않는다.
- `GatedProcessingTranslation`은 GatedResponsesTransport/ProviderDispatch가 없으면 거부한다.
  실행 시 owner·번역 설정·외부 호출 허용 조건을 재확인하고 direct HTTP로 조용히 대체하지 않는다.
  원본 provider/transport가 도중에 재설정돼도 이 호출의 dispatch·HTTP 함수·설정은 유지한다.
  원래 dispatch 자체의 intent나 원장이 바뀌면 새 예약/전송 전에 중단하며 unknown 차단 상태를
  초기화하기 위해 새 ProviderDispatch를 만들지 않는다.
- 전송 준비 포트는 다음 인자를 받는다.

  ```text
  prepare_attempt(source, actual_dispatch_intent, translation_request, attempt_number, stage, payload, payload_sha256)
      → PreparedProviderAttempt(영속 dispatch_key, 동일 payload_sha256, 안전한 입력 토큰 상한)
  ```

  준비 포트에는 실제 사용할 고정 dispatch intent도 전달한다. source/job과 다른 실행의
  lease·동의·예산을 실수로 연결하지 않도록 A의 신뢰 경로에서 대조해야 한다.
  준비 포트는 source/job과 실제 원장 실행·세대의 신뢰 연결, 리비전/청크/시도·고정 payload와
  영속 key·토큰 상한의 결합을 책임진다. 처리기는 임의 key·추정 토큰 상한을 만들지 않는다.
  서로 다른 청크의 본문이 같다는 이유로 전송 의도를 합치지 않는다. 시도 순번은 해당 호출의
  청크별 순번일 뿐 영속 실행 번호가 아니다. 재시작 시 동일 시도 복구·중복 차단은 원장이 보장해야 한다.
  이 대조는 실제 authorize 트랜잭션의 owner/job/실행 세대·lease·동의 검사를 대체하지 않는다.
- 기존 서비스의 제한 재시도·한국어 품질 검사는 유지한다. 각 청크·실제 재시도 앞에서 checkpoint를
  확인하며 실제 전송은 매번 원장 gate를 거친다. timeout·usage unknown·예산/동의/원장 오류를
  재시도 가능한 번역 provider 오류로 변환하지 않는다. 늦은 usage 정산은 이 모듈이 수행하지 않는다.
- 일반적인 개별 번역 실패는 원문 대신 한국어를 꾸며 넣지 않고 기존 실패 코드·text=None을
  보존한다. 부분 실패 결과는 반환하되 이 첫 구현에서는 성공분만 임베딩하거나 부분 게시하지 않는다.
  이미 성공한 일부 번역을 장기 저장/재사용하는 정책은 A의 staging 저장과 함께 연결해야 한다.
- source/원문/순서/번역 설정·리비전이 결과와 맞는지 임베딩 전에 확인한다.

## 임베딩·실행 허용

- 실제 모델 생성·자동 다운로드·기본 임베더 호출은 없다. `ProcessingEmbedder.embed(items, spec)`를
  주입하고 반환한 chunk ID 순서·개수·지문 및 벡터 개수·차원·유한성·0벡터·정규화를 검사한다.
  벡터는 Python int/float의 불변 tuple이며 bool·비유한·과도한 정수는 거부한다.
- `EmbeddingSpec`는 모델/tokenizer revision·pooling·정규화·최대 길이·차원·cosine·정밀도·
  runtime/version·산출물 해시를 명시한다. 첫 단계는 PyTorch FP32만 허용한다. ONNX로 자동 전환하지 않는다.
  L2이면 norm 오차 1e-5를 검사하며 none이면 L2라고 표시하지 않는다.
- 반환 지문 일치는 **설정 선언의 일치**다. 실제 모델이 선언된 산출물과 일치하는지·tokenizer/
  pooling/정규화가 실제 실행됐는지는 신뢰하는 임베더 adapter와 A 런타임에서 확인해야 한다.
  기존 `embed_texts` 기본 호출이 #391의 고정 L2 snapshot과 같다고 가정하지 않는다.
- `check_allowed(source, stage)`는 parse/translation/embedding/result 및 각 번역 시도에
  호출하며 정확한 True만 허용한다. 거부는 `PROCESSING_NOT_ALLOWED`, 확인 실패는
  `PROCESSING_CHECK_UNAVAILABLE`로 중단한다. 구현이 없으면 기본 허용하지 않는다.
- checkpoint는 협력적 중단 장치다. 확인 직후 철회·삭제·lease 상실 경합, 이미 실행 중인
  파서/임베더 강제 중단이나 저장/게시의 원자 권한을 보장하지 않는다. 실제 provider 원장과
  A의 최종 CAS가 필요하다. 부분 결과를 반환할 때도 마지막 checkpoint를 확인한다.
  Protocol과 callback은 신뢰하는 서버 조립 포트다. 임의 translator·임베더나 항상 True를
  반환하는 대역을 주입한 것만으로 실제 보안·작업 소유권 검증이 성립하지 않는다.
- 외부 예외는 본문 없는 고정 단계 오류로 바꾸고 DispatchError의 제한/unknown 구분은 유지한다.
  이 오류명은 내부 코드이며 C의 공개 wire code로 확정하지 않는다.

## 남은 A·C 연결

1. A가 R2 객체·업로드 의도·owner/job·PDF 크기/해시를 검증하고 bytes와 새 리비전 ID를 공급한다.
2. A가 실제 processing job의 lease/세대·동의/고지·deadline/tombstone을 checkpoint와
   authorize에 연결한다. 현재 remote execution 저장 경로를 full_processing job에 임의로
   대응시키거나 가짜 execution을 만들지 않는다. #395의 미완성 포트도 우회하지 않는다.
3. A+B가 처리용 `prepare_attempt`의 영속 dispatch key·입력 상한·재시작/정산 계약과
   단계 진행 상태·성공 번역 staging 저장/재사용을 연결한다.
4. A가 미게시 결과의 source/지문·전체 게시 집합·현재 lease/deadline/삭제/동의를 다시 검사해
   저장·원자 게시한다. `prepared`만 보고 기존 active pointer를 바꾸지 않는다.
5. 실제 PyTorch 임베더 adapter·고정 artifact·모델 준비·파서 자원 격리·worker 이미지 의존성을
   A+B가 연결한다. 현재 서버 패키지/배포 이미지는 수정하지 않았다.
6. C가 고정 외부 상태·제한 안내·접수/조회·사용자 동의를 연결한다. 이 문서로 공개 도구를 추가하지 않는다.

실제 PostgreSQL/R2·프로세스 재시작·동시 worker·late usage·staging/호스트 종단 검증은 남는다.
대역 gate의 메모리 상태를 영속 원장 증거로 해석하지 않는다. 기존 context/메모/근거의
불변 검증은 실제 게시 연결 후에도 별도로 수행해야 한다.

## 오프라인 테스트

```powershell
New-Item -ItemType Directory -Force runtime
python -X utf8 -m pytest tests/unit/test_remote_processing_stages.py tests/unit/test_pdf_extractor.py tests/unit/test_pdf_extractor_errors.py tests/unit/test_chunker.py tests/unit/translation tests/unit/test_gated_responses.py tests/unit/test_remote_input_dispatch.py tests/unit/test_execution_budget.py -q --basetemp=runtime/pytest-517-unit
python -X utf8 -m pytest tests/integration/test_ingestion.py tests/integration/test_ingestion_process_injection.py tests/integration/test_openai_translation_flow.py -q --basetemp=runtime/pytest-517-local
```

새 처리 테스트는 네트워크와 torch/sentence-transformers/transformers/ORT import를 직접 차단한다.
실제 합성 PDF 추출·페이지/섹션 청킹과 제품 번역/전송 gate를 사용하며 HTTP·원장·임베더는
대역이다. 테스트용 합성 SQLite/벡터 파일의 바이트 불변성도 확인하며 원본 데이터는 사용하지 않는다.
기존 로컬 통합 테스트는 임시 SQLite·실제 Chroma와 합성 HTTP·임베딩으로 실행한다.

### Windows 검증 결과 (2026-11-16)

- 새 처리 테스트 **89개**를 포함한 기존 파싱·청킹·번역·gate·실행 예산 회귀:
  **294 passed, 13 subtests passed**.
- 기존 로컬 등록·주입 임베더·OpenAI 번역 연결 통합: **30 passed**.
- Ruff와 새 파일 공백 검사 통과. 기존 추적 파일의 변경은 없으며 A의 큐·DB·게시 코드를 수정하지 않았다.
- 최초 통합 실행은 Windows 하드 링크 생성 권한으로 3개 실패했다. 필요한 파일 권한으로
  같은 합성 통합 테스트 전체를 재실행해 30개가 통과했다. 제품 코드를 우회하거나 검사를 생략하지 않았다.
- 실제 API·실제 모델·원본 DB/PDF/벡터 저장소는 사용하지 않았다. 실제 원격 job/원장·
  PostgreSQL/R2·동시성·프로세스 재시작·원자 게시·Desktop 검증은 위 남은 범위로 유지한다.

재검토에서 공유 transport 교체와 L2 설정 변경에 따른 검사 우회를 합성 입력으로 재현했다.
실행 중 참조 고정과 dispatch identity 변경 거부로 보완한 뒤 같은 회귀가 통과했다.
다른 job/intent 연결의 준비 거부, 준비 중 철회, checkpoint 교체 거부와
성공·부분 실패·unknown·중단·임베딩 오류 후 기존 합성 DB/벡터의 바이트 불변도 검증했다.
