# #594 정규 저장·후보 게시·새 색인 선택

Refs #594. staging 설정 변경과 실제 API 호출은 하지 않았다.

## 연결한 흐름

1. 기존 단발 복구 worker가 만든 `staged_result`를 읽는다. owner/key/input hash, 동의·만료·삭제·기존 active publication을 재검사한다.
2. 복구 결과를 기존 `ProcessingResult` 계약으로 복원한다. 번역 복구는 기존 parse/chunk를 재사용하고 새 translation revision을 만든다. 재파싱은 새 parse/chunk를 사용하며, 번역을 생성하지 않았으므로 `original_only`와 `empty_translation`을 명시한다. 번역 완료라고 표시하지 않는다.
3. 기존 로컬 `PyTorchProcessingEmbedder`에 고정 artifact/model/tokenizer 지문과 token-window 정책을 전달한다. 모델 지문이 기존 검토 snapshot과 다르면 로딩 전에 거부한다. 모델 다운로드나 외부 임베딩 API를 사용하지 않는다.
4. 기존 staging 검증기로 문서·번역·부모 청크/fragment 전체 대응·벡터 차원·유한값·정규화·크기를 검사한다.
5. 한 DB 트랜잭션에서 게시 전용 `embed` 작업의 실행 세대, 정규 revision/chunk/translation/fragment/vector, 후보 publication, 작업 성공, 복구 원장의 publication 연결을 모두 저장한다. 실패하면 전부 rollback한다. 이 작업은 queued로 노출되지 않으며 기존 처리 worker가 소비하지 않는다.
6. 후보 게시 시 active pointer를 바꾸지 않는다. 인증 웹에서 publication ID를 명시한 선택 POST와 CSRF 검증을 거쳐 active pointer 전환과 **새 context 생성**을 함께 커밋한다. 기존 context·메모·질문·과거 근거는 변경하거나 복사하지 않는다. 같은 요청의 재선택은 같은 새 context를 반환한다.

완료 의미: `published`는 검증된 후보 게시본이 존재한다는 뜻이다. `selected_context_id`가 있어야 사용자가 새 색인을 선택한 상태다. 다른 작업이 기본 게시본을 바꿨으면 선택을 충돌로 거부한다. 선택된 context는 이후 다른 색인이 선택되어도 고정된다.

## 단발 실행

```sh
python -m solo_leveling.interfaces.cli.reprocessing_worker --execute --publish
```

기존 `REPROCESSING_WORKER_EXECUTION_ENABLED=true`, 전용 DB URL 및 승인 계획이 필요하다. 계획에 `publication` 객체를 추가한다:

- `model_dir`: 이미 준비된 고정 모델 revision 디렉터리의 절대 경로
- `embedding_spec`: 기존 검토 snapshot의 모델 지문에서 `split_policy`를 제외한 `EmbeddingSpec` 전체 필드
- `fragment_policy`: `payload_tokens`, `overlap_tokens`, `version` (`token-window-v1`)
- `batch_size`: 양의 정수

이 게시 단계는 OpenAI 키·R2 자격을 사용하지 않는다. 파싱/번역 실행과 게시를 별도 명령으로 유지해 실패 시 외부 호출을 반복하지 않는다. 동시 게시가 로컬 임베딩을 중복 계산할 수는 있으나 정규 저장·publication은 한 번만 생성한다. `staged` 상태의 취소/철회는 후보 게시를 차단한다.

## 역할별 검증

- A: 실제 로컬 PostgreSQL에서 후보 게시·선택의 원자성, 동시 요청 멱등성, 중간 저장 실패 rollback, owner/동의/삭제/판본 변경 차단을 확인한다. 기존 세대·ready 불변 트리거는 제거하거나 완화하지 않는다.
- B: 합성 임베딩 대역과 실제 pgvector 저장/검색으로 fragment 전체 대응, 비정상 벡터 거부, 기존 parse 재사용/새 parse 구분과 원문 전용 상태를 확인한다. 기존 게시 포트의 기본 activate=true 동작 회귀도 유지한다.
- C: 조회만으로 선택하지 않고 명시적 버튼에서 POST한다. 새 context 링크, 같은 키 재조회, CSRF/owner 위조 거부, 로그아웃 뒤 늦은 응답 차단을 확인한다.

실제 PyTorch 모델 실측 및 staging/Desktop 검증을 수행한 것으로 간주하지 않는다.

## 남은 적용 범위

- migration 0061 적용 및 전용 역할 권한 확장: worker의 정규 결과·벡터·게시·embed 작업 저장, 웹의 active pointer UPDATE/context INSERT와 복구 원장 선택 기록. 최소 권한 리허설 및 staging 적용은 별도 승인 후 진행한다.
- 이미지/모델 파일·실제 DSN·Render 설정 연결, 실제 R2/복구/임베딩/선택 종단 검증. 실제 번역 API 호출은 전송 본문·호출 수·비용 한도 재승인 후에만 진행한다.
- #595 공통 MCP 작업 상태, 무인 만료 정리, 불명확한 전송 정산 및 실제 두 계정/Desktop 검증.

#594의 실제 원격 종단 완료 기준이 남아 있으므로 티켓은 열어둔다.
