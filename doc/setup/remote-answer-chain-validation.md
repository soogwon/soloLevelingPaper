# B 실제 저장 답변 경로 검증 (#397)

선행 PR #461의 factory를 사용하는 후속 테스트입니다. 원격 공개·Auth0·Desktop 전체 검증은 아닙니다.

| 사례 | 실제 구간 | 합성 대역 | 확인 사항 |
|---|---|---|---|
| [6], strict [6], [6,7] | PostgreSQL 검색·source·저장 | 인증 owner, 임베딩, 생성 | 높은 점수의 다른 계정/범위 밖 후보 제외, 저장 인용의 원문·리비전 유지 |
| 새 publication 뒤 재조회 | 원자 저장, 새 publication, 읽기 복구 | 모델 | 같은 실행·payload 유지, 재조회 임베딩/생성 추가 0회 |
| 다른 owner | 실제 resolver | 인증 identity | 임베딩/생성 전 동일한 비공개 오류 |
| 생성 직후 삭제 | tombstone·저장·재조회 | 생성 | 늦은 저장 근거 0건, 재조회 불가 |
| 정상·실패 진단 | 내부 진단 출력 | 합성 질문/원문 | 질문·본문 canary 비포함 |

로컬에 DSN이 없으면 skip되므로 통과 증거로 사용하지 않습니다. CI의 일회용 PostgreSQL+pgvector에서
`tests/integration/test_persisted_answer_chain_postgres.py`를 실행합니다. 다른 기존 통합 테스트 수와 구분합니다.

## 남은 범위
- 합성 원문은 완결 문장이며 실제 PDF의 페이지 경계 문장·번역 정렬 품질/의미 판정 증명은 아닙니다.
- provider attempt는 모델 대역이므로 0건입니다. 실제 유료 모델과 영속 gate를 함께 쓰는 경로는
  #396의 단계별 원장 테스트와 구분하며 이번 결과로 전체 전송 경로를 통과 처리하지 않습니다.
- 두 계정 Auth0→MCP, 실제 호스트 재접속/응답 유실, HTTP·프록시 로그 표본은 별도입니다.
- 운영 제한값, 정책 영속 연결, 삭제 복원/물리 정리와 #399/#400 준비가 남습니다. #397을 종료하지 않습니다.

## 추가: 영속 gate와 실제 답변 저장의 단일 흐름

`tests/integration/test_gated_answer_chain_postgres.py`는 기존에 분리했던 답변 저장과 원장 경계를
같은 실행에서 연결하는 추가 8개 사례다. 기존 표의 모델 대역/provider attempt 0건 기록은
기존 `test_persisted_answer_chain_postgres.py`의 범위로 유지한다.

| 구간 | 이번 실행 방식 |
|---|---|
| owner 인증 공급·질문 임베딩 | 합성 대역. 실제 Auth0·모델 실행 아님 |
| context·검색·출처 | 실제 PostgreSQL resolver·pgvector·고정 source adapter |
| 생성·의미 검사 | 실제 OpenAI adapter와 기존 parser/검사기. HTTP 응답은 합성 fixture |
| 전송 승인·예산·정산 | 실제 execution scope·Postgres 원장·실행 예산 검사 |
| 답변·근거 저장과 재조회 | 실제 원자 저장소와 읽기 전용 복구 |

- [6], strict [6], [6,7]에서 generation/semantic_verification 2건이 observed로 정산된 뒤에만
  model_checked 답변·인용이 저장된다. 합성 의미 판정이며 실제 사실성·페이지 경계 품질 증명은 아니다.
- 같은 실행의 완료 결과를 반복 조회해도 HTTP·임베딩·provider attempt가 늘지 않는다.
- 생성 후 의미 검사 호출 예산 소진은 제한 오류로 전달되고 미검증 초안을 완료/근거로 저장하지 않는다.
- 시간 예산 만료는 HTTP 0회다. timeout은 unknown 예약을 유지하며 새 service/전송 키도 추가 송신할 수 없다.
- 생성과 의미 검사 사이의 AI 동의 철회·tombstone은 두 번째 HTTP와 저장을 차단한다.
- 정상·timeout 진단에 합성 질문/원문/번역/API key·원시 오류 canary가 없는지 확인한다.

연결 검사 중 실행 예산 초과가 상위 B service에서 일반 preparation failure로 변환되는 것을
단위 테스트로 재현했다. `RemoteAnswerExecutionError`와 고정 내부 `DispatchError`를 구분해
전달하도록 수정했다. 임의 provider/DB 예외는 기존 고정 오류로 계속 비식별화한다.
공개 MCP/HTTP 사유 코드를 새로 정의하거나 실행 상태를 임의로 완료/실패 전환하지 않는다.
제한 시 worker 상태 전이와 C 응답 연결, 이미 검증된 일부 답변의 별도 보존 정책은 여전히 후속이다.

네트워크는 requests.post 차단과 명시적 합성 post 주입을 함께 사용한다. 실제 API 키·모델·사용자
논문/DB·운영 설정은 사용하지 않는다. fixture의 예산값/전송 키/토큰 상한은 테스트용이며 운영
정책 기본값이나 모델별 안전한 토큰 추정의 증명이 아니다. A DB 파일·C 도구 등록은 바꾸지 않는다.
로컬 DSN이 없으면 DB 8개를 건너뛰고 CI 일회용 PostgreSQL/pgvector에서 실제 실행을 확인한다.
#397 및 #395/#396의 공개·호스트·정책 관련 완료 조건은 유지한다.
