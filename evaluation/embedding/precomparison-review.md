# #391 후보 실측 전 사람 검토·기준 동결 기록

기준 main: `4e9be39`. 상태: **라벨 검토 반영 / 후보 실측 미실행 / 허용값 미확정**.
2026-11-16 사용자가 B 권장안에 따른 라벨·대안군·평가 분류 반영을 요청했다.
`cases.json`은 schema 2, `review_status=reviewed`로 갱신하며 기존 실측 결과는 보존한다.
라벨 검토 요청의 날짜를 수치 허용값 승인이나 전체 기준 동결 시각으로 사용하지 않는다.
모든 문장과 ID는 합성 자료다. 모델·사용자 DB·API를 사용하지 않았다.

## 검토할 자료

- 자료: [cases.json](cases.json), `synthetic-embedding-v3-reviewed`
- `comparison.digest` SHA-256: `6df95d142a81c36308057f0158de718e514ba8e0c8bdb149f206f7e09c0953ef`
- 이전 v2-draft 자료는 `git show 4e9be39:evaluation/embedding/cases.json`으로 확인한다.
  이전 digest는 `1dee0c6f3ce0740cd3b58f1e376898cf832814ec71abfdbf0a2e2c80055134a3`다.
  새 자료에는 기존 캡처를 재사용하지 않으며 digest가 다르면 거부한다.
