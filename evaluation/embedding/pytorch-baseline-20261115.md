# #391 PyTorch 로컬 기준선 — 2026-11-15

실측 대상은 합성 v2-draft의 청크 17개·질의 12개이다. 원본 DB·기존 context·벡터 저장소에
접근하지 않았다. 모델 다운로드·외부 AI API·ONNX 실행은 0회다. 원시 벡터는 runtime에만 보관한다.

## 고정 조건

| 항목 | 값 |
|---|---|
| OS / Python | Windows 11 / Python 3.12.14 |
| CPU | Intel Family 6 Model 165 / CPU 실행 |
| 모델 / 토크나이저 | sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 |
| snapshot | e8f8c211226b894fcb81acc59f3b34ba3efd5f42 |
| 모델 파일 묶음 SHA-256 | 715d2c3d56264ca48e372fceeb7ba2ac9d41d30e259386c1d81008d3df50164a |
| 런타임 | torch 2.14.0 / sentence-transformers 6.1.0 / transformers 5.17.0 |
| pooling / 정규화 / 차원 | mean / L2 / 384 |
| 길이 / 정밀도 / 거리 | 128 / torch.float32 / cosine |
| 배치 / 스레드 | 청크→질의, batch 8 / intra·interop·OMP·MKL 1 |
| 메모리 수집 | psutil 7.2.2, worker 및 자식 RSS 합, 10ms 표본 간격 |

기존 환경의 로컬 캐시를 읽었으며 제품 모델 설정은 바꾸지 않았다.

## 시간·메모리

| 새 프로세스 | cold (초) | warm 3회 (초) |
|---|---:|---|
| 1 | 36.187 | 1.582 / 1.759 / 1.637 |
| 2 | 32.450 | 1.601 / 1.457 / 1.367 |
| 3 | 30.585 | 1.282 / 1.275 / 1.413 |

관측 peak RSS: **1,471,307,776 bytes (약 1.37 GiB)**.
cold는 새 프로세스 시작부터 import·모델 로딩·전체 임베딩·토큰 수 관측까지이고,
warm은 같은 프로세스에서 준비 실행 후 전체 임베딩·토큰 수 관측을 반복한 값이다.
순수 encode 지연이나 질문 한 건의 응답 시간이 아니다.

## 검색·잘림 관측

| 질의 | 초안 필수 근거 recall@k |
|---|---:|
| ko-condition / en-condition / ko-path / parallel-vs-cost | 각각 1 |
| cross-page | 1 (필수 앞·뒤 청크 모두 포함) |
| strict-page | **0** |
| near-scores | **0** |
| korean-source / french-query / long-tail | 각각 1 |
| owner-and-revision / section-filter | 각각 1 |

실패 두 건을 숨기거나 정답을 결과에 맞게 바꾸지 않았다. strict-page는 허용 페이지 안에서
필수 조각을 top_k로 회수하지 못했다는 뜻이며, 범위 밖 근거를 반환했다는 뜻은 아니다.
near-scores는 근접 수치 문서의 구별에 실패했다. 둘 다 후보 비교 시 확인할 기준선 결과다.

`long` 청크만 토큰 제한으로 잘렸다. long-tail 회수 성공은 문서가 검색됐다는 뜻이며,
모델이 잘린 마지막 정보를 읽었다는 뜻이 아니다.
세 실행의 청크·질의 벡터 최대 절대 차이는 0이었다. 자기/반복 비교의 cosine distance에는
부동소수 연산 오차 약 2.22e-16이 관측됐다.

## 한계와 다음 단계

- 자료와 필수 근거 라벨은 사람 검토 전 draft다. 일반적인 임베딩 품질·격리 보증으로 해석하지 않는다.
- 재부팅·OS 파일 캐시 비움은 하지 않았다. 측정 전 모델 파일 해시 계산으로 캐시가 데워질 수 있다.
- 다른 앱이 실행 중인 로컬 Windows 환경의 표본이다. Desktop end-to-end·서버 worker·메모리 최소 사양의
  근거로 일반화하지 않는다. 10ms 표본 사이 메모리 peak를 놓칠 수 있다.
- 비교기는 필터된 exact cosine 검색이다. 실제 Chroma/pgvector/ANN의 종단 경계를 검증하지 않는다.
- A의 ONNX export·캡처 설정 검토와 B의 라벨 검토, 후보 채점 전 허용값 합의가 남았다.
  **통과 기준을 이번 결과에 맞춰 확정하지 않았으며 #391·ADR25 미결 3은 열린 상태로 유지한다.**
- 원격 저장은 런타임 지문별 새 embedding set을 사용하며 기존 context·색인을 교체하지 않는다.

재현 명령과 측정 절차는 [평가 안내](README.md#b의-로컬-pytorch-기준선-캡처)를 따른다.
