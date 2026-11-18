# 원격 학습 기록과 고정 context 계약 (#580, #581)

## A: 저장과 생명주기

원격 처리의 publication, 실행 context, 학습 기록은 서로 다른 객체다.
PDF 처리 완료나 단순 목록·상세 조회는 학습 시작이 아니다.
사용자가 명시적으로 학습을 시작할 때 `PostgresRemoteLearningStore.start`를 호출한다.
owner는 인증된 주체에서만 가져오고 context의 소유권과 논문 tombstone을 검사한다.

`server_remote_learning_records`는 owner/context당 한 행과 서버 UUID를 갖는다.
동일 context의 시작 재전송·다기기 이어하기는 같은 ID와 현재 상태를 반환한다.
로컬 동기화용 `server_learning_records`와 구별하며 가짜 기기·로컬 기록 ID를 만들지 않는다.
동일 논문을 새 context로 시작하면 별도 학습 기록이며, 제품은 이어하기에 기존 context를 사용한다.
기록 목록·선택 UI는 후속 제품 연결에서 제공한다.

완료→재열기는 expected_revision을 확인하고 같은 기록을 reading으로 바꾼다.
메모·요약을 지우지 않는다. 완료 상태의 start 재전송도 완료를 유지한다.
학습 상태는 context 단위이며 기존 논문 단위 읽기 상태를 암시적으로 변경하지 않는다.
논문 삭제 후 조회·시작·변경은 거부되고 기존 cleanup의 context 삭제가 학습 기록을 함께 지운다.
개별 학습 기록 삭제 API는 제공하지 않는다.

## B: 판본과 근거

복합 FK로 owner/paper/version/publication/parse/context가 정확히 일치해야 한다.
publication이 translation revision과 embedding set을 고정하므로 별도 최신값 추정은 하지 않는다.
학습 기록의 연결 필드는 DB trigger로 불변이다. 재처리 후 새 context는 새 publication에 연결하고
기존 학습 기록·질문·메모·요약의 근거를 자동 이동하지 않는다.

## C: 제품 연결 계약

1. 인증된 논문 판본 열기로 실행 context를 확보한다(학습 기록은 아직 없음).
2. 명시적 학습 시작으로 학습 기록을 생성하거나 기존 기록을 반환한다.
3. 이어하기는 기존 context/learning_record_id를 선택하며 임의의 새 context를 만들지 않는다.
4. 신규 원격 annotation/요약 제품 흐름은 학습 기록 확인 후 저장한다.
   기존 내부 annotation 포트의 호환성은 유지하며 제품 진입점의 선행 검사는 다음 PR에서 연결한다.
5. 요약 요청은 선택한 기록을 사용하고, 없으면 사용자에게 학습 시작을 안내한다.
   PDF 처리 완료가 요약 요청이나 외부 전송 동의가 되지 않는다.
6. 기록 생성 자체는 AI 호출·유료 실행·동의 부여가 아니다.

## 이번 구현과 남은 범위

0043 migration과 내부 저장 포트·격리 PostgreSQL 회귀만 추가한다.
공개 HTTP/MCP 도구, 웹·카드, 제한 DB 역할, 원격 요약 테이블은 다음 단계다.
기존 동기화형 annotation/summary 테이블에 원격 기록을 억지로 넣지 않으며,
원격 요약 저장과 웹 조회 통합은 출처를 명시한 별도 연결로 구현한다.
실제 staging migration·권한·배포는 별도 승인 후 적용한다.
