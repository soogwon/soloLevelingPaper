# #391 후보 실측 전 사람 검토·기준 동결 기록

현재 점검 main: `5edb42c` (#521 병합 포함). 상태: **라벨·FP32 허용값 승인 반영 / 후보 실측 미실행 / 환경 최종 동결 대기**.
라벨 최초 검토 main은 `4e9be39`이며 과거 기록을 보존한다.
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

## 승인과 사전 동결 상태

[A의 FP32 승인](https://github.com/soogwon/soloLevelingPaper/issues/391#issuecomment-6918803245)을 반영했다. 수치·단위·집계와 별도 운영 제안은
[허용값 문서](threshold-proposal.md), 기계 판독 가능한 기록은 [approved-policy.json](approved-policy.json)에 있다.
자료·정책·구현 파일 해시와 관측 환경은 [사전 점검 기록](approved-preflight-20261116.md)에 기록했다.

| 대상 | 현재 상태 |
|---|---|
| 라벨·대안 OR / 연결 AND·범위·k | reviewed 자료 그대로, canonical/raw 해시 동결 |
| FP32 오차·회수·입력 관측·RSS·warm | A 승인 반영, 정책 해시 동결 |
| 로컬 Python·관련 패키지·OS·CPU | 읽기 전용 관측과 환경 지문 기록. 완성된 실행 환경의 승인 해시는 아님 |
| 실제 ONNX·외부 weight·exporter/opset | 지정 snapshot에 ONNX 없음. manifest 확인·최종 환경 동결 대기 |
| CPU provider·intra/inter-op·실행 모드 | 코드의 의도만 정적 확인. 실제 세션은 만들지 않아 미확인 |
| 실제 worker 예산·동시성·제한 시간 | A/Soo 운영 결정 대기. 로컬 비교 승인과 별도 |

승인 일시는 연결 도구에서 제공되지 않아 임의로 기입하지 않았다. 승인 근거는 위 댓글 ID로 식별한다.
문서 작성·관측 시각은 승인 시각이 아니다. 후보 결과를 보거나 모델을 실행하지 않았다.
기존 `decision=pending_thresholds`는 자동 판정 연결 미구현 때문에 그대로다. 승인 취소를 뜻하지 않는다.

## 다음 순서

1. 고정 ONNX 산출물과 필요한 측정 의존성을 준비한 뒤 새 환경/manifest 해시를 추가 기록한다. 기존 관측 기록은 보존한다.
2. exporter·opset·FP32·외부 weight·tokenizer와 실제 ORT CPU provider·스레드·실행 모드를 대조한다.
3. 실행 승인 후 공통 v2의 PyTorch 6회×warm 3회와 ONNX 6회×warm 3회를 교대한다.
   순서는 `(PyTorch→ONNX), (ONNX→PyTorch)`를 3번 반복해 총 12 process다.
4. 기준선 그룹 충족 안정성부터 확인하고, 불안정하면 비교 보류한다. 원시 실패를 삭제하거나 임의 재측정하지 않는다.
5. 승인 기준으로 비교 보고서를 작성한다. 기존 context/publication/set은 유지하고 새 embedding_set_id는 별도 검증한다.

#391·ADR25 미결 3은 열어둔다. 현재 작업은 실제 캡처·제품 적용·운영 배포 승인이 아니다.
