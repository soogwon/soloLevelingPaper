# #391 임베딩 비교 준비

## 캡처 v2 보완

- ONNX graph의 producer/version·opset, graph 및 모든 external tensor 파일, 토크나이저/공통 설정
  파일을 지문에 결합한다. graph metadata를 읽기 위해 평가 환경에 `onnx` 패키지가 필요하다.
  export·파일 다운로드는 하지 않으며 metadata가 없으면 추정하지 않고 거부한다.
- ORT SessionOptions에 intra/inter-op 스레드와 sequential 실행을 명시하고, 실제 생성된 session의
  provider·옵션을 관측해 일치하는지 검사한다. 환경 변수만으로 단일 스레드를 보증하지 않는다.
- ONNX graph의 tensor type·quantization operator 관측을 기록하며 정밀도를 무조건 float32로 쓰지 않는다.
  이 관측이 exporter의 정확도·양자화 방법을 인증하는 것은 아니므로 A의 산출물 검토는 별도다.
- PyTorch/ONNX는 공통 procedure v2를 사용한다. input artifact hash·스레드·환경·반복 수 등이
  일치해야 performance_comparable이 true다. 기존 v1 기준선과 새 v2 후보는 그대로 비교 가능 처리하지 않는다.
  기준선도 v2로 다시 캡처해야 한다. 기존 원시 결과나 보고서를 덮어쓰지 않는다.
- ORT session의 접근 경로는 현재 adapter의 `model[0].auto_model.model`이다. 실제 설치한
  sentence-transformers/Optimum 버전에서 이 경로와 session_options 전달을 확인해야 한다.
  지원되지 않으면 실패하며 다른 provider/기본 스레드로 조용히 대체하지 않는다.

이번 보완은 오프라인 파일·대역 테스트만 수행했다. 후보 실측·export·모델 다운로드는 하지 않았다.
라벨은 2026-11-16 사용자 권장안 채택을 반영했으며 수치 허용값은 미확정이다.
후보 결과를 보기 전에 허용값·환경과 데이터/기준 버전·해시를 최종 동결해야 한다. #391은 계속 열어둔다.

이 PR은 B가 독립적으로 준비할 **합성 자료·캡처 형식·오프라인 비교기**이다.
모델·ONNX exporter·DB·검색 어댑터·런타임 의존성은 추가하지 않는다.
PyTorch 기준선과 ONNX 실측, 허용값 확정은 아직 남아 있으므로 #391을 닫지 않는다.

## 고정 자료

`cases.json`: 직접 작성한 합성 청크 17개와 질의 12개. 논문 PDF나 사용자 데이터가 아니다.
소재(lantern relay, bucket chaining, depot, convoy)는 지어낸 것이며 실제 논문의 주장을 재서술하지 않는다.
자료를 고칠 때도 공개 논문의 문장이나 주장을 옮기지 않는다.
현재 자료는 `synthetic-embedding-v3-reviewed`, dataset schema 2, `review_status=reviewed`다.
라벨 검토 반영이며 모델 합격 승인은 아니다. 자료 해시가 달라져 두 런타임 모두 다시 캡처해야 한다.
기존 결과·실패 기록은 보존하며 모델 결과를 본 뒤 정답을 맞추지 않는다.

### 근거 그룹과 집계 계약

- `required_evidence_groups`: 바깥 그룹은 AND, 각 그룹 안은 OR.
  `[["korean", "multilingual"]]`은 둘 중 하나면 충족한다.
  `[["continuation-head"], ["continuation-tail"]]`은 두 조각 모두 필요하다.
- 보고서 schema 2의 `baseline/candidate_required_recall`은 충족 그룹 수 / 전체 그룹 수다.
  `*_group_hits`로 그룹별 결과, `*_evidence_complete`로 모든 그룹 충족 여부를 표시한다.
  연결 사례의 recall=0.5는 성공이 아니다. 이 값은 회수 결과이며 생성·연결·의미 검증이나 합격 판정은 아니다.
