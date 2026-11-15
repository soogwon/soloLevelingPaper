# #396 B 입력 해시·제공자 전송 gate 준비

기준 main `97dc4bc`. 제품 도구·실제 제공자·A 원장·동의 DB에는 연결하지 않았다.

## 원격 질문 해시

`question_input_hash`는 `remote-question-v1:` 해시를 반환한다. 기존 로컬 v1 해시는 변경하지 않는다.
인증·고정 binding의 owner/context/paper/version/publication/revision/set/모델 지문/게시 정책과
실제 실행 질문, 유효 focus·strict_pages·top_k·처리 설정을 결합한다. 필터 집합의 순서와 JSON 키
순서만 정규화하며 질문의 공백·Unicode·CRLF는 바꾸지 않는다. 중복/무효 필터는 기존 타입에서 거부한다.

처리 설정은 provider_profile_id, generation_model/prompt_version, semantic_model/prompt_version,
budget_policy_version의 고정 키다. 실제 키 이름은 코드의 CONFIG_KEYS를 따른다. 신뢰하는 서버 설정에서
만들어야 하며 API key나 임의 설정은 포함하지 않는다. 비활성 단계도 명시적인 정책 식별값으로 표현한다.
동의 기록은 이 해시와 고지/revision에 결합하되, request key의 유일성에 해시를 추가하지 않는다.
같은 owner+operation+request key의 다른 해시는 충돌이며 새 실행 허용이 아니다.

## provider gate

`ProviderDispatch`는 같은 DispatchIntent의 여러 단계가 공유하는 전송 경계다. 생성/보완/coverage/
의미 검증/번역의 **실제 HTTP 전송 1회마다** send를 호출해야 한다. transport 안에서 숨은 재시도나
리다이렉트·다른 제공자 fallback을 해서는 안 된다.

- 호출자는 모델별로 검증된 입력 토큰 상한과 실제 요청에 적용할 출력 상한을 전달한다. 문자 수 추정이나
  클라이언트가 준 수치를 안전한 토큰 상한으로 인정하지 않는다. 추정기·payload 일치 검증은 별도 구현이다.
- intent는 인증 owner·영속 execution·현재 lease·입력 해시·보관/AI consent·고지에 결합한다.
- `dispatch_key`는 논리 실행 내 전송 의도를 식별하는 **재시작에도 유지되는 키**다. authorize 응답 유실 때
  새 키를 만들지 않는다. 시도 ID는 A 원장이 발급한다. 로컬 `_used` 집합은 영속 멱등성의 대체물이 아니다.
- A의 authorize는 현재 세대·동의/철회/고지·운영 중지·예산을 원자 검사/예약하고 같은 dispatch key의
  전송 허가를 한 번만 소비해야 한다. 이미 소비/불명확한 허가는 다시 반환하지 않고 상태 조회로 복구한다.
- 허가 거부/원장 장애/잘못된 scope는 transport를 호출하지 않는다. 허가 응답이 유실될 수 있는 장애는
  controller를 차단하며 자동 재요청하지 않는다. A의 보류 상태를 로컬에서 환급하지 않는다.
- transport 예외·없거나 잘못된 usage는 unknown으로 보고하고 같은 controller의 후속 전송을 차단한다.
  usage 보고 자체가 실패해도 provider를 다시 호출하지 않는다. 기존 검증 완료 내용의 보존/표시는
  상위 pipeline 책임이며, 이 모듈은 새 미검증 payload를 답변으로 공개하지 않는다.
- 실제 사용량이 상한을 넘으면 알려진 사용량을 사실대로 보고한 뒤 후속을 차단한다.
- observe 응답 유실 후 unknown 보고가 이미 정산한 known usage를 덮어쓰면 안 된다. A 원장은
  단조 상태 전이·중복 정산 방지를 보장해야 한다. stale worker의 결과 게시 허가는 복구하지 않는다.

## 실제 연결 전 A·B·C 확인

