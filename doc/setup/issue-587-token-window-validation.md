# #587 토큰 분할 기반과 잘림 차단 검증

## 이번 변경

- A: 기존 청크·번역·게시·근거 ID와 DB schema는 유지한다. 하위 구간은 새 embedding set, 부모 청크, 분할 정책, 토큰 구간 및 토큰 내용으로 결정적 ID를 만든다.
- B: 고정 tokenizer로 truncation 없이 계수한다. 기본 payload 112 / overlap 24 / stride 88로 마지막 토큰까지 포함한다. 특수 토큰도 모델 한도에 포함한다.
- C: 원격 처리와 질문에서 입력이 모델 한도를 넘으면 encode 전에 고정 오류로 거부한다. 본문이나 tokenizer 예외는 오류 메시지에 포함하지 않는다.

## 자동 검증

모델·네트워크를 차단한 단위 테스트에서 128 한도 경계, 248 토큰 전체 보존, 끝 구간 중복 방지, 정책·set 변경 시 fragment ID 분리, 잘못된 tokenizer 결과 거부, 긴 질문·번역의 encode 미호출을 확인한다.

```sh
pytest tests/unit/test_embedding_token_windows.py tests/unit/test_processing_embedder.py tests/unit/test_processing_composition.py tests/unit/test_remote_query_runtime.py -q
```

## 남은 범위

분할 함수는 아직 제품 worker에 연결하지 않았다. 현재 저장·게시 계약이 청크당 벡터 1개이므로 이 단계에서는 긴 입력 작업을 명시적으로 실패시킨다. 길이가 긴 문서를 처리할 수 있게 된 것은 아니다.

후속 단계에서 token ID를 재해독·재토큰화하지 않고 인코딩하는 경로, fragment 저장·fingerprint·부모별 검색 집계, 전체 매핑 확인 후 새 set 원자적 게시, 과거 context 보존 및 로컬 재색인을 연결해야 한다. 실제 모델 품질·성능 비교와 번역 실패 재시도 검증도 남아 있다. #587을 닫지 않는다.

실제 staging 변경·PDF 전송·유료 API 호출은 수행하지 않는다.