- `required_rank_changes`는 모든 그룹에 포함된 개별 ID의 순위 변화다. 대안 ID가 교환돼
  top_k 교집합이 0이어도 같은 그룹을 충족하면 근거 회수는 성공할 수 있다.
  `required_group_rank_changes`는 각 그룹의 가장 높은 순위 변화다. 같은 순위의 대안으로
  교체되면 0이며 개별 ID 변화와 구분한다. 회수 완료 여부는 여전히 top_k 내 그룹 충족으로 판단한다.
- `evidence_summary_by_kind`는 retrieval 10개 / connection 1개 / truncation_observation 1개를
  각각 집계한다. 12개 전체를 섞은 품질 합격률은 출력하지 않는다.
  각 분류의 `mean_group_recall`은 질의별 그룹 recall의 단순 평균이며, `complete_count`는
  모든 그룹을 회수한 질의 수다. 질의가 없는 분류는 평균을 0 대신 null로 표시한다.
- `selection_is_exhaustive`는 k가 허용 청크 수 이상인지 표시한다. cross-page와 long-tail은
  전수 반환이므로 검색 선별력이나 잘린 정보 보존의 증거로 쓰지 않는다.
- 그룹·ID 중복, 빈 그룹, 잘못된 ID/형식, 범위 밖 대안, k보다 많은 필수 그룹은 거부한다.
  schema 1은 기존 `required_chunk_ids`의 모든 ID 필수 의미로 읽으며 신·구 형식 혼합을 거부한다.
  캡처 schema는 1을 유지하지만 자료 digest가 달라 과거 캡처를 새 자료에 재사용할 수 없다.
  connection은 적어도 두 개의 필수 그룹이 필요하다. 두 조각을 한 OR 그룹으로 합친 라벨은 거부한다.

| 질의 | 비교 목적 |
|---|---|
| ko-condition / en-condition | 한국어·영어 질문과 조건 구절 |
| ko-path | 한국어 질문과 영어 근거 |
| parallel-vs-cost | 순차 라운드 수와 총 전달 비용 구별 |
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

## B의 로컬 PyTorch 기준선 캡처

`scripts/capture_pytorch_embeddings.py`는 이미 설치된 sentence-transformers/PyTorch와
추가 측정 의존성 `psutil==7.2.2`를 사용한다. 제품 의존성에는 추가하지 않는다.
모델은 `paraphrase-multilingual-MiniLM-L12-v2`의 로컬 Hugging Face snapshot(40자리 커밋)
경로만 받으며 HF/Transformers 오프라인 모드를 강제한다. 모델 파일을 다운로드하지 않는다.

```powershell
python scripts/capture_pytorch_embeddings.py --model-dir <로컬 snapshot 경로> --output runtime/pytorch-baseline --repeats 3 --warm-repeats 3
```

- 새 프로세스 3회, 각 준비 실행 뒤 warm 3회. CPU·float32·L2·batch 8·스레드 1·최대 길이 128을
  기본으로 고정한다. 실제 pooling·런타임 버전·모델/토크나이저 파일 지문은 캡처에서 읽는다.
- cold는 프로세스 시작부터 라이브러리 import·모델 로딩·전체 임베딩·토큰 수 관측까지다.
  warm에도 전체 임베딩·토큰 수 관측을 포함한다. 순수 encode 시간만 잰 수치와 혼합하지 않는다.
- peak RSS는 cold/warm 전체에서 worker 및 자식 RSS 합을 10ms마다 관찰한 최대값이다.
  샘플 사이의 순간 최대값이나 GPU 메모리 측정이 아니다. 재부팅/OS 캐시 비움은 수행하지 않는다.
- 해시 계산은 cold 측정 밖에서 모델 로딩 전에 수행하므로 파일 캐시는 데워질 수 있다.
  이 결과를 디스크 콜드 시작·Desktop end-to-end·서버 처리 성능으로 일반화하지 않는다.
