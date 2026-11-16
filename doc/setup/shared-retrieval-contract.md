# #390 Chroma·pgvector 공통 계약 회귀

기준 main `542ed8f`. 같은 합성 자료·고정 질문 벡터·명시적 정답을 두 실제 저장 어댑터에 적용한다.
제품 코드·DB 스키마·기존 검색 방식은 변경하지 않는다.

## 비교 조건

- 공통 자료: `tests/fixtures/shared_retrieval_contract.py`. 완결 청크 7개와 질문 벡터 `(1, 0)`.
  모든 문서 벡터는 단위 벡터이며 UUID 문자열도 같게 넣어 동점 정렬을 비교한다.
- 실제 SQLite/Chroma, PostgreSQL/pgvector 및 고정 source reader를 사용한다.
- 로컬은 `hybrid=False`를 명시한다. 기본 HYBRID와 원격 vector를 같은 순위 정책으로 간주하지 않는다.
  기존 hybrid 회귀는 별도로 유지한다.
- 번역은 합성 매핑, 임베딩은 주입한 벡터다. 모델 다운로드·생성·의미 검증은 실행하지 않는다.
- 이 비교는 범위/ID/순위/출처/점수 계약이다. 실제 모델 품질·ANN 회수율·성능 판정이 아니다.

## 거리·점수 차이

현재 Chroma 기본 컬렉션은 L2 제곱 거리, 원격은 cosine 거리다. 네이티브 점수는 각각
`1 / (1 + 해당 거리)`이므로 **원시 점수 동등성을 요구하지 않는다.**
단위 벡터에 한해 `L2² = 2 × cosine_distance`다. L2 제곱과 cosine 기대값은 좌표에서 각각
독립 계산하고 실제 원시 거리 및 반환 점수와 먼저 대조한 뒤, 비교용 cosine 값만 환산한다.
단위 벡터라는 전제도 검사한다. 반대 방향 벡터의 점수는 Chroma `1/5`,
pgvector `1/3`이어야 한다. 제품 반환 점수는 변경하지 않는다.
`1e-6` 오차는 작은 float32 fixture의 수치 비교용이며 #391 품질 허용값이 아니다.
비정규화 벡터나 다른 metric에 이 환산/순위 동등성을 일반화하지 않는다.
실제 Chroma 결과에 cosine 점수·제곱하지 않은 L2·잘못된 점수 변환·순서·rank·출처·리비전을
주입하면 공통 검증기가 거부하는지도 검사한다. 같은 순위만 유지되는 metric 오류도 잡아야 한다.

## 공통 사례

| 사례 | 기대 ID 라벨 |
|---|---|
| 전체 범위·동점 | outside-page, outside-section, a, b, c, d, opposite |
| 페이지 6 + s4, k=1 | a. 범위 밖 완전 일치 후보가 밀어내지 못함 |
| 페이지 6만, k=3 | outside-section, a, b |
| s4만, k=3 | outside-page, a, b |
| 동점 경계, k=3 | a, b, c. c와 d는 UUID 오름차순 |
| 허용 개수보다 큰 k | a, b, c, d |
| strict_pages 일반 검색 | 페이지 6 + s4의 a만 |
| 문서 안의 빈 페이지 9 | 빈 결과 |
| 페이지/섹션 교집합 없음 | 빈 결과 |
| 반대 방향 벡터 | opposite. 네이티브 점수는 위 정의대로 다름 |

페이지·섹션·원문·번역·parse/translation revision·embedding set도 기대 snapshot과 대조한다.
정답은 검색 결과에서 생성하지 않는다. 빈 결과 역시 정상 게시 자료가 있는 상태에서 검사한다.
로컬 검색 API에는 strict_pages 인자가 없으므로 일반 검색의 범위 보존만 비교한다. 원격에서는
true/false 모두 범위 밖 청크가 일반 후보에 들어오지 않아야 한다. 문장 보충은 기존 #108 회귀 영역이다.

