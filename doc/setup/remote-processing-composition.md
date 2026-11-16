# #517 서비스 번역·고정 모델 조립

`ProcessingStagesFactory`는 서버 내부 설정을 받아 #539의 `stages_factory`로 주입한다.
작업마다 파서, 번역 서비스, ProviderDispatch, PostgreSQL 원장 adapter, 고정 PyTorch
임베더를 새로 만든다. 공유 전송의 used 집합이나 다른 job의 heartbeat를 재사용하지 않는다.

## 명시적 서버 입력

- 서비스용 OpenAITranslationConfig(API 키는 repr에서 제외). 사용자 요청·DB에서 개인 키를 받지 않는다.
- 현재 policy_version/window_key와 DB의 사용자/서비스 예산 행.
- 모델 경로·EmbeddingSpec, batch_size, PDF/페이지/청크 제한, 번역 재시도 정책.
- 모델별 전체 요청의 안전한 입력 토큰 상한 추정기. 누락/잘못된 값은 전송 전에 거부하며 임의 숫자로 대체하지 않는다.
- 검증에서는 post/model_factory 대역을 사용한다. 생략 시 실제 구현이므로 외부 호출 승인과 실행 환경이 필요하다.

번역 전송 키는 job + 청크 순서 + 번역 시도의 논리 슬롯으로 고정된다. lease와 파싱 리비전을
다시 생성해도 같은 슬롯은 재예약되지 않는다. payload가 달라져도 같은 슬롯을 새 전송으로
우회하지 않는다. 현재는 저장 번역 재사용이 없어 재claim 시 기존 슬롯에서 안전하게 중단된다.
따라서 이것을 완성된 재시작/재개 구현으로 표현하지 않는다.

## A/B/C 검토

- A: DB에서 생성한 intent만 사용하며 owner/job/lease가 source와 같아야 한다. 영속 원장과
  현재 권한 callback이 전송 직전 검사한다. 게시·성공 전이는 여기서 하지 않는다.
- B: 실제 파서/청커, 기존 번역 provider/gate/품질 검사, 고정 모델 adapter를 연결한다.
  timeout·usage 누락·원장 거부 뒤 모델은 로딩하지 않는다. API 숨은 재시도는 추가하지 않는다.
- C: 결과는 prepared 또는 미완료 내부 결과다. 공개 completed/searchable로 표시하지 않는다.

## 검증과 남은 범위

합성 PDF, HTTP 응답, 모델 factory, 사용량 원장으로 정상 처리·불명 사용량·거부·잘못된
토큰 상한·타 작업 intent·새 lease/리비전의 동일 슬롯·임베딩 중단 callback을 검사한다.
이 대역 검증은 실제 모델/API/DB 조립의 종단 실행이나 배포 검증이 아니다.

실제 실행 전 승인 모델 artifact/런타임, 검증된 토큰 상한 추정기, 작업별 처리 설정의 영속 고정,
worker bootstrap/이미지, 저장된 번역 재사용, staging 저장·원자 게시·공개 상태를 연결해야 한다.
서비스 키를 코드/커밋/문서에 기록하지 않는다. 실제 운영 예산과 실행 한도는 별도 승인 대상이다.
