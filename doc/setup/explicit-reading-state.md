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

로컬 DB의 paper_id 단위로 공유한다. context·버전마다 따로 완료하지 않는다.
로컬 DB는 개인 설치 범위이며 로그인 계정별로 분리되지 않는다. 서버는 기존
owner_user_id/source_instance_id 소유권으로 격리한다. 서로 다른 기기의 논문을 자동 병합하지 않는다.

`get_reading_state(paper_id)`로 revision을 확인한 뒤
`set_reading_completed(paper_id, completed, expected_revision)`을 호출한다.
충돌은 READING_STATE_CONFLICT이며 새 revision으로 자동 덮어쓰지 않는다.
응답 유실은 조회로 확인한다. 완료 취소는 completed에서만 허용한다.
완료 설정은 메모·퀴즈·학습 맥락을 삭제하거나 수정하지 않는다.

학습 기록 동기화에 reading_status를 추가하고 상태 갱신 시각을 source_updated_at에 반영한다.
같은 논문의 모든 context payload는 같은 상태를 가진다. 동의 시점 이후 상태가 변경되면 기존
payload 해시 검사가 전송을 거부하므로 다시 동의한다. 별도 자동 전송을 추가하지 않는다.
서버의 기존 source_updated_at 충돌 정책과 owner 검사를 유지한다. 읽기 상태를 보내지 않는 이전
클라이언트는 새 레코드에 unknown을 저장하고 기존 명시적 상태는 덮지 않는다.
웹 목록·상세는 기존 reading_status 필드를 그대로 사용하며 단계 비율에서 완료를 추정하지 않는다.

## 배포 순서와 제한

1. 서버 migration 0013 적용: unknown 허용 및 과거 추정값을 unknown으로 이관한다.
2. 새 서버 API 배포.
3. 새 MCP 클라이언트 배포. 이전 서버는 새 필드를 거부하므로 먼저 배포하지 않는다.

로컬 이관은 테이블 추가와 기존 행 unknown 초기화이며 반복 실행해도 명시적 값을 덮지 않는다.
실제 PostgreSQL migration 및 같은 계정 웹 화면 표시는 #375에서 별도 검증한다.
아직 context가 없는 논문은 기존 학습 기록 전송 경로가 없어 서버 웹에는 unknown으로 표시된다.
새 논문의 not_started를 학습 시작 전부터 서버에 표시하는 별도 전송은 이 PR의 잔여 연동 항목이다.
