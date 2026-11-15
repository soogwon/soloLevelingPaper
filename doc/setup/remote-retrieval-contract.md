# #390 B의 원격 검색·근거 경계 준비

기준 main: `2b5ca30` (#403·#405·#406·#408·#410 병합 후).
이 문서는 B의 내부 타입·검증·테스트와 **어댑터 연결 전 확인할 제안**을 분리한다.
pgvector·서버 context resolver·MCP 연결을 구현하거나 A의 저장 계약을 변경하지 않는다.

## 기존 합의로 유지하는 것

- 인증된 owner가 필수이며 다른 사용자 context와 존재하지 않는 context는 같은 not-found다.
- 저장 context의 publication·paper/version·parse·translation·embedding set을 고정한다.
  `get_active()`로 최신 publication을 가져와 기존 context를 바꾸지 않는다.
- 검색은 owner·고정 revision·게시 집합·페이지·섹션으로 **후보를 먼저 제한하고** top_k를 적용한다.
- 일반 검색은 `strict_pages=false`여도 페이지 밖 후보를 반환하지 않는다. 허용된 다음 페이지의
  문장 완성은 #108 보충 경로가 담당하며 원문 선택지·생성·검증·저장·반환의 동일 구절 제한을 유지한다.
- 로컬 stdio·기존 SearchScope·근거 검증은 그대로 둔다. 동기화 LearningRecord를 서버 실행 context로
  간주하지 않고 owner가 검증한 별도 매핑 없이 자동 재연결하지 않는다.

## 이번 B 구현

`application/evidence_qa/remote_contract.py`는 아직 제품 경로에 연결하지 않은 내부 경계다.

| 타입/함수 | 역할 |
|---|---|
| RemoteSearchBinding | owner·context·paper/version·publication·3개 revision/set·모델 지문·게시 정책 고정 |
| RemoteSearchFilters | 페이지·섹션·strict_pages를 손실 없이 유지 |
| RemoteContextResolver | owner와 context로 신뢰할 수 있는 binding을 해석하는 포트 초안 |
| RemoteScopedSearch | 고정 binding과 필터를 적용해 검색하는 포트 초안 |
| validate_remote_hits | 혼입·필터 위반·중복·개수·순서·거리 오류 거부, 고정 오류 코드 사용 |

`RemoteSearchHit`은 검증할 최소 metadata이며 본문/번역/근거 저장 모델을 대체하지 않는다.
binding을 모델·사용자 입력에서 만들거나 그 자체를 인증·게시 증명으로 사용하지 않는다.
`model_fingerprint` 문자열의 직렬화 방식은 A의 JSON 지문과 연결할 때 합의해야 한다.

## B 제안 — 연결 전에 A와 확인

### 거리와 점수

cosine distance d∈[0,2]에 `score=1/(1+d)`를 적용한다. 현재 로컬 vector-only 경로와 같은
단조 변환으로 높을수록 관련성이 높다. NaN/Infinity/bool/범위 밖 거리는 거부하며 임의 보정하지 않는다.
동점은 chunk ID 오름차순이다. BM25/RRF 점수와 수치 자체를 비교하지 않는다.
원격 hybrid/ANN 적용 시 검색 방법과 후보 수를 따로 기록하고 같은 검색이라고 표기하지 않는다.

### partial 게시 색인

현재 A 저장 계약의 `indexed_chunk_count == translated_chunk_count`를 기준으로, 해당 translation
revision에서 **성공한 번역으로 확정된 색인 집합만** 일반 후보로 제안한다. 누락 원문을 다른 경로에서
즉석 임베딩하거나 최신 번역으로 채우지 않는다. 번역 0개 partial 게시 거부는 유지한다.
원문 전체 검색을 추가하려면 별도 embedding set/검색 모드 계약이 필요하다.

partial 색인이라는 이유만으로 답변을 무조건 partial/insufficient로 바꾸거나 ok로 보장하지 않는다.
답변 상태는 질문 항목별 근거·추출·의미 검사 결과에 따른다. 누락 범위 표시는 별도로 보존한다.
원문은 고정 parse에서, 번역은 고정 translation revision에서 읽으며 인용문을 덮어쓰지 않는다.

### 필수 근거 회수 기준

고정 fixture의 허용 근거 ID가 filtered top_k에 들어오는지 검증한다. 외부 사용자·revision·범위 밖의
더 높은 점수 후보가 있어도 허용 근거가 밀려나지 않아야 한다. 이는 데이터셋별 회수 oracle이며
실제 모델 recall 허용값은 #391 기준선 이후 따로 합의한다. 의미 일치와 숫자 정확도를 보증하지 않는다.

## 포트 목록과 연결 순서

| 현 경로 | 필요한 경계 | 담당·다음 단계 |
|---|---|---|
| evidence_qa/ports.py의 LearningContextReader·DefaultContextStore | 인증 owner + 서버 실행 context resolver | A 조회/생성 구현, B binding 검증; 로컬 포트 변경 없음 |
| evidence_qa/search.py·infrastructure/retrieval/sqlite_chroma.py | 고정 scope의 pgvector candidate search | A SQL·벡터 저장, B 거리·범위 공통 테스트 |
| evidence_qa/ports.py의 ContinuationReader·page_completion.py | 같은 게시 집합의 인접 청크·허용 구절 | B #108 회귀 재사용, 섹션·순서 데이터는 A 확인 |
| evidence_qa/evidence.py·generation/evidence_store.py | 고정 context의 원문/번역 조회·원자 근거 저장 | A 저장소, B 기존 인용/소속 검사 유지 |
| application/annotations/service.py | 번역 리비전·저장 인용 snapshot 조회 | 기존 SQLite 직접 의존 분리 범위는 별도 작은 PR |
| workers/reparse.py·workers/translation_recovery.py | 원격 job lease 및 세대별 결과 게시 | #408 저장 계약 사용, 파일/프로세스 경계는 A와 연결 |

특히 원격 chunk 스키마에는 현재 section_id·chunk 순서/문장 보충에 필요한 metadata의 전체 매핑이
준비됐다고 가정하지 않는다. 지원할 수 없는 섹션 제한을 조용히 무시하지 말고 연결 전 계약을 확정한다.
vector 컬럼·embedding set 차원/거리·게시된 벡터 불변성 및 허용 집합 확인은 A의 #390 구현에서 필요하다.

## 테스트의 의미와 남은 범위

```powershell
python -m pytest tests/unit/test_remote_retrieval_contract.py -q
```

공통 검증은 각 binding 필드, 페이지·섹션, partial, 순서·동점·중복·잘못된 거리의 거부를 확인한다.
합성 후보의 prefilter→top_k와 전역 top_k→postfilter를 비교해 후자는 허용 근거를 잃는다는 oracle도 있다.
**사후 검증은 빈 결과를 거부하지 못하므로 실제 어댑터의 사전 SQL 필터 검증을 대체하지 않는다.**

Chroma/pgvector 실제 어댑터를 같은 fixture로 실행하는 검증, 실제 서버 owner/context 조회,
ANN 회수, #108 전체 허용 구절·출처 저장 회귀는 어댑터 연결 후 추가해야 한다.
이 PR의 테스트만으로 사용자 격리나 #390 완료를 주장하지 않는다. #390은 열어둔다.
