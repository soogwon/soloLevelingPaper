# #517 처리 결과 staging

9번은 게시용 parse/translation/embedding 테이블을 채우기 전의 **비공개 불변 결과 저장**이다.
`server_processing_result_staging`에 schema_version=1 결과 JSON과 canonical SHA-256을 저장한다.
원문/번역, 페이지·인쇄 페이지 라벨·섹션, 빈 페이지, 번역 설정, 전체 모델 지문·벡터,
owner/job/원문 버전/리비전/lease/PDF hash·크기를 보존한다. PDF bytes·서비스 키는 저장하지 않는다.

## 저장 계약

- 서버 설정의 PDF/페이지/청크 제한, 번역 설정, 임베딩 지문, 명시적 `max_staging_bytes`를 요구한다.
- 완전한 prepared 결과만 저장한다. 부분 번역은 현재 저장하지 않으며 저장 번역 재사용도 후속이다.
- 논문→job 순서로 잠그고, 현재 lease·동의·업로드·원문 식별자와 미정산/초과 사용량을 재검사한다.
  job 독점 잠금은 새 예약/다른 저장과 직렬화한다. 확인은 동일 DB transaction 안에서 이루어진다.
- 같은 owner/job/lease의 동일 payload는 재생한다. 내용이 다르면 `processing_staging_conflict`로
  거부하고 기존 결과를 덮어쓰지 않는다. jsonb가 음의 0을 보존하지 않아 벡터 0 표현을 정규화한다.
- 저장 직후 실제 시각으로 lease를 다시 확인한다. 실패하면 transaction 전체를 rollback한다.
  commit 이후 응답이 유실돼도 동일 결과로 재시도할 수 있다. 새 실행 세대에는 이전 결과를 반환하지 않는다.
- `load(source)`는 현재 권한과 전체 source identity를 검사하는 내부 조회다. 공개 API가 아니다.
- DB는 INSERT 시 살아 있는 논문·lease를 확인하고 UPDATE를 거부한다. 앱 저장소는 추가로 동의·usage·결과를 검증한다.
  DB 관리자 SQL로 앱 검증을 우회한 payload가 유효하다는 보장은 하지 않는다.
- job 삭제 FK cascade로 staging을 삭제한다. 기존 논문 삭제 정리 경로도 이 cascade를 사용한다.

worker는 heartbeat가 살아 있는 동안 prepared 결과를 저장한다. 저장 오류는 성공으로 반환하지
않고, 저장 후 heartbeat 오류가 나면 결과 반환을 거부한다. 이때 이미 커밋된 staging은 비공개로
남을 수 있다. **staging 존재만으로 후속 게시를 허용하지 않으며 10번에서도 현재 권한을 다시 검사해야 한다.**

`create_processing_worker`는 staging 저장소를 기본 조립하며 `max_staging_bytes`를 필수 입력으로
추가한다. 직접 `RemoteProcessingWorker`를 만드는 기존 테스트/호출은 선택적 result_store로
호환한다. staging receipt에는 hash·replay 여부만 있고 원문은 repr에 포함하지 않는다.

## A/B/C와 남은 범위

- A: 현재 세대/동의/usage·원자적 저장·멱등 재시도·삭제 격리. 실제 배포와 운영 보관 기간/정리 정책은 별도다.
- B: prepared 자료와 설정/출처/벡터 검증 재사용. 현재 작업의 전체 결과 저장이며 청크별 중간 checkpoint가 아니다.
- C: stored는 succeeded/completed/searchable이 아니다. active pointer/job 성공/공개 UI는 변경하지 않는다.

10번에서 이 payload를 게시용 정규 테이블·pgvector로 옮기고, 전체 출처/권한/정책/정산 상태를
재검증해 publication·active pointer·작업 성공을 원자적으로 전환해야 한다. 처리 설정의 접수 시점
영속 snapshot·완료 번역 재사용·lease 회수 후 staging 재사용/정리도 미구현이다.
실제 PDF/API/모델/R2/배포 없이 합성 결과와 격리 PostgreSQL로 검증한다. #517은 열어둔다.

PR 생성 후 재검토에서 조회 시 번역 설정·모델 지문도 저장소의 기대값과 대조하도록 보완했다.
저장 후 보관 동의 철회·취소·삭제, 다른 파싱 리비전, 변경된 prompt/모델 지문으로 조회하면
기존 row가 남아 있어도 반환하지 않는다. row 정리는 별도 정책이며 조회 차단과 구분한다.
