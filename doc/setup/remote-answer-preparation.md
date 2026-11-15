# #390 원격 검색에서 답변 검사까지 — 저장 전 내부 준비

`RemoteAnswerPreparationService`는 인증 context를 해석하는 검색 서비스와 원격 source reader,
기존 generator/semantic service를 받아 기존 `AnswerService` 검사를 재사용한다.
MCP 도구·공개 API·실제 저장소를 등록하지 않는다. 실제 생성기 사용 시 #396의 gate가 필요하다.
이번 테스트는 합성 생성/판정뿐이며 실제 API를 호출하지 않는다.

## 흐름

인증 context resolver → 고정 binding 검색 → 저장 원문/번역 대조 → 기존 문장 보충 →
생성·구조/추출/의미 검사 → 임시 evidence buffer → **PreparedRemoteAnswer(not_saved)**.

기본 맥락 생성이나 active publication 추종은 없다. 검색 확대 때도 매번 인증 resolver를 거치고
처음 고정한 전체 binding과 대조한다. 변경/삭제/권한 거부를 새 맥락으로 대체하지 않는다.
호출별 binding과 buffer는 독립적이며 서비스 인스턴스에 결과/owner 캐시를 두지 않는다.

`RemoteContinuationReader`는 기존 source 경계에서 추출한 공용 연결이다. 페이지·섹션·리비전
검사와 #108 허용 구절 검사는 그대로 유지한다. 기본 [6]에서는 다음 페이지의 연결 문장만
생성/검증/임시 인용에 전달하고 정렬 없는 번역은 null로 둔다. strict_pages의 범위 밖 후보는 거부한다.

## 저장 전이라는 의미

- 내부 response/evidence는 **아직 저장하지 않은 제안**이다. 성공 status가 있어도 공개 반환하면 안 된다.
- 기존 AnswerService의 writer 포트는 이 경로에서 메모리 buffer만 받는다. 기존 evidence_save 진단 단계도
  이 내부 경로에서는 buffer 처리를 나타내므로 DB commit 성공의 증거로 쓰면 안 된다.
- 검증 이력 saver는 연결하지 않는다. 기존 verifier의 미저장 이력 상태를 saved로 위장하지 않는다.
- A의 원자 저장 adapter가 owner/context/고정 source·publication·lease·tombstone과 현재 권한을
  다시 검사하고 답변·근거를 commit한 뒤에만 공개 응답으로 전환해야 한다.
- 준비 결과/hash는 권한 증명이 아니며 mutable 내부 필드도 A 검사를 우회하는 신뢰 입력이 아니다.

## 검증과 남은 범위

대역 source/검색/생성/판정으로 고정 출처, 문장 완성, strict_pages, 잘못된 참조·의미 거부,
빈 검색, 검색 확대 중 publication 변경을 검사한다. 원래 로컬 AnswerService 저장 동작은 바꾸지 않는다.
검사 기준을 완화하거나 최신본/로컬 writer fallback을 추가하지 않았다.

A의 동일 snapshot source reader·실제 vector 집합 게시 검증·lease 결합 원자 답변/근거 저장,
실제 runtime 지문, C의 인증 요청/응답/호스트 연결은 남아 있다. #390은 열어둔다.
