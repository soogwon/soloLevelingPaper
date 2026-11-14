# 최초 작성 주체 보존안 — #374

A·C 계약 검토용 구현안이며 새 분류에 대한 합의가 끝났다는 뜻은 아니다. Draft PR로 검토한다.

`creation_origin`은 최초 생성 시 호출자가 신고한 `user`, `ai`, `unknown` 중 하나다.
기본은 unknown이고 과거 기록도 임의로 추정하지 않는다. 사용자 문장을 MCP가 그대로 전달하면
user, 모델이 문장을 생성하면 ai, 판별할 수 없으면 unknown을 사용한다.
사용자가 직접 카드에 입력하거나 하이라이트 버튼을 누르면 user로 신고한다.

이 필드는 **소유권이나 인증된 작성자 정보가 아니다**. 서버의 owner_user_id는 로그인 자격으로
결정되고 source_instance_id는 전송 출처다. 사용자가 creation_origin을 지정해도 다른 계정의
메모를 읽거나 수정할 권한이 생기지 않는다. 웹과 MCP 카드에 ‘최초 작성 … (신고)’로 표시한다.

생성 이후 본문 수정은 가능하지만 creation_origin은 수정하지 않는다. 최종 문장 전체가 처음
작성 주체의 글이라는 보장은 아니며 이후 편집자 이력은 별도 범위다. 같은 요청 키에 다른
creation_origin을 넣으면 REQUEST_KEY_CONFLICT로 거부한다. 이전 unknown 요청의 해시는 호환한다.

로컬은 별도 출처 테이블을 사용하고 메모 삭제 시 함께 삭제한다. 동기화 payload 및 서버 migration
0014의 컬럼으로 보존한다. 이전 클라이언트가 필드를 생략하면 새 기록은 unknown, 기존 기록의
최초 작성 주체는 유지한다. 지정된 주체를 바꾸는 서버 upsert는 충돌로 거부한다.

배포 순서는 migration 0014 → API → MCP다. 서버 미배포 상태에서 새 payload를 보내지 않는다.
실제 PostgreSQL migration과 Desktop·웹 표시는 #375에서 검증한다. #214는 종료하지 않는다.
