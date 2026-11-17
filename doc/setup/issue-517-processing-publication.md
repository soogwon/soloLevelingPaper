# #517 처리 결과 정규화와 원자적 게시

## 구현 범위

검증된 private staging을 다시 읽어 현재 동의·입력·실행 세대·usage 상태·결과 해시를 검사한다. 원문 청크, 번역 청크, 전체 모델 fingerprint를 가진 pgvector 행, 섹션/페이지 메타데이터와 번역 설정을 정규 저장소에 기록한다.

하나의 PostgreSQL 트랜잭션에서 ready 전환, publication 생성, active pointer 교체, job 성공과 lease 해제를 수행한다. 어떤 단계든 실패하면 정규 행과 성공 상태를 함께 롤백하며 staging은 유지된다. worker가 처리 시작 시 읽은 active pointer와 게시 직전 값이 다르면 충돌로 거부한다. 동일 staging·job·실행 세대의 재호출은 기존 publication을 반환하며 active pointer를 되돌리지 않는다.

heartbeat는 게시 직전에 정지하여 성공 처리의 lease 해제를 실패로 오인하지 않는다. 게시 트랜잭션은 실제 시각으로 lease 만료를 다시 검사한다. 완료 후 재조회도 현재 동의와 입력 연결을 검사한다.

## 검증

- 합성 typed 결과 복원, 잘못된 staging 및 해시 거부 단위 테스트
- PostgreSQL·pgvector 통합 테스트: 실제 벡터 검색/페이지 필터/타 owner context 거부
- 동일 요청 동시 게시의 단일 publication, 성공 후 재조회
- 벡터 누락과 게시 직후 오류의 전체 롤백 및 재시도
- receipt/pointer 충돌, 취소·동의 철회·lease 만료 차단
- 실제 PostgreSQL 테스트는 `SOLO_TEST_POSTGRES_DSN`을 사용하는 격리 CI에서 실행한다.

## 역할별 확인과 남은 범위

- A: 같은 DB 연결에서 정규화·게시·job 완료를 원자 처리한다. migration 0037을 추가하며 기존 ready 불변 가드를 유지한다.
- B: staging의 전체 embedding spec과 벡터를 기존 writer/search 포트로 연결한다. 실제 모델 품질·지연·비용 실측은 포함하지 않는다.
- C: 명시적으로 생성한 context로 게시된 근거 검색을 검증한다. 제품의 context 생성/원격 질문 연결은 후속 #390/#395 범위다.

실제 worker 배포, R2/PDF 전송, 번역·질문 API 호출 및 실제 모델 실행은 하지 않는다. worker 운영 루프·재시도 정책과 실제 PDF 종단검증, 두 계정 종단 격리 및 비용 실측은 남아 있으므로 #517은 닫지 않는다.
