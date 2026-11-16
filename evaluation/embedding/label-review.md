# #391 B 라벨 검토와 후보 비교 기준 제안

2026-11-16 사용자가 B 권장안을 채택하고 라벨·평가 자료·비교기 보완을 요청했다.
라벨 승인은 반영했으며 수치 허용값·후보 합격은 승인되지 않았다. 모델은 실행하지 않았다.

실제 질문·근거 원문·쟁점과 승인 기록은 [후보 실측 전 검토 양식](precomparison-review.md)에서
함께 대조한다. 라벨 승인과 전체 평가 기준 동결을 구분한다.

## 대상

`synthetic-embedding-v3-reviewed` (dataset schema 2, review_status=reviewed): 청크 17개, 질의 12개.
`comparison.digest` 기준 SHA-256:
`6df95d142a81c36308057f0158de718e514ba8e0c8bdb149f206f7e09c0953ef`.

아래는 사용자가 채택한 라벨이다. 모델 순위에 맞춰 수정하지 않았고 과거 결과는 보존한다.
자료 digest가 바뀌었으므로 양쪽 기준선을 다시 측정해야 하며 기존 캡처 재사용은 거부한다.

`required_evidence_groups`의 그룹 안은 OR, 그룹 사이는 AND다.
`[["korean", "multilingual"]]`은 하나만 회수해도 충족하고,
`[["continuation-head"], ["continuation-tail"]]`은 두 조각 모두 필요하다.
group recall은 충족 그룹 수 / 전체 그룹 수이며 모두 충족해야 `evidence_complete=true`다.
연결 사례의 recall=0.5는 완료 성공이 아니다. 이는 근거 회수 완료이며 답변 생성·실제 문장 연결·
의미 검증·최종 모델 합격 판정은 아니다.

## 사람 검토표

| 질의 ID | 페이지 / 섹션 / k | 현재 필수 ID | B 검토와 주의점 |
|---|---|---|---|
| ko-condition | 6,7 / s4 / 3 | condition | 라운드당 전달 조건은 m < k. 순차 라운드 수와 혼동하지 않음 |
| en-condition | 6,7 / s4 / 3 | condition | 1번과 같은 명제. head+tail의 ‘더 빠르다’를 전달 횟수와 동일시하지 않음 |
| ko-path | 6 / s4 / 2 | hops | 구간이 적으면 먼 거리 배송 조율이 쉬워진다는 방향 |
| parallel-vs-cost | 6 / s4 / 2 | rounds | 고정 순차 라운드 대 m 라운드와 총 전달 비용은 다른 기준 |
| cross-page | 6,7 / s4 / 5 | continuation-head AND continuation-tail | connection 별도 집계. k=허용 청크 수=5라 검색 선별력 증거로 쓰지 않음 |
| strict-page | 6 / s4 / 3 | continuation-head | 잘린 조각 검색 과제이며 완전한 조건 답변 과제가 아님. page7 제외 |
| near-scores | 8 / s5 / 1 | near-b | 81.02는 B. 81.01의 near-a는 오답. 실제 임베딩 점수가 근접하는지는 별도 관측 |
| korean-source | 3 / s2 / 1 | korean OR multilingual | 질문을 한국어 질문과 연결하는 근거의 언어로 명확화. 정답은 영어 근거 |
| french-query | 3 / s2 / 1 | korean OR multilingual | 같은 명제를 담은 두 근거 중 하나면 성공. 프랑스어 지원 자체를 묻는 질문은 아님 |
| long-tail | 9 / s6 / 1 | long | truncation_observation 별도 집계. 정답은 파란 스위치지만 단일 허용 문서 회수는 정보 보존 증거가 아님 |
| owner-and-revision | 전체 / s4 / 4 | condition | 고정 published-1/parse-1/ko-1 유지. 페이지 전체이므로 outside-page의 99쪽도 허용 |
| section-filter | 6 / s4 / 2 | condition | 같은 문구의 다른 페이지·섹션·owner·리비전은 후보 자격 없음 |

retrieval 10개 / connection 1개 / truncation_observation 1개로 집계하며 12개 통합 품질 합격률은 출력하지 않는다.
모든 분류에 기존 범위 검사를 적용한다. 후보 결과를 본 뒤 필수 ID를 교체하거나 k를 늘리지 않는다.

## 허용값 제안 — 미확정

| 구분 | B 제안 | 확정할 것 |
|---|---|---|
| 안전성 | 혼입·차원·비유한·0벡터·전체 설정 불일치는 0건. 평균 품질로 상쇄하지 않음 | 기존 거부 검사 유지 |
| 회수 | 질의별 그룹 recall·전체 그룹 충족 여부 공개, 과거 실패 유지 | 최소 회수율·질의별 무퇴행 기준을 후보 결과 확인 전 승인 |
| 벡터 | 청크·질의 양쪽 최대 절대 오차와 cosine distance | FP32/양자화별 수치는 A의 후보 조건 확인 후 사전 합의 |
| 순위 | top_k 교집합·순위 변동·경계 score margin 병기 | 근접 순위 교환과 필수 근거 탈락을 구분, 허용 수치는 미확정 |
| 시간 | 동일 v2 절차·환경·batch·threads·반복 사용 | cold/warm 목표 개선율·집계 방식 미확정. v1과 비율 비교 금지 |
| 메모리 | 동일 process-tree RSS 수집 방식 | worker 상한·여유율 A/Soo 확인 전 미확정. 로컬 결과를 서버 보장으로 쓰지 않음 |

수치가 미확정이므로 **후보 합격 판정은 불가**하다. 임의 기본 임계값을 적용하지 않는다.

## 다음 순서

1. 라벨/대안군/범위의 사용자 검토를 반영했다. 새 버전과 해시를 기준으로 사용한다.
2. A가 산출물·런타임·정밀도를 확인하고 A/B가 후보 결과 확인 전에 수치와 집계법을 합의한다.
3. 승인자·일자·자료/기준 해시·환경을 동결 기록에 남긴 뒤 PyTorch v2를 재캡처한다.
4. ONNX 후보를 캡처하고 실제 session/provider/threads/manifest를 대조한다.
5. 실패와 기존 결과를 보존하며 기존 context·색인·publication을 교체하지 않는다.

이번 범위에는 실제 모델 실행·API·제품 적용이 없다. #391은 계속 열어둔다.