## 게시·격리 추가 사례

- 부분 게시: 원문 8개, 성공 번역/벡터 7개와 실패 1개가 실제 저장됐는지 확인한다.
  top1과 전체 허용 ID를 모두 검사한다. 실패 청크는 완전 일치 벡터를 가질 설정이지만 색인에
  넣지 않는다. 즉석 번역/벡터 생성은 없다.
- 재파싱/재게시: 기본 색인이 실제로 새 집합으로 바뀌었음을 먼저 확인한 뒤에도 이전 context의
  ID·원문·번역·리비전·순위가 유지되는지 검사한다. 각각 실제 재파싱/게시 API를 사용한다.
  이전/새 리비전의 원문과 번역을 다르게 넣어 본문 혼합도 식별한다.
- 원격 owner 격리: 다른 owner의 고득점 자료와 context를 넣어 교차 resolver 접근 거부 및
  자신의 top1 보존을 확인한다. 로컬 단일 사용자 DB의 tenant 인증 증거로 간주하지 않는다.

## 실행·현재 결과·한계

```powershell
python -m pytest tests/integration/test_chroma_shared_contract.py tests/integration/test_pgvector_shared_contract.py -q
```

Chroma 파일은 DSN 없이 실제 로컬 어댑터 15개 사례를 실행한다. pgvector 파일은 두 backend를
직접 비교하는 16개 사례이며 `SOLO_TEST_POSTGRES_DSN`이 없으면 명시적으로 skip한다.
원격 준비는 기존 임시 schema fixture를 사용하며 운영 DB를 지정하지 않는다. 기존 CI의 일회용
PostgreSQL/pgvector에서 함께 실행할 수 있다. skip을 pgvector 통과로 기록하지 않는다.

PR #486 작성 당시 Windows 로컬 확인: 신규 Chroma 15개 통과. 기존 범위 검색/hybrid·원격 계약 회귀 포함
**92 passed, 16 skipped**. 실제 PostgreSQL DSN/실행기가 없어 pgvector 비교 16개는 미실행이다.
실제 API·모델·원본 데이터 접근은 없다. 이번 단계에서 CI/실환경 통과를 주장하지 않는다.

### C 재검토 결과 (2026-11-16)

위 결과는 작성자의 Windows 로컬 실행 기록이다. 이후 PR #486 head `2276cda`를
macOS에서 같은 6개 테스트 파일로 재실행해 **92 passed, 16 skipped**를 확인했다.
이 환경에도 PostgreSQL DSN이 없어 pgvector 16개는 건너뛰었으며 로컬 통과로 계산하지 않는다.

