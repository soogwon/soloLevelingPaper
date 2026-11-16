# #368 C 삭제 확인·상태 안내 대역

> 후속 제품 연결: [서버 논문 삭제 연결과 검증](server-paper-deletion.md). 아래는 기존 내부 대역의
> 범위 기록이며, 새 HTTP/MCP 경로와 아직 남은 실기기 검증은 후속 문서에서 구분한다.

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
같은 owner로 로그아웃·재로그인하는 전환은 이 동기 adapter만으로 구분하지 못한다.
아래 C 화면 제어는 세션 전환 알림으로 이전 응답을 폐기하며 제품 인증 경계 연결은 남아 있다.
공개 도구/라우트에는 아직 연결하지 않았으며 삭제 접수 권한을 새로 부여하지 않는다.

- owner는 인증 경계에서 제공하며 클라이언트가 지정한 owner를 신뢰하지 않는다.
- owner·paper·request key의 정확한 기존 요청과 현재 조회 권한을 확인한다.
- `access_allowed`는 삭제된 자료의 본문 접근 허용이 아니라 **삭제 요청 상태 조회 권한**이다.
- 조회는 tombstone/작업 생성, 물리 삭제 재실행, 동의 소비, 외부 호출을 일으키지 않는다.
- `backup_erasure_confirmed`는 향후 백업 계층의 검증된 사실만 전달한다. 현재 adapter가
  없으므로 이 adapter는 항상 false를 반환한다. 임의 입력·시각·UI 체크로 true를 만들면 안 된다.
- 반환 안내에는 원문·원시 오류·owner/paper/request key/request ID를 넣지 않는다.
- C 내부 화면 제어의 계정 전환·늦은 응답 차단은 비동기 대역으로 검사한다. 실제 인증/카드
  연결에서도 추가 검증해야 하며 Desktop 재시작이나 제품 계정 전환 완료를 주장하지 않는다.

## C 세션 전환과 늦은 응답 차단

main `f1b54a8` 기반 `fix/issue-368-session-generation`에서 `DeletionStatusScreen`을 추가했다.
기존 `render_deletion_preview`와 같은 고정 문구 검증을 사용하며 읽기 전용 `AsyncDeletionReader`를
받는다. 새 공개 도구·HTTP 라우트나 삭제 API는 추가하지 않는다.

인증·화면 adapter는 다음 순서를 지켜야 한다.

1. 화면 인스턴스는 인증 전 `signed_out`으로 시작한다. 인증된 owner가 확인된 후
   `session_changed(owner)`를 호출하고 `render()`의 현재 내용을 표시한다.
2. 로그아웃·만료·인증 무효화 때 `session_changed(None)`을 호출하고 **즉시 다시 렌더링**한다.
   같은 owner의 재로그인도 `session_changed(owner)`를 새로 호출한다. owner 문자열만 비교해
   알림을 생략하면 안 된다. 이 알림은 인증 검증이나 서버의 폐기 자격 검사를 대신하지 않는다.
3. 현재 세션에서 명시적으로 `refresh(lookup)`을 시작한다. 조회는 기존 키만 사용하며
   삭제 접수·재접수·동의 소비·외부 호출을 새로 시작하지 않는다. 조회 중에는 과거 결과를 지운다.
4. 각 조회는 고유한 표식을 가진다. 세션 전환 또는 새 조회는 이전 표식을 무효화한다.
   늦은 성공·오류는 `False`를 반환하고 현재 화면을 바꾸지 않는다. `True`인 경우에도
   반환 결과를 캐시하지 말고 `render()`로 현재 상태만 표시한다. 미인증/다른 owner 거부도
   현재 안내를 바꾸므로 True이며 reader는 호출하지 않는다.
5. 현재 조회 실패는 고정 오류 안내로 표시한다. 취소는 안내를 갱신한 뒤 CancelledError를
   전파한다. 호출자는 취소 처리에서도 현재 `render()`를 사용한다. 취소된 이전 조회는
   새 세션의 대기/완료 안내를 바꾸지 않는다.

이 제어는 **화면별 단일 이벤트 루프**에서 사용한다. 동기 PostgreSQL reader를 이벤트 루프에서
직접 실행하지 않는다. 실제 비동기 인증 조회 adapter와 세션 이벤트 연결은 후속 제품 작업이다.
화면을 폐기할 때도 세션을 무효화하고 오래된 작업이 UI에 직접 쓰지 않도록 해야 한다.

2026-11-16 자동 검증은 Future/Queue로 응답 시점을 제어했으며 임의 sleep을 사용하지 않았다.
같은 owner 재로그인, 다른 owner 전환, 로그아웃 후 지연 성공/실패, 최신 조회 우선,
표시 즉시 초기화, 미인증/타인 조회 차단, 잘못된 응답, 현재 오류/취소를 검사했다.
새 화면 제어 17개와 기존 안내/reader/삭제 계약을 합해 **70 passed**. Ruff와 공백 검사 통과.
실제 사용자 데이터·삭제·외부 API를 사용하지 않았고 Desktop·웹 로그인 화면을 실행한 증거는 아니다.

## 자동 검증

```sh
python -m pytest tests/unit/mcp/test_deletion_status_screen.py tests/unit/mcp/test_deletion_guidance.py tests/unit/mcp/test_deletion_status_reader.py tests/unit/test_remote_deletions.py -q
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
