# #214 후속 통합 검증 — #375

2026-11-14에 Windows 합성 데이터 자동 검증을 수행했고, 2026-11-16에 실제 Windows
Claude Desktop과 staging 웹 검증을 추가했다. PostgreSQL migration 이력 직접 확인과 macOS
Desktop 검증은 아직 완료하지 않았으므로 #375의 전체 검증이 끝난 것으로 기록하지 않는다.

## 최신 main 반영 상태 (2026-11-16)

생성 멱등성과 명시적 읽기 상태는 main에 반영됐고, 번역문 하이라이트와
최초 신고 주체 구현도 #477·#480으로 main에 반영됐다. 과거 #378~#380의
Draft 상태와 기능 브랜치 병합 순서는 현재 배포 절차가 아니다.

추가 회귀는 #478의 실제 stdio 메모 재시작, #479의 읽기 상태 재시작,
#481의 구형 지문·서버 신고 주체 보존 검증을 참고한다.
이 문서의 자동 종단 계약은 실제 Desktop·배포 서버 검증을 대신하지 않는다.

배포 시 서버 migration 전체 이력을 순서대로 적용한 뒤 API → MCP를 배포한다.
최초 신고 주체는 0032 migration이며 기존 0014를 수정하거나 재사용하지 않는다.

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

첫 파일은 4개 사례이며, 2026-11-16 macOS 재검증에서도 통과했다. stdio 검증은 실제 자식 프로세스의 도구 목록과
읽기 상태 조회·완료 호출을 검사한다. 각 테스트는 임시 DB를 쓰며 실제 서버·사용자 DB를 변경하지 않는다.

## 실제 환경에서 아직 해야 할 검증

이 작업 환경에서는 PostgreSQL/배포 서버 migration을 실행하지 않았다. `psql`·`docker` 실행 파일도
확인되지 않았다. A·B 계약 검토와 서버 배포가 선행돼야 하며 이를 완료된 것으로 기록하지 않는다.

- [ ] 서버 migration 전체 이력(최초 신고 주체 0032 포함) 적용 및 재실행/버전 이력 확인.
- [x] Windows Claude Desktop에서 원문/번역문 하이라이트 저장과 조회 카드 표시.
- [x] Desktop·MCP 재시작 뒤 같은 request_key 재사용과 기존 기록 반환.
- [x] 실제 Desktop에서 잘못된 인용 거부·수정 revision 충돌 시 원본 보존.
- [x] 실제 서버 전송 동의 후 같은 계정 웹 목록·상세의 내용·시각·작성 주체·상태 대조.
- [x] 실제 다른 계정에서 동일 ID 조회 차단.
- [ ] macOS Desktop에서도 동일 흐름 확인.
- [x] 자동 계약: #372의 context 없는 신규 논문 `not_started`가 별도 reading-state API로 전송되고
  가짜 learning record를 만들지 않음을 확인한다. 실제 배포 환경 확인은 아래 수동 표에 남긴다.

실제 검증은 개인 DB와 분리한 테스트 DATA_ROOT 및 공개 가능한 합성 논문으로 진행한다.
새 키는 새 저장 의도에만 발급하며 재시도에서는 바꾸지 않는다. 원문/번역문의 위치는 get_evidence
또는 저장된 인용 결과로 확인하고 추측하지 않는다. 서버 전송은 테스트 대상 논문의 동의 카드에서
명시적으로 승인한 후 실행한다. 기록할 내용은 commit SHA, OS/Claude 버전, 서버 배포 버전,
사용한 환경이 합성/실제인지, 성공/실패 코드와 화면 관찰 결과다. 토큰·사용자 본문은 게시하지 않는다.

## 실제 Windows Desktop·staging 검증 (2026-11-16)

Windows Claude Desktop에서 별도 `DATA_ROOT`와 공개 가능한 합성 논문
`2611.16620v1.pdf`를 사용했다. Claude Desktop 버전 문자열과 서버 migration 버전 이력은
이번 검증에서 별도로 수집하지 않았으므로 확인 완료로 간주하지 않는다.

| 항목 | 결과 | 관찰 근거 |
| --- | --- | --- |
| 원문 하이라이트 생성·즉시 재시도 | 통과 | 같은 request key에서 같은 annotation ID·revision 1, 두 번째 응답 `reused=true` |
| MCP 프로세스 재시작 후 재시도 | 통과 | Claude Desktop 완전 종료·재실행 뒤에도 같은 ID·revision 1, `reused=true` |
| 번역문 하이라이트 생성·재시도 | 통과 | 번역 revision과 문자 구간 `[0, 42)` 보존, 두 번째 응답 `reused=true` |
| 잘못된 인용문 | 통과 | `QUOTE_MISMATCH`, 실패한 request key 및 주석 미저장 |
| 수정 revision 충돌 | 통과 | revision 1 수정은 revision 2로 저장, 오래된 revision 1 재수정은 `CONFLICT`, 첫 수정 본문 보존 |
| 읽기 완료·재시작·다시 열기 | 통과 | `reading` revision 4 → `completed` revision 5 → 재시작 후 유지 → `reading` revision 6, action `reopen` |
| staging 서버 동기화 | 통과 | 사용자가 카드에서 전송 범위를 확인하고 승인, 작업 `d3dead07-49cc-46e6-8825-0ae695f31aec`가 `synced` |
| 같은 계정 웹 조회 | 통과 | 논문·기준 버전·읽기 상태와 원문/번역문 하이라이트 2건, 구간·본문·최초 신고 주체 표시 일치 |
| 다른 계정 접근 차단 | 통과 | 두 번째 테스트 계정으로 같은 상세 URL 접근 시 `기록을 찾을 수 없어요` 표시 |

staging 웹에서 원문 하이라이트 `[0, 32)`와 번역문 하이라이트 `[0, 42)`가 표시됐고,
두 기록 모두 최초 작성 주체가 `사용자(신고)`로 보존됐다. PDF 파일·전체 추출 원문·번역문·로컬
경로는 동기화 안내 범위에 포함되지 않았다.

아직 남은 실제 환경 검증은 다음과 같다.

- [ ] staging PostgreSQL migration 전체 이력과 0032 적용 여부를 직접 조회하고 재실행 안전성을 확인한다.
- [ ] macOS Claude Desktop에서 같은 사용자 흐름을 확인한다.