- 캡처 전후 모델 파일 지문을 확인하고, 토큰 수·잘림 여부와 반복 간 벡터 오차를 기록한다.
- 새 전용 디렉터리만 만들며 기존 결과를 덮어쓰지 않는다. 실패 시 부분 worker 결과는 그곳에만
  남는다. timeout 시 이 명령이 만든 worker 트리만 종료하며 MCP/다른 실행에는 손대지 않는다.
- 원시 벡터·캡처는 runtime에만 둔다. 결과 문서에는 본문·개인 경로·벡터를 복사하지 않는다.
- 라벨은 사용자 권장안을 반영했다. ONNX 실측·공동 허용값 확정·원격 set 연결은 별도이며
  과거 캡처는 해당 시점의 draft 자료에 대한 결과다.

## ONNX 후보 캡처

ONNX 파일은 별도 export 검토에서 만든 고정 로컬 산출물만 받는다. 캡처 명령은 다운로드나 자동 export를
허용하지 않으며 `onnx/` 아래 파일의 SHA-256을 runtime 지문에 포함한다. CPUExecutionProvider,
float32, 동일 tokenizer·pooling·L2·max length·batch·thread 설정을 사용한다.

```powershell
python scripts/capture_onnx_embeddings.py --model-dir <동일 snapshot 경로> --file-name onnx/model.onnx --output runtime/onnx-candidate
```

새 프로세스 cold/warm/RSS와 반복 벡터를 PyTorch와 같은 형식으로 저장하지만, 이 자체 비교는 후보의
반복 가능성만 확인한다. B가 라벨과 허용값을 후보 채점 전에 확정한 뒤 `compare_embedding_captures.py`로
PyTorch 기준선과 비교해야 하며, 통과해도 기존 context나 embedding set을 자동 교체하지 않는다.

## B 라벨 검토 제안

[수치 허용값 제안서](threshold-proposal.md)에 B의 FP32 품질 기준과 A와 협의할
반복·성능·메모리 기준을 수치·단위·집계법·이유별로 정리했다. 승인 전 초안이며 자동 판정은 연결하지 않았다.

[12개 질의의 검토 반영과 사전 기준 제안](label-review.md)을 참고한다.
라벨은 reviewed로 갱신했고 수치 기준 미확정이므로 후보 판정은 pending_thresholds를 유지한다.

[후보 실측 전 사람 검토·기준 동결 기록](precomparison-review.md)에 실제 질문·근거·범위와
채택한 대안 근거, 허용값별 결정란을 모았다. 수치 승인란은 비어 있으며 모델 실행 없이 검토할 수 있다.
특히 long-tail은 허용 문서가 하나여서 문서 회수 성공을 끝 구절 보존의 증거로 쓰지 않는다.

## v3 라벨·그룹 판정 오프라인 검증

2026-11-16 Windows에서 비교기·산출물·PyTorch/ONNX 캡처 대역 회귀 **94 passed**.
대안 교환·대안 미회수·한 그룹 중복 회수·연결 조각 부분 회수·신구 자료 형식·범위 밖 대안
거부·과거 캡처 digest 불일치·관측 집계 분리를 포함한다. 재검토에서 연결의 단일 OR 라벨 거부,
모든 출처 차원의 범위 밖 대안 혼합 거부, 그룹별 순위, 집계 분모·빈 분류도 확인했다.
Ruff와 공백 검사도 통과했다.
최초 실행은 runtime 부모 디렉터리 누락으로 임시 경로 설정 오류가 있었고 준비 후 전체 재실행이 통과했다.
실제 모델·export·다운로드·API·DB는 사용하지 않았다. CLI 합성 smoke도 합격 판정을 보류한다.

```powershell
New-Item -ItemType Directory -Force runtime
python -m pytest tests/unit/test_embedding_evaluation.py tests/unit/test_embedding_artifacts.py tests/unit/test_onnx_capture.py tests/unit/test_pytorch_capture.py -q --basetemp=runtime/pytest-391
```
