# 명시적 읽기 상태 — #372 구현안

이 PR은 A의 서버 배포·이관 검토 대상이다. #214를 자동 종료하지 않는다.

| 계기 | 이전 상태 | 다음 상태 |
| --- | --- | --- |
| 새 논문 등록 | 없음 | not_started |
| 기존 DB 이관 | 알 수 없음 | unknown |
| 사용자 start_learning 성공 | not_started / unknown | reading |
| 명시적 완료 | 모든 상태 | completed |
| 명시적 완료 취소 | completed | reading |
| 질문·학습 재개·퀴즈·단계 완료·파싱·번역 | 모든 상태 | 유지 |

로컬 DB의 paper_id 단위로 공유하되 basis_version_id에 완료/학습 기준 원문 버전을 남긴다.
다른 판본을 열어도 완료를 취소하거나 기준 버전을 바꾸지 않는다. 웹에는 기준 버전과
다른 판본의 학습 완료를 의미하지 않는다는 안내를 표시한다. 로컬 개인 설치 상태는
로그인 계정별로 분리되지 않는다. 서버는 인증 owner와 등록 source_instance_id·paper_id로
격리하며 서로 다른 기기의 논문을 자동 병합하지 않는다.

`get_reading_state(paper_id)`로 revision을 확인한 뒤
`set_reading_completed(paper_id, completed, expected_revision, basis_version_id)`을 호출한다.
기준 버전 생략 시 기존 기준을 유지하며 기준 없는 완료는 거부한다. 기준은 해당 논문에
속해야 한다. 완료 취소는 completed에서만 가능하며 기준을 변경하지 않는다.
메모·퀴즈·학습 맥락은 변경하지 않는다. 충돌은 READING_STATE_CONFLICT이며
새 revision으로 자동 덮어쓰지 않는다. 응답 유실은 먼저 조회로 확인한다.

## 논문 단위 동의와 전송

새 동의 `paper-reading-state-sync-v5`만 상태·기준 버전·리비전·명시적 동작을 포함한다.
이전 v4의 학습/요약/메모 동의 범위는 유지하되 읽기 상태 전송을 추가하지 않는다.
학습 맥락이 없어도 별도 reading_state outbox로 not_started를 전송한다.
학습 기록 payload에서 읽기 상태를 제거하고 서버 목록/상세는 전용 상태만 조회한다.
기존 클라이언트의 learning record 상태는 전용 상태를 덮어쓰지 않는다.

GET/PUT `/v1/mcp/papers/{paper_id}/reading-state`는 device 인증과 등록 출처를 검사한다.
PUT은 expected_revision CAS를 사용한다. 승인 시점의 요청 본문·operation_id·기준 리비전을
고정 저장하여 응답 유실 후에도 같은 요청으로 재시도한다. 같은 마지막 요청은 재사용하고
이후 상태가 바뀐 오래된 요청은 409로 거부한다. 시각 순서로 덮어쓰지 않는다.
source_revision 역행, 같은 source_revision의 다른 상태, 명시적 reopen 없는 완료 해제도
409이다. 완료→취소를 오프라인에서 마친 첫 전송은 reading/reopen으로 허용한다.
충돌은 자동 재기준화하지 않는다. 서버 조회 후 사용자가 해결하는 UI는 후속 범위다.
동의 후 로컬 상태 변경/동의 철회도 전송을 막는다.

## 배포 순서·이관 검토

1. A가 기존 배포 이력, 상태별 건수 및 명시적 상태의 출처를 확인하고 백업한다.
2. 미적용 migration 0013으로 전용 테이블을 추가한다. 기존 learning record 행은
   수정하지 않는다. 신뢰할 수 있는 명시적 상태의 별도 이관은 A 확인 전 수행하지 않는다.
3. 서버 API → MCP 순서로 배포하고 새 동의로 상태를 전송한다.
4. 다른 owner/source, CAS 충돌, 초기 상태와 완료/취소, 재시도 및 웹 표시를 실제 PostgreSQL에서 검증한다.

0013을 이미 적용한 환경은 수정본을 재실행하거나 checksum을 우회하지 않는다.
A가 배포 여부를 확인한 뒤 별도 전진 이관을 정해야 한다. 이전 버전으로 되돌릴 때도
기존 기록과 전용 상태를 보존하고, 구버전이 보여주는 추정 상태를 명시적 상태로 이관하지 않는다.
새 공개 상태가 없는 기존 서버 논문은 unknown이며 원래 learning record 데이터는 남는다.
로컬 init_db 반복 실행은 이미 저장한 상태를 보존한다. 이전 Draft의 기준 없는 완료도
삭제하지 않으며, 기준 버전을 명시해 완료를 재확인해야 새 서버로 전송할 수 있다.

## 검증 범위

회귀 테스트는 로컬 SQLite, FastAPI 및 SQL 저장 로직의 SQLite 트랜잭션 대역을 사용한다.
이는 실제 PostgreSQL의 행 잠금·migration 또는 배포 환경 검증이 아니다.
실제 Claude Desktop·서버·웹 연결 검증은 #375에 남기며 #372/#214를 자동 종료하지 않는다.

### 제품 stdio 프로세스 재시작 검증

`tests/integration/test_reading_state_stdio.py`는 임시 DB의 제품 MCP 서버를 실제 종료하고
다시 실행한다. 학습 시작 → 명시적 완료 → 서버 재시작 → 추가 학습 시작에도 완료 유지 →
오래된 revision 거부 → 명시적 완료 취소 → 재시작 후 reading 유지 순서를 확인한다.
완료 취소는 기준 원문 버전과 기존 메모를 보존한다. 응답을 확인하지 못한 동일 완료 요청도
revision이 오래됐다면 자동 재시도하지 않고 상태를 조회해야 한다.

외부 API·사용자 환경 파일·임베딩 준비 없이 실행하며, 실제 Desktop의 렌더링이나
서버→웹 동기화 및 PostgreSQL 배포 검증으로 해석하지 않는다.