동일 head의 [CI 실행 310](https://github.com/soogwon/soloLevelingPaper/actions/runs/43801361632)은
9개 체크가 모두 성공했다. Python 로그에서 일회용 PostgreSQL/pgvector 서비스와
`SOLO_TEST_POSTGRES_DSN` 설정, 단위 테스트 **3933 passed, 6 skipped** 및
통합 테스트 **479 passed, 29 skipped**를 확인했다. 이는 CI 전체 집계다.
개별 사례의 결과 목록을 제공하는 출력은 아니므로 별도 로컬 pgvector 실행 결과와
혼용하지 않는다. 배포 서버·실제 임베딩 모델·ANN 품질 검증의 증거로도 사용하지 않는다.

후속 CI에서는 `pgvector 공통 계약 개별 검증` 단계로 해당 파일을 `-v`로 별도 실행한다.
JUnit 결과에 최소 16개 사례가 있고 skipped/failure/error가 하나도 없어야 성공한다.
따라서 DSN 누락으로 전부 skip되면 pytest 자체가 성공하더라도 이 단계는 실패한다.
사례별 결과는 로그와 `pgvector-shared-contract` artifact로 보관한다.

### B의 실제 pgvector 개별 CI 결과 확인 (2026-11-16)

최신 main `437131e5d1c34467c4e5e0cc962c14c349a5522f`의
[CI 실행 43808635412](https://github.com/soogwon/soloLevelingPaper/actions/runs/43808635412),
[Python job 133105079937](https://github.com/soogwon/soloLevelingPaper/actions/runs/43808635412/job/133105079937)
로그를 직접 확인했다. Ubuntu 24.04 / Python 3.12, 일회용 PostgreSQL 16 호환
`pgvector/pgvector:0.8.7-pg16`, 테스트 전용 DSN을 사용하는 실행이다.

`pgvector 공통 계약 개별 검증`은 **16 passed in 3.58s, 0 skipped**이며 다음 사례 각각의
PASSED 출력을 확인했다. 두 backend 비교는 이 파일의 실제 Chroma·PostgreSQL fixture로 수행된다.

| 테스트 | 개별 결과 수 |
|---|---:|
| 전체/페이지/섹션 범위, top1, 동점, 큰 k, strict 일반 검색, 빈 결과, 반대 벡터 | 10 passed |
| 부분 게시에서 실패 청크 제외: top1 및 전체 ID | 2 passed |
| 재게시 후 기존 context·출처 고정 | 1 passed |
| 원격 owner 격리 | 1 passed |
| 같은 단위 벡터의 네이티브 점수 차이 | 1 passed |
| 독립 수학 기대값과 양쪽 원시 거리 비교 | 1 passed |

JUnit 검사 단계가 사례 수 16개 이상과 skipped/failure/error 0건을 확인했고,
`pgvector 공통 계약 결과 보관` 단계도 성공했다. 결과 파일은 `pgvector-shared-contract` artifact의
`pgvector-shared-contract.xml`이다. 코드/설정은 이 확인 작업에서 변경하지 않았다.

같은 job의 전체 집계는 단위 **3933 passed, 6 skipped**(69 subtests 별도),
통합 **482 passed, 29 skipped**다. 전체 skip 29개를 위 파일의 skip으로 오해하지 않으며,
전체 집계도 공통 계약 신규 테스트 개수로 표시하지 않는다.

따라서 **실제 pgvector 공통 비교 16개는 위 CI 환경에서 확인 완료**다. 이전 Windows/macOS의
16개 skip 기록은 당시 로컬 결과로 보존하며 로컬 PostgreSQL 성공으로 바꾸지 않는다.
로그 조회 외에 외부 AI API·원본 DB·배포 서버에는 접근하지 않았다.

### #390에서 남은 범위

| 확인 범위 | 현재 증거 / 다음 담당 |
|---|---|
| 공통 검색 범위·순위·거리·출처·부분 게시·과거 context | 위 실제 CI 16개와 로컬 Chroma 15개. 이 구간의 pgvector 비교 미실행은 해소 |
| strict_pages·섹션과 허용 보충 구절 | 위 strict 사례는 일반 검색만 검사. 기존 #108/B 원문 구절 검사를 실제 원격 실행에 연결한 증거는 별도 |
| 실제 runtime의 전체 fingerprint·질의 벡터 | A+B/#391. 고정 합성 벡터로 실제 모델 품질을 판정하지 않음 |
| 인증 owner→MCP→생성/의미 검사→원자 저장→재조회 | C 공개 연결, A 저장/원장, B runner를 같은 실행으로 검증. #395/#397/#399와 연결 |

#390은 위 잔여 연결·검증 때문에 열어둔다. CI 합성 계약 통과로 원격 제품 공개나 상위 티켓
종료를 승인하지 않는다.

OAuth/MCP·원장·제공자·답변/근거 저장·인용 허용 구절의 전체 흐름이나 게시 시 vector/manifest
집합 불일치 거부를 새로 검증한 것은 아니다. C 제품/호스트 연결, A 게시 집합 검증 위치 확인,
#391 실제 runtime 판정은 별도다. #390은 계속 열어둔다.
