# #517 Mac 실제 모델·PDF 구성요소 검증

검증일: 2026-11-17 (Asia/Seoul). 기준 main eb2077b에 pooling 호환성 수정 적용.
사용자가 별도 검증 환경의 패키지·고정 모델 다운로드와 합성 문장 로컬 추론을 승인했다.
Docker 설치와 Linux 실제 검증은 사용자의 요청에 따라 보류했다.

## 실제 발견과 보완

sentence-transformers 6.1.0의 Pooling.get_config_dict()는
`embedding_dimension=384, pooling_mode="mean", include_prompt=true`를 반환한다.
기존 제품 어댑터는 word_embedding_dimension과 pooling_mode_mean_tokens 등 구형 boolean 형식만
허용하여 정상 고정 모델을 PROCESSING_EMBEDDING_RUNTIME_MISMATCH로 거부했다.

신형 단일 mean과 기존 boolean mean 형식을 모두 검증한다. 다른 mode·복수 mode·잘못된 차원·
신/구 필드 혼합·잘못된 include_prompt 타입은 encode 전에 거부한다.
artifact·전체 runtime 지문·CPU/FP32·벡터 검사·빈 prompt 명시는 유지했다.
이 문제는 Mac 전용으로 단정하지 않으며 라이브러리 설정 표현 호환성 문제다.

## 관측 결과

- macOS 26.7.1 / ARM64 / Python 3.12.14, 별도 venv. 기존 프로젝트 환경은 변경하지 않았다.
- torch 2.14.0, sentence-transformers 6.1.0, transformers 5.17.0,
  tokenizers 0.23.3, numpy 2.5.5, pdfplumber 0.11.10. pip check 통과.
- 고정 모델 revision e8f8c211226b894fcb81acc59f3b34ba3efd5f42.
  model-lock의 파일 10개 크기·SHA-256 및 전체 artifact digest 일치.
- 실제 합성 PDF 2페이지→청크 2개, 수동 합성 한국어 문장→실제 모델 벡터 2개.
- CPU/FP32·384차원·L2, 모든 수치 유한. norm 0.9999999801~1.0000000058.
- 모델을 다시 로드한 두 번째 실행과 최대 절대 차이 0.
- 첫 로딩 포함 encode 2.8386초, 다시 로딩한 encode 1.0081초, 전체 4.6752초.
  프로세스 최대 RSS 1514.0 MiB. 단일 소규모 실행 관측값이며 운영 용량·성능 기준이 아니다.
- 잘못된 artifact hash는 PROCESSING_EMBEDDING_ARTIFACT_MISMATCH로 거부.
- 다운로드 이후 검증 프로세스는 HF offline 및 socket 연결 차단. 네트워크 시도 0, 유료 API 0회.

결과 JSON과 설치 버전 관측 목록은 evaluation/embedding/macos-worker-20261117.*에 기록했다.
패키지 목록은 Mac 검증 관측값이며 Linux 배포용 의존성 lock이 아니다.

## 재현

승인 후 고정 모델과 의존성을 먼저 준비해야 한다. 이 스크립트는 다운로드하지 않는다.

```sh
PYTHONPATH=src:. python evaluation/embedding/macos_worker_smoke.py \
  --model-dir /absolute/path/e8f8c211226b894fcb81acc59f3b34ba3efd5f42 \
  --output /absolute/path/macos-result.json
```

합성 PDF와 수동 번역만 사용한다. 실제 논문이나 질문 파일은 읽지 않는다.
RSS 단위는 macOS 기준이므로 Linux 실측에 이 결과/명칭을 재사용하지 않는다.

관련 어댑터·처리 단계·조립 회귀 테스트 194개와 Ruff 검사를 통과했다.

## 남은 범위

A: 실제 DB/R2·큐·동의·사용량·원자 게시까지 연결한 종단검증, 운영 자원/재시작 검증.
B: 실제 번역 provider, 실제 논문 품질, #391의 고정 평가셋 PyTorch/ONNX 비교.
C: 제품 웹/원격 질문/근거 확인과 두 계정 격리 종단검증.

Linux 실제 모델/배포 호환 검증은 보류 상태이며 Mac 통과로 대체하지 않는다.
실제 PDF 전송·유료 API 호출은 별도 구체적 승인이 필요하다. #517/#391은 닫지 않는다.
