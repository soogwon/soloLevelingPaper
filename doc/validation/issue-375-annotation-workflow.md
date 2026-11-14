# #214 후속 통합 검증 — #375

검증일: 2026-11-14. Windows에서 합성 데이터로 자동 검증했다.
이 문서는 실제 Claude Desktop·PostgreSQL·배포 웹 검증 완료 보고서가 아니다.
#214 및 #375는 열린 상태로 유지한다.

## 선행 PR과 검토 순서

1. #377: 생성 요청 중복 방지 (#371)
2. #378: 명시적 읽기 상태 (#372, Draft)
3. #379: 번역문 하이라이트 (#373, Draft, A·B 범위 검토 필요)
4. #380: 최초 작성 주체 신고 (#374, Draft, A·C 계약 검토 필요)
5. #375 검증 PR: 종단 계약 자동 테스트와 실제 환경 확인 절차

각 PR은 바로 앞 브랜치를 기반으로 한다. 앞 PR 병합 뒤 후속 브랜치를 최신 main으로
리베이스하고 PR base를 변경해야 한다. 여러 PR을 동시에 main에 독립 병합하지 않는다.
서버 migration 0013·0014 → API → MCP 순서로 배포한다.

## 실행한 자동 검증

`tests/integration/test_annotation_reading_workflow.py`:

| 계층 | 사용한 구현 |
| --- | --- |
| MCP | 실제 MCP 메모리 전송 및 공개 도구 |
| 로컬 | 실제 SQLite·서비스·트랜잭션 |
| 동기화 | 실제 동의·outbox·payload 고정 서비스 |
| HTTP | 실제 FastAPI 라우트·Pydantic 요청/응답 검사, TestClient |
| 인증·서버 저장 | 합성 계정 검증기·메모리 저장소 대역 |
| 웹 | 웹 조회 API 응답 확인, 브라우저 렌더링 아님 |

- 원문과 번역문 각각 생성 → MCP 서버 재생성 → 같은 키 재전송: 같은 메모 ID, 중복 없음.
- 명시적 start_learning → 완료 → 재시작/학습 재개에도 completed 유지.
- 동의 전과 동의 접수만으로는 HTTP 요청 없음. sync 실행 시에만 전송.
- 서버 웹 API에서 본문·인용·target·생성 시각·최초 작성 신고 보존.
- 완료 상태 전달 및 완료 취소 후 reading 전달.
- 다른 합성 계정의 동일 메모 조회는 404.
- 서버 전송 실패 시 로컬 메모 보존.

```sh
python -m pytest tests/integration/test_annotation_reading_workflow.py -q
python -m pytest tests/integration/test_mcp_stdio.py -q
```

첫 파일의 3개 사례는 Windows에서 통과했다. stdio 검증은 실제 자식 프로세스의 도구 목록과
읽기 상태 조회·완료 호출을 검사한다. 각 테스트는 임시 DB를 쓰며 실제 서버·사용자 DB를 변경하지 않는다.

## 실제 환경에서 아직 해야 할 검증

이 작업 환경에서는 PostgreSQL/배포 서버 migration을 실행하지 않았다. `psql`·`docker` 실행 파일도
확인되지 않았다. A·B 계약 검토와 서버 배포가 선행돼야 하며 이를 완료된 것으로 기록하지 않는다.

- [ ] 서버 migration 0013·0014 적용 및 재실행/버전 이력 확인.
- [ ] Windows Claude Desktop에서 메모·원문/번역문 하이라이트 버튼과 조회 카드 표시.
- [ ] 응답 유실 후 카드 재시작 시 localStorage 유지와 같은 request_key 재사용.
- [ ] 실제 Desktop에서 잘못된 인용 거부·수정 revision 충돌 시 원본 보존.
- [ ] 실제 서버 전송 동의 후 같은 계정 웹 목록·상세의 내용·시각·작성 주체·상태 대조.
- [ ] 실제 다른 계정에서 동일 ID 조회 차단.
- [ ] macOS Desktop에서도 동일 흐름 확인.
- [ ] #372의 context 없는 신규 논문 not_started 최초 전송 연결 완료 후 확인.

실제 검증은 개인 DB와 분리한 테스트 DATA_ROOT 및 공개 가능한 합성 논문으로 진행한다.
새 키는 새 저장 의도에만 발급하며 재시도에서는 바꾸지 않는다. 원문/번역문의 위치는 get_evidence
또는 저장된 인용 결과로 확인하고 추측하지 않는다. 서버 전송은 테스트 대상 논문의 동의 카드에서
명시적으로 승인한 후 실행한다. 기록할 내용은 commit SHA, OS/Claude 버전, 서버 배포 버전,
사용한 환경이 합성/실제인지, 성공/실패 코드와 화면 관찰 결과다. 토큰·사용자 본문은 게시하지 않는다.
