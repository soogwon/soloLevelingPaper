# #587 분할 worker·게시 검증

## 구현

- A: fragment 메타데이터를 포함한 staging payload v3를 추가한다. 기존 v1/v2의 바이트 표현·복구를 유지한다. 부모 집합·set·구간·정책을 저장 전과 게시 전에 검사하며 현재 lease 아래서 벡터·정규 테이블·active pointer를 한 트랜잭션으로 게시한다.
- B: 고정 tokenizer의 토큰 ID를 그대로 모델에 넣는다. decode 후 재토큰화하지 않는다. Transformers의 기존 prepare_for_model과 새 TokenizersBackend의 단일 입력 TemplateProcessing을 지원하며, 모르는 템플릿은 거부한다. 모델·artifact·차원·정규화 확인은 유지한다.
- C: 검색은 부모 chunk ID를 유지한다. 새 set의 fingerprint에 split_policy가 포함되며 과거 context는 기존 set을 사용한다. 기존 설정은 자동으로 분할로 전환하지 않는다.

## 실행 설정

검증 runtime JSON의 schema_version 2에서만 다음 항목을 명시한다. 기존 schema_version 1은 원래 경로를 유지한다.

```json
"embedding_split_policy": {
  "version": "token-window-v1",
  "payload_tokens": 112,
  "overlap_tokens": 24,
  "stride_tokens": 88
}
```

실제 적용 전 migration 0049/0050과 fragment 테이블 권한 승인이 필요하다. worker에는 SELECT·INSERT 및 embedding 열 UPDATE만, 원격 질문 조회 역할에는 SELECT만 추가하는 범위다. 테이블 생성·삭제·역할 전환 권한은 필요하지 않다. 해당 권한은 이번 로컬 테스트의 임시 역할에만 부여했다.

## 로컬 검증

- 대역 모델: 토큰 ID 전달, 입력 한도, 배치별 실행 허용 확인, 잘못된 설정 거부와 기존 처리 회귀.
- 실제 PostgreSQL: 제한 worker 계정에서도 staging 재생·복원→fragment 원자 게시→부모 검색 성공. 누락 구간·다른 set/부모·잘못된 토큰 수·정책은 저장 전 거부.
- 실제 Mac CPU 모델: 기존 로컬 paraphrase-multilingual-MiniLM-L12-v2 revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42` 사용. 네트워크 연결을 차단하고 합성 문장만 처리했다. 680토큰을 8개 fragment로 마지막 680번째 토큰까지 처리, 384차원 L2 단위 벡터를 확인했다. 짧은 합성 문장의 기존 encode 경로와 최대 절대 오차는 0.0이었다.
- 실제 모델 검증에서 새 tokenizer의 prepare_for_model 미지원이 드러나 템플릿 처리 호환성을 보완하고 대역 회귀 테스트를 추가했다.

## 남은 검증

staging 권한·runtime 설정·배포는 적용하지 않았다. 실제 논문 품질·성능 비교, 로컬 기존 색인의 명시적 이관, 번역 본문 실패의 제한 재시도와 최종 원문 폴백은 별도 단계다. 신규 PDF 전송·번역 API·질문 API 호출 없이 검증했으며 #587을 닫지 않는다.
