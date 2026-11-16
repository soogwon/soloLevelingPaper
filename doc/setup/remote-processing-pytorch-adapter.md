# #517 B 고정 PyTorch adapter 준비

기준 main: `71d70a8`. 브랜치: `soogwon/feat/issue-517-pytorch-adapter`.
#525의 처리 단계가 받는 `ProcessingEmbedder`에 연결할 구현을 추가했다.
#531 공개 분류 PR과 독립적이며 A의 큐·DB·원장·게시·배포 코드는 바꾸지 않는다.
현재 실제 worker/HTTP 조립에는 연결하지 않았다.

## 처리 계약

- 서버 설정의 **로컬 고정 snapshot Path와 EmbeddingSpec**만 받는다. 사용자 PDF에서 모델 경로·지문을 만들지 않는다.
- snapshot 디렉터리 이름은 40자리 revision이고 모델/tokenizer revision이 일치해야 한다.
  model_id와 tokenizer_id도 같아야 한다. 이 이름만으로 모델 신원을 인증하지 않으며 A가 승인한 파일 해시와 함께 검증한다.
- config·modules·tokenizer·pooling 설정과 실제 safetensors 파일이 있어야 한다. #391의 `artifact_digest`와
  같은 파일 선택·정렬 JSON·SHA-256으로 로딩 전·로딩 후 인코딩 직전·인코딩 후 전체 지문을 대조한다.
  디렉터리 심볼릭 링크/Windows junction은 loader가 해시 열거에서 빠진 파일을 읽을 수 있어 거부한다.
  HF blob 파일 링크는 파일 내용 해시에 포함한다. 지원되는 일반 디렉터리의 지문 계산 규칙은 #391과 같다.
  전체 파일을 신뢰하는 서버가 배포·읽기 전용 관리해야 하며 해시 검사는 파일 교체 경쟁의 원자적 방어가 아니다.
- 기존 전역 모델 캐시나 `embed_texts` 기본값을 사용하지 않는다. 기본 loader는 backend=torch·CPU·local_files_only=True·
  trust_remote_code=False이며 자동 다운로드·ONNX 전환·원격 코드 허용을 하지 않는다.
- loader 호출은 `embed` 시점뿐이다. 테스트에서는 loader/모델을 대역으로 주입한다.
  주입 factory는 신뢰하는 내부 테스트/조립 포트이며 임의 factory로 모델 신원을 증명할 수 없다.
- 로딩 전에 modules.json의 중복 키를 거부하고, 기본 Transformer/Pooling의 고정 두 항목·경로·인덱스만
  허용한다. trust_remote_code=False만으로 사용자 정의 module/path가 모두 차단된다고 가정하지 않는다.
- torch/sentence-transformers/transformers 버전은 #391 방식의 runtime_version 문자열로 대조한다.
  설치 metadata와 실제 로딩된 모듈의 __version__을 별도로 읽어 로딩 후·인코딩 후 비교한다.
  누락·불일치·빌드 접미사 차이는 조용히 정규화하지 않고 거부하며 A가 실제 환경에서 형식을 검증해야 한다.
  `EmbeddingSpec.precision`은 기존 #525 계약의 `float32`를 사용한다. 캡처의 `torch.float32` 문자열과
  자동으로 혼합하지 않으며 A가 저장 지문 조립에서 형식을 확인해야 한다.
- 인코딩 전·후 backend=torch, CPU 장치·모든 parameter의 torch.float32/CPU,
  module 수 2개와 두 번째 module의 pooling 설정, 최대 길이·차원·mean pooling·숨은 기본 prompt 부재를 확인한다.
  module 클래스·토크나이저·가중치 로딩의 진위는 승인 artifact와 신뢰하는 loader의 실제 설치본 검증도 필요하다.
  pooling 지문은 현재 B의 `mean` 표기 또는 실제 설정 dict의 정렬 JSON과 대조한다.
- encode에 빈 prompt를 명시해 기본 prompt/기본 encode 설정이 번역 입력에 숨은 접두사를 붙이지 못하게 한다.
- L2·cosine·PyTorch FP32만 받으며 batch는 명시한다. 번역 실패·누락·중복 ID는 로딩 전에 거부한다.
- ID 순서를 보존하고 ndarray float32·개수·차원·유한성·0벡터·L2를 검사한 불변 EmbeddingBatch를 반환한다.
  기존 처리 단계의 결과 검사는 그대로 유지한다. 외부 예외 본문은 고정 ProcessingError로 치환한다.

## 중단·usage 확인 포트

adapter 생성에는 `check_allowed: Callable[[], bool]`가 **필수**다. 공개 MCP/HTTP 입력이 아닌
서버 내부 조립 포트이며, 같은 owner/job/attempt의 현재 lease·동의·삭제·취소·미정산 usage 등을
A가 확인하는 closure를 연결해야 한다. 실제 조회·원장 변경은 이번 구현에 없다.

