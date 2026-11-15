# #391 임베딩 비교 준비

이 PR은 B가 독립적으로 준비할 **합성 자료·캡처 형식·오프라인 비교기**이다.
모델·ONNX exporter·DB·검색 어댑터·런타임 의존성은 추가하지 않는다.
PyTorch 기준선과 ONNX 실측, 허용값 확정은 아직 남아 있으므로 #391을 닫지 않는다.

## 고정 자료

`cases.json`: 직접 작성한 합성 청크 17개와 질의 12개. 논문 PDF나 사용자 데이터가 아니다.
필수 근거 ID는 **사람 검토 전 초안**이다. 검토 때 `review_status`와 자료 버전을 함께 갱신하고,
자료 해시가 달라지면 두 런타임 모두 다시 캡처한다. 모델 결과를 본 뒤 정답을 맞추지 않는다.

| 질의 | 비교 목적 |
|---|---|
| ko-condition / en-condition | 한국어·영어 질문과 조건 구절 |
| ko-path | 한국어 질문과 영어 근거 |
| parallel-vs-cost | 순차 연산과 총 계산량 구별 |
| cross-page | 앞·뒤 페이지의 두 근거 회수 |
| strict-page | 6페이지 내 후보만 검색 |
| near-scores | 수치가 근접한 두 문서의 순위 변화 |
| korean-source / french-query | 한국어 원문·다국어 질의 |
| long-tail | 최대 길이보다 긴 입력 끝부분의 정보 유실 관찰 |
| owner-and-revision | 사용자·버전·파싱·번역·게시 집합 범위 |
| section-filter | 페이지·섹션 필터를 top_k보다 먼저 적용 |

`long-tail`의 필수 근거는 관련 문서 라벨이며, 모델이 잘린 끝부분까지 읽었다는 뜻이 아니다.
실측 시 양쪽의 토큰 수·잘림 여부를 별도 관찰 기록에 남긴다.
near-scores는 텍스트가 유사한 표본이다. 실제 벡터 점수가 근접하는지는 cutoff margin으로 확인한다.
동점·의도적 순위 역전은 단위 테스트의 알려진 벡터로 별도 검증한다.

## 1. 네트워크 없는 smoke

저장소 루트에서 실행한다. 출력의 부모 디렉터리를 먼저 만든다.

```powershell
New-Item -ItemType Directory -Force runtime/embedding-evaluation
python scripts/compare_embedding_captures.py --smoke --output runtime/embedding-evaluation/smoke.json
python -m pytest tests/unit/test_embedding_evaluation.py -q -p no:cacheprovider
```

해시로 만든 합성 벡터를 자기 자신과 비교한다. `quality_measurement=false`,
`performance_comparable=false`, `decision=pending_thresholds`이다.
회수율이 낮아도 실제 모델 품질을 의미하지 않으며, 이 결과를 PyTorch 기준선으로 쓰지 않는다.
출력 파일이 이미 있으면 실패한다. 입력·기존 결과를 덮어쓰지 않는다.

## 2. A의 런타임에서 캡처할 형식

동일한 자료와 아래 설정을 고정한 뒤 청크와 질의를 **모두** 임베딩한다.
실제 모델 실행·다운로드·ONNX export는 이 명령이 수행하지 않는다.
특정 Python 모델 인터페이스를 A의 계약으로 가정하지 않고 JSON으로 경계를 둔다.

```python
from evaluation.embedding.comparison import digest, load_json

data = load_json('evaluation/embedding/cases.json')
# 실제 캡처 프로그램에서 채울 값. 아래 예시 자체는 실행 결과가 아니다.
capture = {
    'schema_version': 1,
    'dataset_sha256': digest(data),
    'synthetic': False,
    'settings': {
        'model_id': '실제 모델 저장소 ID',
        'model_revision': '고정한 모델 커밋',
        'tokenizer_id': '실제 토크나이저 저장소 ID',
        'tokenizer_revision': '고정한 토크나이저 커밋',
        'pooling': '실제 pooling 설정',
        'normalization': 'l2',  # 또는 none
        'max_length': 512,     # 실제 고정값으로 대체
        'dimension': 384,      # 실제 출력 차원으로 대체
        'distance': 'cosine',
        'runtime': 'pytorch',  # 후보는 onnx
        'runtime_version': '실제 런타임 버전',
        'precision': '실제 정밀도',
        'artifact_sha256': '실제 실행 모델 산출물의 SHA-256 (소문자 64자리)',
    },
    'chunks': {},  # 모든 chunk id → 숫자 벡터
    'queries': {}, # 모든 query id → 숫자 벡터
    'measurements': {
        'cold_ms': [], 'warm_ms': [], 'peak_rss_bytes': 0,
        'environment': 'OS·CPU·메모리·의존성 잠금 파일 해시',
        'procedure': '실측 절차 버전',
        'rss_method': '프로세스 트리 RSS 수집 방법·주기',
        'batch_policy': '전체 자료 처리 순서·배치 크기',
        'threads': '실제 스레드 설정', 'device': 'cpu',
    },
}
capture['embedding_fingerprint'] = digest(capture['settings'])
```