- 기존 [B 검토 제안](label-review.md), [v1 PyTorch 관측](pytorch-baseline-20261115.md)
- [A가 확인한 진행 순서](https://github.com/soogwon/soloLevelingPaper/issues/391#issuecomment-6894540302)

아래는 채택된 유지/수정 결정이다. 모델 순위를 보고 필수 ID를 바꾸거나 k를 늘리지 않았다.
대안군 채점은 그룹 안 OR / 그룹 사이 AND로 구현했다. 연결을 단일 OR 그룹으로 쓰는 라벨은 거부한다.
개별 ID 순위와 그룹의 최상위 순위 변화를 함께 기록한다. 양쪽 기준선은 다시 캡처해야 한다.

## 근거 원문 대조

각 ID의 정확한 페이지·섹션·owner·리비전은 `cases.json`이 기준이다. 같은 문구라도 범위가
다르면 후보 자격이 없다. 아래 합성 원문은 의미 검토용이며 제품 인용 목록이 아니다.
1·2번의 전달 횟수와 head+tail의 속도 표현을 동일시하지 않아 대안으로 인정하지 않는다.

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
`old-translation`은 조건과 비슷한 문구를 가진 대조군이다. 허용 여부는 질의 범위로 판단한다.
페이지 전체인 11번에서는 outside-page의 99쪽도 허용되며 문구 유사성으로 다른 owner/리비전을 허용하지 않는다.

## 질문 12개와 사람 결정란

모든 질의는 user-a / paper-v1 / parse-1 / ko-1 / published-1에 고정한다.
페이지 전체는 빈 배열이며 최신 publication 선택이라는 뜻이 아니다.

| 번호 / ID | 실제 질문 | 페이지 / 섹션 / k | 현재 필수 ID | 확인할 결정 |
|---|---|---|---|---|
| 1 ko-condition | Lantern relay가 bucket chaining보다 라운드당 전달 횟수가 적은 조건은? | 6,7 / s4 / 3 | condition | 유지. m < k 조건. 순차 라운드 수와 구분. head+tail은 대안 아님 |
| 2 en-condition | When does lantern relay need fewer handoffs per round than bucket chaining? | 6,7 / s4 / 3 | condition | 1번과 같은 명제. 영어라는 이유로 다른 정답 정책을 쓰지 않음 |
| 3 ko-path | 중계 구간 수가 먼 거리 배송 조율에 미치는 영향은? | 6 / s4 / 2 | hops | 구간이 적을수록 조율이 쉬움이라는 방향 |
| 4 parallel-vs-cost | 순차 라운드 수와 총 전달 비용은 같은가? | 6 / s4 / 2 | rounds | 고정 라운드 대 m 라운드와 총 비용의 구분 |
| 5 cross-page | Complete the condition split across pages six and seven. | 6,7 / s4 / 5 | continuation-head AND continuation-tail | 두 조각 모두 필수. connection 별도 집계. condition으로 대체 불가 |
| 6 strict-page | Find only the condition fragment on page six; do not add page seven. | 6 / s4 / 3 | continuation-head | 잘린 조각의 회수 과제. 완성된 조건 답변이나 #108 페이지 보충 평가가 아님 |
| 7 near-scores | Which model scored 81.02? | 8 / s5 / 1 | near-b | B만 정답. 81.01의 A와 구분. 실제 점수 근접성은 후속 관측 |
| 8 korean-source | 가상 검색기는 한국어 질문을 어떤 언어의 근거와 연결하나요? | 3 / s2 / 1 | korean OR multilingual | 질문 명확화. 영어 근거가 정답. 둘 중 하나면 성공 |
| 9 french-query | Quel système relie les questions coréennes aux preuves anglaises? | 3 / s2 / 1 | korean OR multilingual | 같은 명제를 담은 둘 중 하나면 성공 |
| 10 long-tail | 긴 입력의 마지막 실험 종료 조건은? | 9 / s6 / 1 | long | 파란 스위치. truncation_observation 별도 집계. recall=1만으로 끝 구절 이해 판정 불가 |
| 11 owner-and-revision | Find the active lantern relay condition without other users or revisions. | 전체 / s4 / 4 | condition | 고정 출처 유지. 99쪽도 허용. 다른 owner/리비전/게시 집합 제외 |
| 12 section-filter | Find the section four lantern relay condition on page six. | 6 / s4 / 2 | condition | 페이지·섹션을 top_k 전에 적용. 같은 문구의 범위 밖 근거는 정답 아님 |

1·2·3·4·6·7·12는 기존 라벨 유지, 5는 두 조각 필수 및 별도 집계, 8은 질문 명확화와 대안군,
9는 대안군, 10은 관측 분리, 11은 페이지 전체 범위 해석을 반영했다.
retrieval 10개 / connection 1개 / truncation_observation 1개로 집계하며 12개 통합 품질 합격률은 없다.
5번은 허용 청크 수=k=5, 10번은 허용 청크 수=k=1이라 `selection_is_exhaustive=true`다.
문서 회수 성공을 검색 선별력·실제 문장 연결·끝 구절 보존의 증거로 쓰지 않는다.
v1 기준선의 strict-page·near-scores 실패는 보존한다. 자료를 바꾸더라도 과거 실패 기록을 지우지 않는다.

## 후보 결과를 보기 전에 확정할 기준

아래 값은 공란이다. 임의 기본값이나 기존 10/12 결과로 채우지 않는다.
단위 벡터 수치 검사 `1e-5`나 #390의 float32 비교 오차 `1e-6`은 모델 품질 허용값이 아니다.

| 기준 | 사전 결정할 값·집계 방식 | 승인 상태 |
|---|---|---|
| 안전성 | 다른 owner/출처·범위 혼입 0건. 차원·비유한·0벡터·설정 불일치는 기존 즉시 거부 유지 | 기존 방어 유지, 완화 없음 |
| 청크 벡터 | 최대 절대 오차·최대 cosine distance, 정밀도별 상한 | 대기 |
| 질의 벡터 | 청크와 별도 상한. 청크 평균으로 질의 오차를 상쇄하지 않음 | 대기 |
| 필수 근거 | 질의별 그룹 recall@k 최소값·모든 그룹 충족 여부·무퇴행 규칙. 분류별 집계 | 대기 |
| 순위 | 필수 ID 순위 변동·top_k 교집합 허용값. 동점/근접 교환과 필수 근거 탈락을 구분 | 대기 |
| 입력 잘림 | 양쪽 토큰 수·잘림 여부·문서 회수와 정보 보존을 각각 기록. long-tail은 별도 해석 | 대기 |
| cold/warm | 공통 v2 절차·동일 환경·반복 수, 중앙값/최댓값 등 집계 및 개선율/상한 | 대기 |
| 메모리 | 동일 process-tree RSS 방법/주기, worker 실제 상한·여유율·허용 개선율 | A/Soo 확인 대기 |

## 동결 기록 양식 — 아직 승인 기록이 아님

| 항목 | 기록할 내용 |
|---|---|
| 검토자·승인 일시 | 미기입. 에이전트의 문서 작성 시각을 승인 시각으로 쓰지 않음 |
| 질의별 결정 | 위 라벨 반영 완료. 수치 기준 동결과 구분 |
| 자료 | 확정 버전·전체 digest 및 검토 커밋 SHA |
| 기준 | 위 기준의 모든 값·단위·집계·예외, 기준 문서 digest |
| 캡처 조건 | 모델/tokenizer 고정 revision, runtime/정밀도/manifest, OS·CPU·device·threads·batch·v2 절차 |
| 독립성 | 후보 캡처/수치를 보기 전 결정했는지. 승인 이전 후보 수치를 봤다면 그 사실과 새 평가 계획 기록 |
| 선행 확인 | A의 실제 ORT provider/threads/실행 모드/session 경로·외부 weight manifest 확인 |

수치·환경 등 남은 필수 결정이 승인되기 전에는 candidate를 실행하거나 합격 판정을 추가하지 않는다.
기존 비교기의 `decision=pending_thresholds`는 그대로 유지한다. 이 문서는 자동 판정 gate의
구현이나 승인 진위 검증이 아니며, 승인된 값의 기계 판정 연결은 별도 검토한다.

## 승인 후 순서

1. 라벨·대안군 변경은 반영했다. 남은 수치·환경을 승인한 뒤 자료/기준의 최종 동결 기록을 작성한다.
2. 같은 고정 입력·공통 v2 절차로 PyTorch 기준선을 다시 캡처한다. v1 결과는 보존한다.
3. 같은 환경에서 고정 로컬 ONNX 후보를 캡처하고 실제 session/manifest를 대조한다.
4. 사전 기준으로 질의별 실패를 포함해 비교한다. 환경·절차가 다르면 성능 비교 불가로 유지한다.
5. 통과해도 기존 context/publication/set을 바꾸지 않는다. 원격 연결은 별도 embedding_set_id로 검증한다.

#391·ADR25 미결 3은 열어둔다. 수치 합의, 실제 v2 기준선/ONNX 비교와 제품 적용 판단이 남아 있다.
