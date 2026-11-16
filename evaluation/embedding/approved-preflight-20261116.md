# #391 승인 반영·자료와 환경 사전 점검 — 2026-11-16

기준 main: `5edb42c`. 브랜치: `soogwon/docs/issue-391-approved-freeze`.
[A 승인 댓글](https://github.com/soogwon/soloLevelingPaper/issues/391#issuecomment-6918803245)을 반영했다.
승인 시각은 도구에서 제공하지 않아 임의 기입하지 않았다. 아래 관측 시각과 구분한다.

**자료·정책은 해시로 고정했지만 실행 환경 최종 동결은 미완료다.**
현재 환경 지문은 미설치 항목을 포함한 관측 기록이며, 실행 준비 완료를 뜻하지 않는다.
모델·세션 생성·추론·export·다운로드·API·DB 접근은 하지 않았다. 기존 산출물도 수정하지 않았다.

## 승인값

| 항목 | 승인 기준 |
|---|---|
| 범위·출처 혼입 / 차원·비유한·0벡터·설정 불일치 | 0건. 기존 L2 검사 유지 |
| FP32 좌표 오차 | 청크·질의 각각 최대 절대 오차 ≤1e-4 |
| 근거 회수 | 질의별 그룹 recall 감소 0, 완료→미완료 0건 |
| 페이지 연결 | head AND tail, 그룹 recall 1.0 |
| 입력 관측 | ID별 토큰 수·잘림 플래그 불일치 0건. 누락을 일치로 세지 않음 |
| 로컬 채택 목표 | ONNX/PyTorch 최대 process-tree peak RSS ≤0.80, warm 중앙값 비율 ≤1.10 |
| 반복·집계 | 각 backend fresh 6회×warm 3회. process별 warm 중앙값을 구한 뒤 6개 중앙값의 중앙값 |
| 순서 | `(PyTorch→ONNX), (ONNX→PyTorch)`를 3번 반복: 총 6쌍·12 process |
| 예외 | cold는 필수 기록·단독 실패 gate 아님. 기준선 그룹 충족이 흔들리면 비교 보류 |

순서는 [B 원제안](https://github.com/soogwon/soloLevelingPaper/issues/391#issuecomment-6917195081)과
A가 명시한 **backend별 6회**를 충족하도록 풀어 적은 실행 해석이다. A 댓글의 문구를 그대로 인용한 것이 아니다.
‘교대 3쌍’을 총 6 process로 줄이지 않으며, 실행 계획에는 이 12 process 순서를 명시해야 한다.
실패 실행은 보존하고 사유·재측정 여부를 기록한다. 자동 재측정은 허용하지 않는다.
구체적인 수집 실패 재측정 상한은 실행 계획에 추가 고정한다.
FP16/INT8에는 이 오차를 재사용하지 않는다. worker 실제 메모리 한도·동시성·시간 예산·25% 여유율은
별도 A/Soo 운영 결정이다. 통과해도 기존 context·embedding set·publication은 교체하지 않는다.

## 해시 기록

SHA-256의 canonical 값은 기존 `comparison.digest`의 정렬 JSON/UTF-8 규칙을 사용한다.
raw 파일 해시와 혼동하지 않는다. [정책 JSON](approved-policy.json)은 실행기가 아니라 승인 기록이다.
[점검 JSON](approved-preflight-20261116.json)에 파일별 해시·환경·설정 원본을 함께 보존했다.
환경 해시는 선택한 패키지 버전·Python 실행 파일·OS/CPU·일부 스레드 환경값·별도 psutil 파일의 지문이다.
전체 패키지 전이 의존성·설치 바이너리 전체·전원/부하 상태를 봉인한 재현 환경 해시는 아니다.
raw 파일 해시는 당시 바이트 기준이다. Git 줄바꿈 변환 후 달라질 수 있으므로 canonical 자료 해시와 구분한다.
문서 보완으로 정책·문서 해시를 갱신했으며, 자료 해시와 최초 관측 환경 해시는 그대로 보존했다.

| 대상 | SHA-256 |
|---|---|
| reviewed 자료 canonical | `6df95d142a81c36308057f0158de718e514ba8e0c8bdb149f206f7e09c0953ef` |
| cases.json raw | `0b7117ffaaf241ad387d945947eea583a9dca46c7d65da590c2b3b2313c78659` |
| 승인 정책 canonical | `fd17795e8b111cee15811a1c366314ecfefac7fc4a479a6b178ce053667934d3` |
| 현재 관측 환경 canonical | `6cb89be7ed6bedd04b7911d436d5090676f1c760b0b2ffe51dc1e5cf2c26f27e` |
| PyTorch 가중치·설정 묶음 | `715d2c3d56264ca48e372fceeb7ba2ac9d41d30e259386c1d81008d3df50164a` |
| tokenizer·입력 설정 묶음 | `de9aa76bbd78daf2c9c0ae6a205413ace7c5c213dacc8bea4b77e6a133a29297` |

청크 17개·질의 12개, 대안 OR·페이지 연결 AND·범위·k는 변경하지 않았다.
입력 설정 묶음은 기존 `input_artifact_digest`, PyTorch 묶음은 기존 `artifact_digest`로 계산했다.
모델 디렉터리 파일 10개의 상대 이름·크기·해시도 기록했다. 이전 PyTorch 묶음 해시와 일치한다.
이 일치는 과거 v1/v2-draft 실측을 새 v3 기준선으로 재사용할 근거가 아니다.

## 실제 산출물·설정 확인

검사 범위는 기존에 사용한 로컬 HF snapshot
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` /
`e8f8c211226b894fcb81acc59f3b34ba3efd5f42`다. 다른 ONNX 후보 경로는 제공되지 않았다.
컴퓨터 전체에 ONNX가 없다고 단정하지 않는다.

| 대상 | 읽기 전용 확인 결과 |
|---|---|
| 모델 파일 | model.safetensors 있음. tokenizer.json·관련 설정 있음 |
| modules / pooling | Transformer + Pooling, mean=true, 차원 384 |
| 길이 | sentence_bert_config.json의 max_seq_length=128. tokenizer 고유 model_max_length와 구분 |
| tokenizer 설정 차이 | tokenizer_config의 do_lower_case=true, sentence_bert_config는 false. tokenizer의 model_max_length=512. 실제 로더가 채택하는 경로는 미확인으로, 파일만 보고 인코딩 일치를 선언하지 않음 |
| ONNX·외부 weight | 지정 snapshot에 .onnx 0개. graph/external weight manifest·producer·opset·dtype 검증 불가 |
| 개발 Python / CPU | Python 3.12.14 / Windows 11 / i7-10750H / 논리 CPU 12개 |
| 설치된 관련 패키지 | torch 2.14.0, sentence-transformers 6.1.0, transformers 5.17.0, ORT 1.30.0, numpy 2.5.3, tokenizers 0.23.2 |
| 없는 관련 패키지 | 현재 개발 환경에 optimum·optimum-onnx·onnx·psutil 없음. 설치하지 않음 |
| 별도 측정 도구 | 과거 기준선의 runtime/capture-tools에 psutil 7.2.2 있음. 현재 sys.path에는 없으며 파일 해시만 기록 |
| 스레드 / provider / 모드 | 코드 의도는 CPUExecutionProvider, intra/inter-op=1, ORT_SEQUENTIAL. 현재 OMP/MKL 환경값은 미설정 |
| 실제 세션 / FP32 / 입력 인코딩 | 세션·모델을 생성하지 않아 미확인. 설정 파일·코드 의도를 실제 런타임 관측으로 대체하지 않음 |

`artifacts.ort_session_options`와 `observe_ort_session`은 위 세션 계약을 검사한다.
`onnx_capture`는 export=False로 로컬 파일을 요구한다. 이 점검에서는 이 경로를 실행하지 않았다.
L2·batch 8·FP32·OMP/MKL 1도 캡처 목표이며 현재 실행 관측값이 아니다.

## 다음에 필요한 것

1. 고정 FP32 ONNX graph·외부 weight·export 도구/버전/opset과 Optimum 버전을 확보한다.
   tokenizer·입력 설정 해시를 대조하고, graph 타입·양자화 여부도 정적으로 확인한다.
2. 공통 측정 환경과 psutil 경로를 명시해 준비한다. 변경 후 새 환경 해시를 기록하고 이번 관측은 보존한다.
3. 실행 승인 후 실제 CPU provider·스레드·모드·입력 인코딩을 관측·대조한다.
   실행 순서/집계·토큰 관측 일치·기준선 안정성·승인값 판정 연결도 준비해야 한다.
4. PyTorch v2 재캡처 → 동일 환경 ONNX 캡처 → 실패 포함 비교 보고서 순으로 진행한다.

## 검증

기존 평가·산출물·PyTorch/ONNX 캡처 대역 테스트 **94개 통과**. 별도 측정 도구 경로는
테스트 명령의 PYTHONPATH에만 적용했으며 제품·영구 환경 설정은 바꾸지 않았다.
자료 검증·정책 수치·반복 순서·파일/환경 해시·문서 링크·공백도 검사했다.
제품 코드·기존 검사·DB·context는 변경하지 않았다. 모델 실행·커밋·푸시는 하지 않았으며 #391은 열린 상태다.