- 파일 검사 전, 모델 로딩 직전·직후, 인코딩 직전·직후, 최종 결과 반환 전에 정확한 True를 요구한다.
- False/None/문자열·숫자는 PROCESSING_NOT_ALLOWED, 확인 예외는 본문 없이 PROCESSING_CHECK_UNAVAILABLE로 중단한다.
- 따라서 해시 계산 중 취소·미정산 usage가 확인되면 factory를 호출하지 않는다. factory 실행 중 중단은
  이미 시작한 로딩을 되돌리지 못하지만 이후 encode를 막는다. encode 중 중단은 반환 결과를 폐기한다.
- callback 거부만으로 usage_pending·lease 만료·취소 완료 등 공개 사유를 확정하지 않는다.
  A가 원장 상태와 #531의 미정산 우선순위로 공개 코드를 결정한다.
- 이 검사는 협력적 중단이다. 확인 직후의 경합·이미 실행 중인 모델 강제 중단·게시 CAS를 대신하지 않는다.
  공유 전역 True callback이나 다른 작업에 묶인 callback을 연결하면 실제 권한 검증이 성립하지 않는다.

## 합성 검증 범위

실제 API·모델 로딩·추론·export·다운로드 없이 임시 합성 파일과 모델/factory 대역만 사용한다.
socket 연결과 실제 모델 라이브러리 import를 차단한다. 기본 loader 옵션 검사는 import 자체를 합성 모듈로 대체한다.
기존 실제 파서·번역 gate/합성 HTTP·메모리 원장과 adapter를 연결한 prepared/부분 실패/중단/unknown도 확인한다.

```powershell
python -X utf8 -m pytest tests/unit/test_processing_embedder.py tests/unit/test_remote_processing_stages.py -q --basetemp=runtime/pytest-517-adapter
ruff check src/solo_leveling/infrastructure/embeddings/processing_embedder.py tests/unit/test_processing_embedder.py
```

이 검증은 실제 모델의 라이브러리 API 호환·파일 로딩·FP32 정확도·RSS·시간 측정 증거가 아니다.
#391의 실제 PyTorch v2 비교와 모델 준비/실행은 별도 승인 후 해야 한다.

### Windows 합성 결과 — 2026-11-17 재검토

- 최초 45개/전체 회귀 339개 통과 이후 로딩 중 파일 변이가 인코딩 후에야 거부되는 사례를 재현하고 보완했다.
- 신규 adapter 계약 **78개**, 기존 처리 단계 포함 **167 passed**.
- 파싱·청킹·번역·전송 gate·실행 예산 회귀 포함 **372 passed, 13 subtests passed**.
- 파일/버전/설정 불일치의 로딩 전 거부, 인코딩 중 파일·길이·backend·버전 변경 거부,
  출력 차원·비유한·0벡터·L2 거부와 기본 loader의 로컬 CPU/torch 옵션을 검증했다.
- 해시 계산 중 철회·미정산 usage·조회 오류의 factory 미호출, 로딩/인코딩 각 경계의 중단,
  모듈 경로/사용자 정의 코드 거부, 설치 버전과 로딩 버전의 독립 대조도 확인했다.
- 디렉터리 링크/junction 거부는 경로 관측 대역 검사이며 실제 OS 링크 생성·배포 경합 검증은 아니다.
- Ruff·공백·상대 링크 검사 통과. 실제 모델·API 실행과 A의 큐·DB·게시 변경은 없다.

## A가 연결할 때 남는 것

1. 승인된 artifact·필요 의존성을 worker 이미지에 준비하고 실제 loader/get_backend/get_embedding_dimension/
   pooling·tokenizer 경로와 버전을 검증한다. 현재 대역 테스트를 실제 설치본 검증으로 해석하지 않는다.
2. 파서/임베더 자원 격리·시간 제한·강제 중단·CPU 스레드·동시성은 A의 worker 진입점에서 정한다.
   adapter는 모델을 매 embed 호출에서 만들며 전역 캐시·상주 프로세스·세션 수명 정책을 정하지 않는다.
3. gate·usage unknown·동의·lease 중단 뒤 이 adapter를 호출하지 않고, 실행 뒤 최종 권한·게시 CAS를 확인한다.
   필수 check_allowed 포트를 같은 작업의 현재 상태에 연결하고 unknown/조회 실패를 허용으로 취급하지 않는다.
   협력적 체크나 파일 해시로 lease·삭제 경쟁과 원자 게시를 대신하지 않는다.
4. 결과는 prepared일 뿐이다. 기존 embedding set/context/publication을 덮어쓰지 않고
   승인 지문을 갖는 별도 set의 staging 저장·원자 게시·실제 원장 정산을 연결한다.

#517은 실제 연결·실행·종단 검증이 남아 있어 열린 상태로 유지한다.

#531/#532/#534/#535 병합 후 조합 검증과 Python 3.11의 디렉터리 링크 검사 보완은
[worker 경계 합성 검증](remote-processing-worker-boundary-validation.md)에 별도로 기록했다.
