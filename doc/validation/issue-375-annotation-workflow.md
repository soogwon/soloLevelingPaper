# #214 후속 통합 검증 — #375

2026-11-14에 Windows 합성 데이터 자동 검증을 수행했고, 2026-11-16에 실제 Windows
Claude Desktop과 staging 웹, PostgreSQL migration·인증 API 검증을 추가했다. macOS
Desktop→서버→웹 전체 검증은 아직 완료하지 않았으므로 #375의 전체 검증이 끝난 것으로 기록하지 않는다.

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

## 실제 환경 검증 체크리스트

초기 문서 작성 환경에는 `psql`·`docker`가 없어 PostgreSQL 검증을 수행하지 못했다. 이후 staging
읽기 전용 이력 확인과 CI 일회용 PostgreSQL의 제품 migration 도구 검증을 별도 근거로 완료했다.

- [x] 서버 migration 전체 이력(최초 신고 주체 0032 포함) 적용 확인 및 일회용 PostgreSQL에서 제품 도구 재실행 안전성 검증(#489).
- [x] Windows Claude Desktop에서 원문/번역문 하이라이트 저장과 조회 카드 표시.
- [x] Windows Desktop·MCP 재시작 뒤 같은 request_key 재전달과 기존 기록 반환.
- [ ] 카드의 응답 유실 후 재시작·저장소 변경에서도 같은 키가 복원되는지 확인.
- [x] 실제 Desktop에서 잘못된 인용 거부·수정 revision 충돌 시 원본 보존.
- [x] 실제 서버 전송 동의 후 같은 계정 웹 목록·상세의 내용·시각·작성 주체·상태 대조.
- [x] 다른 계정으로 동일 상세 URL 접근 시 웹 화면 미노출 확인.
- [x] macOS 로컬 Desktop 메모 저장·읽기 상태 재시작 검증(#485).
- [ ] macOS Desktop→실제 서버→웹 전체 흐름 확인.
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

아래 표는 PR #484 작성자의 관찰 기록이며 문서 재검토 과정에서 Windows 실험을
다시 실행한 것은 아니다. Windows 빌드, MCP wheel 커밋·해시, 서버·웹 배포 커밋도
현재 기록으로 특정할 수 없다. 문서 커밋 `6a7a7b4`를 제품 검증 커밋으로 대신하지 않는다.

| 항목 | 결과 | 관찰 근거 |
| --- | --- | --- |
| 원문 하이라이트 생성·즉시 재시도 | 통과 | 같은 request key에서 같은 annotation ID·revision 1, 두 번째 응답 `reused=true` |
| MCP 프로세스 재시작 후 같은 키 재전달 | 통과 | Claude Desktop 완전 종료·재실행 뒤 같은 request key로 같은 ID·revision 1, `reused=true`. 카드의 자동 키 복원 검증은 아님 |
| 번역문 하이라이트 생성·재시도 | 통과 | 번역 revision과 문자 구간 `[0, 42)` 보존, 두 번째 응답 `reused=true` |
| 잘못된 인용문 | 통과 | `QUOTE_MISMATCH`, 실패한 request key 및 주석 미저장 |
| 수정 revision 충돌 | 통과 | revision 1 수정은 revision 2로 저장, 오래된 revision 1 재수정은 `CONFLICT`, 첫 수정 본문 보존 |
| 읽기 완료·재시작·다시 열기 | 통과 | `reading` revision 4 → `completed` revision 5 → 재시작 후 유지 → `reading` revision 6, action `reopen` |
| staging 서버 동기화 | 통과 | 사용자가 카드에서 전송 범위를 확인하고 승인, 작업 `d3dead07-49cc-46e6-8825-0ae695f31aec`가 `synced` |
| 같은 계정 웹 조회 | 통과 | 논문·기준 버전·읽기 상태와 원문/번역문 하이라이트 2건, 구간·본문·최초 신고 주체 표시 일치 |
| 다른 계정 웹 화면 미노출 | 통과 | 두 번째 테스트 계정으로 같은 상세 URL 접근 시 `기록을 찾을 수 없어요` 표시. API 직접 접근 차단 검증과 구분 |

staging 웹에서 원문 하이라이트 `[0, 32)`와 번역문 하이라이트 `[0, 42)`가 표시됐고,
두 기록 모두 최초 작성 주체가 `사용자(신고)`로 보존됐다. PDF 파일·전체 추출 원문·번역문·로컬
경로는 동기화 안내 범위에 포함되지 않았다.

아직 남은 실제 환경 검증은 다음과 같다.

- [ ] staging PostgreSQL migration 전체 이력과 0032 적용 여부를 직접 조회하고 재실행 안전성을 확인한다.
- [ ] macOS Claude Desktop에서 실제 서버 동기화와 웹 대조까지 확인한다.
- [ ] Windows OS/Claude 버전, MCP wheel 커밋·해시, 서버·웹 배포 커밋을 회수한다. 회수 불가 시 다음 실험에서 기록한다.
- [ ] 실제 배포 환경에서 context 없는 초기 not_started 전송, 완료 후 추가 질문, 지연된 상태 전송의 CAS 충돌을 확인한다.
- [x] 실제 배포 환경의 device token API에서 무인증·source·owner 경계와 stale revision 충돌 후 상태 보존을 확인한다.

## 재검토와 macOS 검증의 범위 (2026-11-16)

#484의 Windows 실행에서 `test_annotation_reading_workflow.py`는 첫 MCP 메모리 전송
사례에서 정지해 중단됐다. 그 실행을 통과로 계산하지 않는다. 별도로 최신 main
`3588fd8`의 macOS 재검증에서 해당 4개 사례를 포함한 관련 Python 테스트 47개가
통과했다. 이는 Windows 정지 원인을 해결했다는 의미가 아니며 실제 PostgreSQL 검증도 아니다.

[macOS Desktop 검증 기록](../setup/issue-371-372-desktop-validation.md)에 따르면
메모 저장, 읽기 완료·재시작·학습 재개 후 유지, revision 충돌 및 완료 취소는 확인됐다.
반면 커밋 직후 응답을 지연하고 앱을 종료한 뒤 새 시크릿 대화에서 같은 내용을
저장하면 카드 origin이 바뀌어 새 키·중복 기록이 생성됐다. #485는 키가 없으면
신규 기록 확인 전 전송을 차단하지만 사라진 키를 자동 복원하지 않는다.
Windows의 같은 키 재전달 성공과 macOS의 카드 키 유실은 검증 조건이 다르며,
어느 한쪽 결과로 모든 호스트의 재시도 안전성을 보장하지 않는다.

다른 계정의 웹 화면 미노출은 확인된 UI 결과다. API 직접 접근의 상태 코드와
계정·source 경계, 실제 PostgreSQL CAS/행 잠금·migration 안전성은 별도 증거가 필요하다.
이 문서는 관찰 결과와 남은 범위를 기록하며 #371·#372·#375·#214를 자동 종료하지 않는다.

## Windows 자동 통합 재검증 (2026-11-16)

PR #484의 `4201bfb`를 Windows, Python 3.12.14, pytest 9.1.1에서 다시 실행했다.
`PYTHONPATH=src`, `LOCAL_ONLY=true`, `ALLOW_EXTERNAL_API=false`, 모델 오프라인 설정을
사용하고 별도 임시 DB 디렉터리를 지정했다.

```sh
python -m pytest tests/integration/test_annotation_reading_workflow.py -vv -p no:cacheprovider -o faulthandler_timeout=60 --basetemp <새 임시 디렉터리>
```

4개 사례 모두 통과했다(8.17초). 원문·번역문 메모 재시도와 읽기 상태 전송,
context 없는 초기 상태 전송, 전송 실패 후 로컬 보존·같은 작업 재개를 확인했다.
기존 anyio `BlockingPortal` 폐기 예정 경고 1건이 있었다.
이번 실행은 실행 도구의 샌드박스 제한 밖에서 수행했으며 정지는 재현되지 않았다.
이 결과만으로 이전 Windows 정지의 원인을 확정하거나 해결됐다고 판단하지 않는다.
인증과 서버 저장소는 대역이므로 실제 PostgreSQL·배포 서버·Claude Desktop 화면
검증 완료를 뜻하지 않는다.

## 실제 staging 인증 API 검증 (2026-11-16)

Render의 마지막 성공 배포가 main `f1b54a8cd53df79365decfcd99553e977d947b4f`임을
확인한 뒤 별도 테스트 계정 2개와 기존 합성 논문으로 API를 직접 검증했다. 토큰·이메일,
내부 owner ID, paper/source ID는 결과에 기록하지 않았다.

| 요청 | 결과 |
| --- | --- |
| 무인증 읽기 상태 조회 | `401 authentication_required`, `WWW-Authenticate: Bearer` |
| 소유 계정의 실제 device token 조회 | `200`, 기존 `reading`, server revision 1/source revision 6 확인 |
| 소유 계정 device token + 임의 source | `404 paper_not_found` |
| 소유 계정 device token + 틀린 expected revision 갱신 | `409 reading_state_conflict` |
| 충돌 전후 재조회 | 상태·revision·source revision 전체 불변 |
| 두 번째 계정의 실제 device token + 같은 paper/source | `404 paper_not_found` |

두 번째 계정은 Native PKCE와 등록된 loopback callback으로 임시 연결했다. 자격은 메모리에만
보관했으며 검증 직후 임시 기기를 해제했다. 기존 첫 번째 계정의 Windows 자격 저장소는
덮어쓰지 않았다. 이 결과는 실제 Auth0→기기 등록→서버 opaque device token→FastAPI→
staging PostgreSQL 경계의 owner/source 격리와 충돌 후 보존 증거다.

남은 #375 범위는 context 없는 초기 `not_started`의 실제 배포 전송, 완료 후 실제 질문,
같은 배포 버전의 Desktop→동의→서버→웹 재대조 및 macOS 전체 흐름이다. 이 검증만으로
#375 또는 #214를 닫지 않는다.
