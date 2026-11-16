# #368 C 삭제 확인·상태 안내 대역

기준은 #473의 승인된 60/30/30 정책과 #474의 물리 정리 계약이다.
`interfaces/mcp/deletion_guidance.py`는 고정 한국어 안내와 읽기 전용 카드/텍스트 미리보기다.
제품 MCP 도구·HTTP 삭제 경로·실제 Desktop 카드에는 아직 등록하지 않는다.

## 의도와 상태

로컬 삭제, 동기화 중지, 외부 전송 동의 철회, 서버 논문 삭제, 계정 삭제를 별도로 표시한다.
서버 논문 삭제 확인에는 공통 `DELETION_NOTICE_KO`를 재사용한다. 계정 삭제는 논문 삭제
접수에 대입하지 않고 별도 절차가 필요하다고 안내한다. 미리보기 버튼은 비활성 상태다.

| 서버 조회 상태 | C 표시 | 다음 행동 |
|---|---|---|
| blocking | 접근 차단·정리 대기 | 같은 요청 조회 |
| cleaning | 자료 정리 중 | 같은 요청 조회 |
| failed | 일부 정리 미완료 가능, 접근 차단 유지 | 같은 요청 조회 |
| complete | 활성 저장소 정리 완료, 백업 삭제 미확인 | 같은 요청 조회 |
| complete + 신뢰하는 백업 삭제 확인 | 백업 포함 삭제 확인, 최소 표식 별도 보존 | 상태 확인 |
| 미존재·타인·다른 대상/키 | 조회 불가 | 요청 확인, 자동 재접수 없음 |
| 오류·모르는 상태·불일치 | 상태 확인 필요 | 같은 요청 조회 |

`complete`만으로 백업 삭제나 복구 가능 여부를 추론하지 않는다. 30일 경과를 자체 계산해
완전 삭제로 승격하지도 않는다. 복구 API/휴지통 계약이 없으므로 복구 버튼을 만들지 않는다.

## 조회 adapter 인계

`DeletionReader`와 `DeletionSnapshot`은 **C 내부 대역 포트**이며 확정된 공개 wire 계약이 아니다.
`PostgresRemoteDeletionStore.get_existing()`은 owner·paper·request key가 모두 일치하는
기존 요청만 SELECT한다. 미존재 시 접수하지 않고 None을 반환한다. 접수 메서드와 달리
다른 요청 키를 같은 논문의 기존 요청에 연결하지 않는다.
`request()`를 상태 조회 용도로 호출하면 삭제를 접수할 수 있으므로 사용하지 않는다.

`OwnerScopedDeletionReader`는 이 조회를 고정 안내에 연결한다. 인증 경계가 제공하는
`current_owner`를 조회 전후 확인하여 다른 계정·로그아웃 시 응답을 버린다. 콜백에는
사용자 입력이 아니라 검증된 인증 주체를 제공해야 한다. 동기 내부 adapter이므로
HTTP 인증·기기 폐기 검사나 비동기 카드의 세션 세대 검사를 대신하지 않는다.
같은 owner로 로그아웃·재로그인하는 전환은 구분하지 못하므로 제품 연결 때 세대 검사가 필요하다.
공개 도구/라우트에는 아직 연결하지 않았으며 삭제 접수 권한을 새로 부여하지 않는다.

- owner는 인증 경계에서 제공하며 클라이언트가 지정한 owner를 신뢰하지 않는다.
- owner·paper·request key의 정확한 기존 요청과 현재 조회 권한을 확인한다.
- `access_allowed`는 삭제된 자료의 본문 접근 허용이 아니라 **삭제 요청 상태 조회 권한**이다.
- 조회는 tombstone/작업 생성, 물리 삭제 재실행, 동의 소비, 외부 호출을 일으키지 않는다.
- `backup_erasure_confirmed`는 향후 백업 계층의 검증된 사실만 전달한다. 현재 adapter가
  없으므로 이 adapter는 항상 false를 반환한다. 임의 입력·시각·UI 체크로 true를 만들면 안 된다.
- 반환 안내에는 원문·원시 오류·owner/paper/request key/request ID를 넣지 않는다.
- 계정 전환·늦은 응답 차단은 실제 비동기 카드 연결 시 추가 검증한다. 이 동기 대역으로
  Desktop 재시작이나 계정 전환 완료를 주장하지 않는다.

## 자동 검증

```sh
python -m pytest tests/unit/mcp/test_deletion_guidance.py tests/unit/mcp/test_deletion_status_reader.py tests/unit/test_remote_deletions.py -q
python -m pytest tests/integration/test_deletion_status_reader_postgres.py -q
```

서로 다른 삭제 의도, 상태별 문구, 반복 조회의 동일 키 사용, 다른 owner/대상/키 거부,
백업 완료의 별도 확인, 모르는 상태/오류의 비식별화, 고정 문구 위조 거부를 검사한다.
단위 테스트는 저장소 대역을 사용한다. PostgreSQL 통합 테스트는 별도 테스트 DSN과
임시 스키마의 합성 자료로 정확한 키 조회, 타인·다른 논문 거부, 미존재 조회의 무변경,
반복 조회 전후 행 전체 불변을 확인한다. DSN이 없으면 skip한다.
PDF·외부 AI API·운영 데이터에는 접근하지 않는다.

## 남은 #368 범위

- 로컬 행 제거 전 영속 tombstone 및 오프라인 재시도
- 인증된 삭제 접수/읽기 API와 클라이언트·제품 카드 연결
- 실제 Desktop·웹·두 기기의 수정↔삭제 충돌, 재연결, 응답 유실 검증
- 백업 상태 adapter와 보존 기한 집행/운영 증거

이 변경으로 #368을 종료하지 않으며 자동 삭제 동기화 지원으로 안내하지 않는다.