- runtime·runtime_version·precision·artifact hash 이외의 설정이 다르면 비교를 거부한다.
  pooling 차이 등을 허용해 결과를 합격시키지 않는다. cosine 이외의 거리는 현재 지원하지 않는다.
- 차원 불일치, 누락·추가 ID, NaN/Infinity/bool, 0벡터, L2 정규화 불일치를 거부한다.
- 지문은 설정 변조·혼합 실수를 감지하는 장치이며, 설정과 실제 모델이 일치함을 증명하지 않는다.
  A의 캡처 코드·모델 산출물·환경을 검토해야 한다. 이 형식이 서비스 embedding set 스키마는 아니다.
- 벡터와 실측 캡처는 `runtime/embedding-evaluation/`에 보관하고 커밋하지 않는다.
  자료에 없는 사용자 질문·논문 본문을 이 실행기에 입력하지 않는다.

### 실측 절차 (아직 실행하지 않음)

1. 자료 버전·해시와 모델·토크나이저 커밋을 고정하고 필요한 모델 파일을 로컬에 준비한다.
2. 동일한 OS·CPU·device·스레드·배치·처리 순서에서 PyTorch 기준선을 먼저 캡처한다.
3. cold는 새 프로세스 시작부터 import·모델 로딩·전체 청크/질의 임베딩 완료까지의 벽시계 시간이다.
   파일 다운로드 시간은 제외하며 OS 파일 캐시 상태·반복 횟수를 기록한다.
4. warm은 같은 프로세스에서 한 차례 준비 실행 후 전체 청크/질의를 다시 임베딩하는 시간이다.
   cold와 warm의 원시 반복값을 저장한다. peak RSS는 cold 프로세스와 자식의 RSS를 같은 방법·주기로
   관찰한 최대값(bytes)이다. Windows working set과 Linux ru_maxrss를 무표기 혼용하지 않는다.
5. PyTorch 결과를 확보한 다음 허용 오차·최소 회수율·순위/성능 기준을 **후보 채점 전에** 합의·고정한다.
6. ONNX를 같은 절차로 캡처하고 비교한다. 비교기는 허용값을 생성하거나 합격을 선언하지 않는다.
   실제 export·토큰 잘림·측정 도구 검증과 합격 판정은 후속 실측 PR에서 다룬다.

```powershell
python scripts/compare_embedding_captures.py --baseline runtime/embedding-evaluation/pytorch.json --candidate runtime/embedding-evaluation/onnx.json --output runtime/embedding-evaluation/comparison.json
```

## 결과 해석과 한계

- 청크·질의 각각의 최대/평균 절대 오차와 최대 cosine distance를 기록한다.
- 각 질문의 양쪽 top_k·필수 근거 회수율·근거 순위 변화·top_k 교집합 비율·경계 점수 차이를 기록한다.
  순위 변화는 필터된 전체 후보 내 순위 기준이다. 동점은 chunk ID 오름차순으로 고정한다.
- 성능 원시값과 두 캡처 해시·설정을 보고서에 보존한다. 환경·절차·반복 횟수 등이 다르면
  `performance_comparable=false`로 표시한다. 같아도 환경 기록의 진위나 측정 정확성을 보증하지 않는다.
- 보고서에 원문·질문·벡터를 복사하지 않는다. 사용자·리비전 필터는 합성 기준 검색에만 적용되며,
  **pgvector/Chroma 실제 격리, ANN 순위, 페이지 보충·생성·의미 검증은 검증하지 않는다**(#390/#397).
- `strict-page`는 범위 제한 기준 사례다. #108 서비스의 허용 구절 보충 로직을 대체하지 않는다.
- 기본 `decision=pending_thresholds`와 정답 검토 상태를 유지한다. 모델이 같거나 오차가 작아도
  기존 context·색인·벡터를 바꾸지 않는다. 원격은 런타임 지문별 별도 embedding set으로 시작하는
  권장안을 유지한다(ADR25의 미결 3은 여전히 미결).

## 후속 및 다른 티켓과의 경계

| 항목 | 지금 준비한 범위 | 남은 담당·선행 |
|---|---|---|
| #391 | 합성 자료, 캡처 계약, 비교기·실패 방어 테스트 | B 정답 검토·실측/허용값, A 실제 런타임·export |
| #390 | 비교기의 필터된 exact cosine 기준 검색 | A+B owner/context/revision/게시 포트 합의 후 어댑터 연결 |
| #388/#399 | 변경 없음 | C 연결 스파이크·A 인증 검토 후 실제 MCP 연결 |
| #389/#392~#396/#400 | 변경 없음 | DB·큐·동의·예산·운영 계약 및 실측 |
| #397 | 변경 없음 | 위 구현 완료 뒤 실제 종단 검증 |

#398은 작업 티켓이 아니라 이미 병합된 문서 PR이다. #401도 #399·#400을 문서에 연결한 PR이다.
이 준비 작업은 원격 전환·배포 승인이나 #391의 완료를 뜻하지 않는다.
