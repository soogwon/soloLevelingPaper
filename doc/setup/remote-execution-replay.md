# #395 동일 실행의 읽기 전용 복구 경계

`application/remote_replay.py`는 인증된 서버 owner·operation·request key로만 기존 실행을 조회한다.
input_hash는 유일 키가 아니라 일치 검사용이다. 같은 키의 다른 hash/context는 충돌이며 새 실행을
만들지 않는다. 조회 실패·결과 손상도 생성·재접수·다른 키로 대체하지 않는다.

ExecutionReader는 A의 영속 조회 adapter가 구현할 **포트 초안**이다. 현재 조회 권한과 원자 저장된
결과를 같은 읽기 snapshot에서 반환해야 한다. 메모리 대역은 실제 영속 멱등성 증명이 아니다.
각 호출 전 인증/owner 매핑은 C/#390 경계가 수행하며 이 DTO 생성은 인증을 보장하지 않는다.

저장 결과는 정확한 UTF-8 JSON과 SHA-256을 함께 읽는다. B는 hash·공개 필드·context·기존 주장/인용
구조를 검증하고 복사된 dict를 반환한다. 의미 검증을 다시 실행하거나 model_checked를 재부여하지 않는다.
원자 저장 시의 원문/출처 일치 검증과 현재 조회/삭제 권한은 A adapter가 보장해야 한다.
SHA-256은 인증 서명이나 서버 저장의 진위 증명이 아니다.

기존 partial·verification_level·주장/근거 ID·verification_history를 그대로 보존한다.
현재 동의/한도와 조회 권한은 구분하며 budget limited/usage_unknown만으로 결과를 지우지 않는다.
실행 상태·cancel_requested/cancelled·결과 가용성을 구분하고 완료라도 결과가 없으면 상태만 반환한다.

이 코드에는 submit/run/provider/save 포트가 없고 제품 도구에 등록하지 않는다.
실제 원격 접수·답변/근거/usage의 원자 저장, A 조회 구현·C 공개 코드 매핑, Desktop 재시작·두 계정·
동시 요청 검증은 남아 있다. 기존 SQLite 실행 저장을 원격으로 재사용하지 않는다.
가짜 결과를 새 검증 결과로 만들거나 타임아웃을 근거 부족으로 해석하지 않는다. #395는 열어둔다.
