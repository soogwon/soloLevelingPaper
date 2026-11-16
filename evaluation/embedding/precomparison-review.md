# #391 후보 실측 전 사람 검토·기준 동결 기록

기준 main: `437131e`. 상태: **검토 대기 / 후보 실측 미실행 / 허용값 미확정**.
이 문서는 사람이 승인할 내용을 한곳에서 대조하기 위한 양식이다. B의 검토 제안을 사람 승인으로
대체하지 않으며 `cases.json`의 `review_status=draft`, 라벨, 기존 결과는 변경하지 않는다.
모든 문장과 ID는 합성 자료다. 모델·사용자 DB·API를 사용하지 않았다.

## 검토할 자료

- 자료: [cases.json](cases.json), `synthetic-embedding-v2-draft`
- `comparison.digest` SHA-256: `1dee0c6f3ce0740cd3b58f1e376898cf832814ec71abfdbf0a2e2c80055134a3`
- 기존 [B 검토 제안](label-review.md), [v1 PyTorch 관측](pytorch-baseline-20261115.md)
- [A가 확인한 진행 순서](https://github.com/soogwon/soloLevelingPaper/issues/391#issuecomment-6894540302)

아래 번호와 질의 ID로 유지/수정/보류를 기록한다. 수정은 해당 질의의 의미·범위에 근거해야 하며
모델 순위를 보고 필수 ID를 바꾸거나 k를 늘려서는 안 된다. 다른 정답을 허용하려면 자료·비교기
버전을 올리고 양쪽 기준선을 다시 캡처한다. 아직 대안군 채점은 구현돼 있지 않다.

## 근거 원문 대조

각 ID의 정확한 페이지·섹션·owner·리비전은 `cases.json`이 기준이다. 같은 문구라도 범위가
다르면 후보 자격이 없다. 아래 합성 원문은 의미 검토용이며 제품 인용 목록이 아니다.

| ID | 원문 / 핵심 확인 |
|---|---|
| condition | Lantern relay needs fewer handoffs per round than bucket chaining when convoy length m is smaller than depot count k. |
| hops | Fewer relay hops between depots make distant deliveries easier to coordinate. |
| rounds | Lantern relay needs a fixed number of sequential rounds; bucket chaining needs m sequential rounds. Round count is distinct from total handoff cost. |
| continuation-head | The synthetic method is faster than bucket chaining when the convoy |
| continuation-tail | length m is smaller than depot count k. This condition concerns per-round handoffs. |
| near-a | Model A scored 81.01 on the synthetic evaluation. |
| near-b | Model B scored 81.02 on the synthetic evaluation. |
| korean | 가상 논문의 검색기는 한국어 질문과 영어 원문 근거를 연결한다. |
| multilingual | The synthetic search system connects Korean questions to English evidence. Le système accepte aussi le français. |
| long | 전체 반복 원문은 `cases.json`의 long을 그대로 검토한다. 정답 구절은 문서 끝에 있으며 문서 회수 성공과 잘린 정보 이해를 구분한다. |

`outside-page`, `outside-section`, `other-user`, `old-revision`, `unpublished`, `other-version`,
`old-translation`은 조건과 비슷한 문구를 가진 범위 위반 대조군이다. 문구 일치로 허용하지 않는다.

## 질문 12개와 사람 결정란

모든 질의는 user-a / paper-v1 / parse-1 / ko-1 / published-1에 고정한다.
페이지 전체는 빈 배열이며 최신 publication 선택이라는 뜻이 아니다.

| 번호 / ID | 실제 질문 | 페이지 / 섹션 / k | 현재 필수 ID | 확인할 결정 |
|---|---|---|---|---|
| 1 ko-condition | Lantern relay가 bucket chaining보다 라운드당 전달 횟수가 적은 조건은? | 6,7 / s4 / 3 | condition | m < k 조건. 순차 라운드 수와 구분. head+tail 대안 허용 여부 |
| 2 en-condition | When does lantern relay need fewer handoffs per round than bucket chaining? | 6,7 / s4 / 3 | condition | 1번과 같은 명제. 영어라는 이유로 다른 정답 정책을 쓰지 않음 |
| 3 ko-path | 중계 구간 수가 먼 거리 배송 조율에 미치는 영향은? | 6 / s4 / 2 | hops | 구간이 적을수록 조율이 쉬움이라는 방향 |
| 4 parallel-vs-cost | 순차 라운드 수와 총 전달 비용은 같은가? | 6 / s4 / 2 | rounds | 고정 라운드 대 m 라운드와 총 비용의 구분 |
| 5 cross-page | Complete the condition split across pages six and seven. | 6,7 / s4 / 5 | continuation-head, continuation-tail | 두 조각 모두 회수하는 과제. condition 하나로 라벨을 대체하지 않음 |
| 6 strict-page | Find only the condition fragment on page six; do not add page seven. | 6 / s4 / 3 | continuation-head | 잘린 조각의 회수 과제. 완성된 조건 답변이나 #108 페이지 보충 평가가 아님 |
| 7 near-scores | Which model scored 81.02? | 8 / s5 / 1 | near-b | B만 정답. 81.01의 A와 구분. 실제 점수 근접성은 후속 관측 |
| 8 korean-source | 가상 검색기는 어떤 언어의 질문을 연결하나요? | 3 / s2 / 1 | korean | multilingual도 한국어 질문을 설명하므로 단일 필수 ID의 타당성 확인 |
| 9 french-query | Quel système relie les questions coréennes aux preuves anglaises? | 3 / s2 / 1 | multilingual | korean에도 같은 핵심 명제 존재. 질의 언어만으로 필수 ID를 고정하지 않을지 확인 |
| 10 long-tail | 긴 입력의 마지막 실험 종료 조건은? | 9 / s6 / 1 | long | 파란 스위치. 허용 문서가 하나이므로 recall=1만으로 임베딩 품질·끝 구절 이해를 판정하지 않음 |
| 11 owner-and-revision | Find the active lantern relay condition without other users or revisions. | 전체 / s4 / 4 | condition | active라는 단어와 무관하게 고정 출처 유지. 다른 owner/리비전/게시 집합 제외 |
| 12 section-filter | Find the section four lantern relay condition on page six. | 6 / s4 / 2 | condition | 페이지·섹션을 top_k 전에 적용. 같은 문구의 범위 밖 근거는 정답 아님 |

현재 모든 행의 결정은 **대기**다. 특히 1·2·8·9의 대안 근거와 6·10의 측정 의미를 먼저 확인한다.
v1 기준선의 strict-page·near-scores 실패는 보존한다. 자료를 바꾸더라도 과거 실패 기록을 지우지 않는다.

## 후보 결과를 보기 전에 확정할 기준

아래 값은 공란이다. 임의 기본값이나 기존 10/12 결과로 채우지 않는다.
단위 벡터 수치 검사 `1e-5`나 #390의 float32 비교 오차 `1e-6`은 모델 품질 허용값이 아니다.

| 기준 | 사전 결정할 값·집계 방식 | 승인 상태 |
|---|---|---|
| 안전성 | 다른 owner/출처·범위 혼입 0건. 차원·비유한·0벡터·설정 불일치는 기존 즉시 거부 유지 | 기존 방어 유지, 완화 없음 |
| 청크 벡터 | 최대 절대 오차·최대 cosine distance, 정밀도별 상한 | 대기 |
| 질의 벡터 | 청크와 별도 상한. 청크 평균으로 질의 오차를 상쇄하지 않음 | 대기 |
| 필수 근거 | 질의별 recall@k 최소값·기준선 대비 무퇴행 규칙·전체 집계의 분모 | 대기 |
| 순위 | 필수 ID 순위 변동·top_k 교집합 허용값. 동점/근접 교환과 필수 근거 탈락을 구분 | 대기 |
| 입력 잘림 | 양쪽 토큰 수·잘림 여부·문서 회수와 정보 보존을 각각 기록. long-tail은 별도 해석 | 대기 |
| cold/warm | 공통 v2 절차·동일 환경·반복 수, 중앙값/최댓값 등 집계 및 개선율/상한 | 대기 |
| 메모리 | 동일 process-tree RSS 방법/주기, worker 실제 상한·여유율·허용 개선율 | A/Soo 확인 대기 |

## 동결 기록 양식 — 아직 승인 기록이 아님

| 항목 | 기록할 내용 |
|---|---|
| 검토자·승인 일시 | 미기입. 에이전트의 문서 작성 시각을 승인 시각으로 쓰지 않음 |
| 질의별 결정 | 1~12 각각 유지/수정/보류와 근거. 대안군 채택 여부 |
| 자료 | 확정 버전·전체 digest 및 검토 커밋 SHA |
| 기준 | 위 기준의 모든 값·단위·집계·예외, 기준 문서 digest |
| 캡처 조건 | 모델/tokenizer 고정 revision, runtime/정밀도/manifest, OS·CPU·device·threads·batch·v2 절차 |
| 독립성 | 후보 캡처/수치를 보기 전 결정했는지. 승인 이전 후보 수치를 봤다면 그 사실과 새 평가 계획 기록 |
| 선행 확인 | A의 실제 ORT provider/threads/실행 모드/session 경로·외부 weight manifest 확인 |

모든 필수 결정이 승인되기 전에는 candidate를 실행하거나 합격 판정을 추가하지 않는다.
기존 비교기의 `decision=pending_thresholds`는 그대로 유지한다. 이 문서는 자동 판정 gate의
구현이나 승인 진위 검증이 아니며, 승인된 값의 기계 판정 연결은 별도 검토한다.

## 승인 후 순서

1. 결정에 맞춰 자료/비교기 변경이 필요한지 확인하고 버전·해시를 확정한다.
2. 같은 고정 입력·공통 v2 절차로 PyTorch 기준선을 다시 캡처한다. v1 결과는 보존한다.
3. 같은 환경에서 고정 로컬 ONNX 후보를 캡처하고 실제 session/manifest를 대조한다.
4. 사전 기준으로 질의별 실패를 포함해 비교한다. 환경·절차가 다르면 성능 비교 불가로 유지한다.
5. 통과해도 기존 context/publication/set을 바꾸지 않는다. 원격 연결은 별도 embedding_set_id로 검증한다.

#391·ADR25 미결 3은 열어둔다. 사람 검토, 수치 합의, 실제 v2 기준선/ONNX 비교와 제품 적용 판단이 남아 있다.