| 담당 | 필요한 포트/검증 |
|---|---|
| A | authorize/observe/unknown 영속 adapter, dispatch key 유일성, 동시 예산·동의 철회 경합, 늦은 usage·재시작 |
| B | 최종 payload와 모델별 상한 검증기, 모든 실제 전송 지점 주입, 기존 번역 재시도와 전체 예산 공유 |
| C | 같은 실행/키 상태 조회·동의 전달·고정 내부 오류의 wire 매핑, 부분 답변 품질과 제한 상태 분리 |

포트는 B 내부 연결 초안이다. 이 객체를 만들었다는 사실은 인증·동의·예산의 증명이 아니다.
기본 허용 원장은 제공하지 않는다. 아래 선택적 transport를 주입하지 않은 기존 로컬 제공자 동작은 유지한다.
원장 없이 프로세스 카운터로 유료 기능을 공개하지 않는다. 정책 숫자·공개 API/사유 코드는 확정하지 않는다.

## 검증 범위

해시 scope/config/focus·질문 보존, 단계마다 허가 재검사, 허가 실패 전송 0회,
timeout/usage 누락/정산 실패 후 추가 전송 0회, 중복 dispatch key·다른 owner 허가 거부를 대역으로 확인한다.
대역의 원장 상태/재시작 테스트는 실제 DB 원자성·동시성·영속 복구를 증명하지 않는다.
제품 AI·DB·Auth0·Desktop 테스트는 하지 않는다. #396은 실제 원장·pipeline·C 연결까지 열어둔다.

## 선택적 Responses transport 연결

`GatedResponsesTransport`를 ClaimGenerator(생성/보완), SemanticVerifier, TranslationProvider에
명시적으로 주입할 수 있다. 제품 factory나 원격 allowlist에는 아직 등록하지 않는다.
기존 요약·퀴즈 adapter는 이번 연결 범위가 아니다. 질문 항목 보완도 기존 repair_claims 경로를
사용하므로 현재 stage는 repair다. 별도 coverage 계수 정책은 실제 파이프라인 연결 시 합의한다.

prepare 포트는 stage와 최종 payload 사본/해시를 받아 **영속적으로 안정적인 dispatch key와
해당 모델의 검증된 입력 토큰 상한**을 공급해야 한다. 이 구현은 상한을 추측하거나 자동 key를
만들지 않는다. 원장과 prepare 구현이 없으면 제품에 연결하지 않는다. payload 해시는 로컬
변이 검사용이며, 처리 설정/동의/input hash와의 신뢰 연결은 실제 prepare/원장 adapter 책임이다.

송신 payload는 동결 사본으로 model/store/max_output_tokens를 설정과 대조한다.
authorize 뒤 redirect 없는 requests.post 1회, usage 추출, 응답 close, observe 순으로 처리한다.
HTTP 오류/JSON 오류/timeout/usage 누락은 unknown으로 차단한다. 일부 HTTP 실패가 실제 미전송인지
입증하는 분류는 아직 없으므로 보수적으로 예약을 유지한다. 제공자 오류 본문은 공개하지 않는다.
성공 usage가 있는 응답은 먼저 정산하고 기존 생성/번역/의미 파서가 그대로 검증한다.

원격 DispatchError는 번역 서비스의 재시도 가능한 TranslationProviderUnavailable로 바꾸지 않는다.
unknown/예산 거부 뒤 청크 재시도·다음 청크로 유료 전송이 진행되지 않고 상위 실행 경계로 전달된다.
실제 실행 제한 응답과 검증된 기존 일부 답변 보존은 A/C 계약 연결 시 추가 검증해야 한다.
명시적 transport 미주입 시 기존 로컬 동작/번역 재시도는 변경하지 않는다.

대역으로 생성·보완·의미 검증·번역의 gate 통과, unknown 후 전송 1회/추가 0회,
전송 전 거부 0회, payload 변이 차단 및 기존 parser 사용을 검증한다.
이는 영속 원장의 재시작/동시성 또는 유료 서비스 공개 증명이 아니다.
