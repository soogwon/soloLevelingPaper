# #214 후속 통합 검증 — #375

2026-11-14에 Windows 합성 데이터 자동 검증을 수행했고, 2026-11-16에 실제 Windows
Claude Desktop과 staging 웹, PostgreSQL migration·인증 API 검증을 추가했다. macOS
Desktop→서버→웹 전송과 전체 출처 대조를 추가 확인했다. 실제 두 계정의 메모 API 격리 등
남은 항목이 있으므로 #375의 전체 검증이 끝난 것으로 기록하지 않는다.

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
- [x] 같은 origin·저장소 및 입력 재제공 조건의 카드 재시작 복구 확인(#500·#501).
- [ ] origin·저장소 변경 및 계정·기기 간 복구는 별도 #502 범위로 검증.
- [x] 실제 Desktop에서 잘못된 인용 거부·수정 revision 충돌 시 원본 보존.
- [x] 실제 서버 전송 동의 후 같은 계정 웹 목록·상세의 내용·시각·작성 주체·상태 대조.
- [x] 다른 계정으로 동일 상세 URL 접근 시 웹 화면 미노출 확인.
- [x] macOS 로컬 Desktop 메모 저장·읽기 상태 재시작 검증(#485).
- [x] macOS Desktop 동의→실제 서버 전송→같은 계정 웹 조회 확인.
- [ ] 실제 다른 계정의 메모 API 격리 및 아래 남은 상태 전이 검증.
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

- [x] staging migration 전체 이력·0032는 A가 읽기 전용으로 확인했다. 재실행 안전성은 #489의 일회용 PostgreSQL에서 확인했으며 staging 원본에 재실행한 것은 아니다.
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

다른 계정의 웹 화면 미노출은 UI 증거다. 이후 #489·#491·#492에서 PostgreSQL 이관·
CAS·합성 인증 HTTP 검증이 추가됐고, 아래 실제 staging 검증은 읽기 상태 조회 격리와
충돌 후 보존을 확인했다. 각 증거의 환경과 범위를 서로 대체하지 않는다.
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
staging PostgreSQL 경계의 읽기 상태 **조회** owner/source 격리와 충돌 후 보존 증거다.
수행 근거는 [A의 검증 코멘트](https://github.com/soogwon/soloLevelingPaper/issues/375#issuecomment-6909781095)다.
C는 이번 문서 리뷰에서 코멘트와 기록을 대조했으며 실제 자격으로 재실행하지 않았다.

### 실제 staging 인증 거부 행렬 후속 검증

후속 main·Render staging 배포 `430afe7b10bdec6134fdd59d5ff6899130d30e78`에서
동일한 합성 논문의 읽기 상태를 대조군으로 삼아 갱신·자격 거부를 추가 확인했다.

| 요청 | 결과 |
| --- | --- |
| 소유 계정 device token + 임의 source `PUT` | `404 paper_not_found` |
| 소유 계정 device token + 미존재 paper `PUT` | 위 요청과 상태 코드·오류 본문 동일 |
| 형식만 맞춘 무효 device token 조회 | `401 authentication_required`, Bearer challenge |
| 두 번째 계정 device token + 실제 paper/source `PUT` | `404 paper_not_found` |
| 두 번째 계정 device token + 미존재 paper `PUT` | 위 요청과 상태 코드·오류 본문 동일 |
| 임시 기기 해제 후 같은 access token 조회 | `401 authentication_required`, Bearer challenge |
| 모든 거부 뒤 소유 계정 재조회 | `reading`, server revision 1/source revision 6 불변 |

두 번째 계정 자격은 메모리에만 보관했고, 기기 해제 성공 뒤 같은 access token의 실제 거부까지
확인했다. 기존 첫 번째 계정의 Windows 자격 저장소는 변경하지 않았다. 이 후속 결과로 읽기 상태
API의 타 owner·다른 source **갱신** 거부, 미존재 대상과의 공개 오류 동등성, 무효·폐기 자격 거부를
실제 Auth0·staging PostgreSQL 경계에서 확인했다. 메모 API나 completed 상태 전이는 검증 범위가 아니다.

후속 [C의 macOS 검증](https://github.com/soogwon/soloLevelingPaper/issues/375#issuecomment-6910469726)에서
context 없는 초기 `not_started` 전송, 명시적 완료 취소, 연결 실패 후 기존 승인 작업의
재시작 자동 재시도를 확인했다. 이는 이 절의 인증 거부 행렬과 별도 실행 결과다.
[일반 대화 재시작 복구](https://github.com/soogwon/soloLevelingPaper/issues/375#issuecomment-6911226492)는
main `ce1b2eb`의 합성 로컬 DB 검증이며 원격 서버 검증은 아니다.
이 시점에는 학습 시작·완료 후 질문, 늦은 완료/재전송, 메모 API 격리와 전체 출처·시각 대조가
남아 있었다. 아래 C 후속 결과에서 완료한 범위와 현재 남은 범위를 구분한다.

두 번의 실제 API 표만으로 다음 항목을 완료 처리하지 않는다.

- 메모 API의 본문·구간·작성 주체 불변. 이번 직접 요청은 읽기 상태 API에 한정된다.
- 실제 completed 상태의 지연 전송/동일 operation 재생. 이번 staging 대조군은 reading이었다.
- 응답 유실·재시작 후 요청 키 복구의 지원 범위. #490 내부 모듈을 제품 카드에 연결하고
  새 시크릿 origin·저장소 유실·다중 기기 범위를 검증하거나 명시적으로 합의해야 한다.
- 실제 전송 실패 후 로컬 기록 보존·같은 승인 작업 재개와 서버 중복 방지, 실험별 설치본/배포 식별.

#492의 합성 인증+실제 DB 회귀와 위 실제 자격 검증을 구분한다. #375 또는 #214를 닫지 않는다.


## C의 macOS·staging 후속 검증 (2026-11-16)

제품 소스는 main `247333441790c6dc5ad95fef2b140c03a8e05ec0`, 환경은 macOS
26.7.1 arm64, Claude Desktop 5.11492.5 일반 대화다. Render의 마지막 성공 배포도
동일 SHA로 확인했다. 기존 제품 Python 환경에 최신 소스를 `PYTHONPATH`로 연결한
합성 데이터 전용 MCP이며, 새 wheel 설치 검증은 아니다.

### 실제 서버 재전송·불변값·웹 대조

기존에 동의하여 전송한 합성 메모 2건과 원문/번역문 하이라이트 각 1건을 사용했다.
실제 키체인 기기 자격과 staging PostgreSQL 경계에서 확인했다.

| 검증 | 결과 |
| --- | --- |
| 최신 reopen operation 재전송 | 200, 상태·revision 등 응답 전체 동일 |
| reopen 뒤 이전 completed operation 지연 도착 | 409, 현재 상태 불변 |
| 현재 CAS revision을 사용해도 오래된 source revision 전송 | 409, 현재 상태 불변 |
| 메모/하이라이트 4건 동일 payload 재전송 | 모두 200, 같은 기록·revision 1 유지 |
| 각 기록의 본문·선택 판본·최초 신고 주체·원래 생성 시각 변경 재전송 | 각 4가지 모두 409, 원래 요청 재전송으로 기존 기록 불변 확인 |
| 다른 source와 미존재 paper에 각 기록 PUT | 모두 404, 공개 오류 본문 동일, 기존 기록 불변 |
| 로그인한 웹의 메모 조회 API와 로컬 저장값 대조 | 4건 모두 본문·종류·판본·단계·신고 주체·생성/수정 시각 일치 |
| 원문/번역문 구간 대조 | chunk·파싱/번역 revision·target·문자 범위·인용문 일치 |

웹 목록/상세의 표시 검증은 앞선 macOS 실행과 연결하고, 이번에는 로그인된 웹 조회
응답의 전체 필드를 대조했다. 다른 source 거부를 실제 다른 계정(owner)의 거부로
간주하지 않는다. 읽기 상태 대조군은 **reading**이었으므로 completed 상태에서 늦은
reading 전송을 막는 검증과도 구분한다.

### 완료 후 실제 질문과 명시적 재열기

| Desktop 공개 도구 순서 | 관찰 결과 |
| --- | --- |
| start_learning(v1, understand) | ready, 기존 context 반환 |
| 명시적 완료(expected revision 3) | completed/revision 4, basis v1 |
| 완료 후 start_learning(v1) | ready, completed/revision 4 및 시각 유지 |
| 앱 재시작 후 상태 조회 | completed/revision 4 유지 |
| ask_paper 실제 답변 성공 | “첫 번째 근거의 문장은 First.”, 원문 First./번역 첫째. 근거 연결 |
| 질문 직후 상태 조회 | completed/revision 4 및 시각 불변 |
| 없는 판본으로 start_learning | NOT_FOUND, completed/revision 4 및 시각 불변 |
| 명시적 완료 취소(expected revision 4) | reading/revision 5, action reopen |
| 후속 공개 조회와 SQLite 대조 | reading/revision 5, 동일 시각·basis v1 |

사용자 승인 합성 원문 First./Second., 번역 첫째./둘째.와 질문을 사용했으며
OpenAI 요청은 승인 최대 4회 중 **1회**였다. 답변은 `ok`, `structural_only`,
`verification_history=not_used`로 표시됐고 의미 검증 완료로 간주하지 않는다.

초기 합성 fixture는 가짜 임베딩 메타데이터만 있어 Chroma 검증에 실패했다.
실제 캐시된 다국어 임베딩 모델로 합성 2청크의 벡터를 만들고 테스트 DB의 메타데이터를
맞췄다. 이후 외부 API 허용 설정이 빠져 발생한 UPSTREAM_UNAVAILABLE도 테스트 설정을
수정한 뒤 재검증했다. 두 초기 실패 모두 유료 API 호출 전이었고 완료 상태는 유지됐다.
실제 PDF 등록·파싱 파이프라인을 이 fixture 준비로 대체 검증했다고 주장하지 않는다.

질문 검증용 MCP에는 서버 자동 동기화를 연결하지 않았으므로 로컬 revision 4→5를
staging에 전송한 결과로 간주하지 않는다. 임시 MCP 설정은 백업과 바이트 단위로 일치하게
복원하고 Desktop을 재실행했다. API 키·토큰·개인 DB·모델 파일은 저장소에 포함하지 않는다.

### PR #505 작성 시 남은 범위와 종료 판단

- 실제 두 번째 계정으로 **메모 API** 조회/갱신 거부, 미존재 대상과 오류 동등성 및 소유 기록 보존 확인(A 협조 필요).
- 실제 staging completed 상태에서 늦은 reading 전송 거부·동일 완료 operation 재생 확인.
- 이번 완료→실제 추가 질문→완료 취소를 등록부터 연결한 단일 제품 흐름과 서버/웹 최종 상태 대조.
- 새 wheel 및 Windows의 이번 후속 조건은 미검증으로 유지한다. 이전 Windows 증거를 대체하지 않는다.
- #371은 A·B 합의와 165개 관련 회귀 통과 후 종료했다. 계정·기기·새 origin 간 원격 요청 키 복구는 #502로 분리했으며 현재 지원 보장에 포함하지 않는다.

이번 후속 검증에서 제품 코드 결함은 새로 확인하지 않았다. #375와 상위 #214는 열어두며,
위 남은 검증과 플랫폼 지원 범위를 합의한 뒤 종료 여부를 판단한다.


## 최신 main wheel·완료 상태 종단 후속 검증 (2026-11-16)

제품 및 Render 성공 배포는 `6ed9924a502a08bec42c7ce27c2691fa991d0504`다.
macOS 26.7.1 arm64 / Claude Desktop 5.11492.5 일반 대화에서 새 wheel을 별도 경로에
설치해 기존 합성 DB와 연결했다. 런타임 import가 wheel 설치 경로임을 확인했으며
기본 설치본·개인 DB는 변경하지 않았다. wheel SHA-256:
`4b3a95faeda39e4363032490f3c708fc04d4a05beb92315cdeb0a823b207caf0`.

| 검증 순서 | 실제 결과 |
| --- | --- |
| 명시적 완료 | 로컬 reading/revision 5 → completed/revision 6, basis v1 |
| Desktop 동의 카드 확인 후 동기화 | synced, 서버 completed/revision 4/source revision 6 |
| 웹 재조회 | 읽기 완료, 기준 v1 및 다른 판본까지 완료를 뜻하지 않는 안내, 메모·하이라이트 4건 |
| 같은 완료 operation 재전송 | 200, 완료 응답 전체 불변 |
| 이전 reading/reopen operation 지연 전송 | 409, 완료 상태·revision 전체 불변 |
| 새 operation ID와 현재 서버 CAS revision으로 오래된 source revision 전송 | 409, 완료 상태·revision 전체 불변 |
| start_learning 후 승인된 합성 질문 1회 | ready, 답변 ok 및 First./첫째. 근거 표시, structural_only/not_used |
| 질문 후 읽기 상태 | completed/revision 6 및 원래 수정 시각 유지 |
| 명시적 완료 취소 | reading/revision 7, action reopen, basis v1 |
| 새 동의 카드 확인 후 동기화 | synced, 서버 reading/revision 5/source revision 7 |
| 웹 재조회 | 읽는 중, 기준 v1, 메모·하이라이트 4건 유지 |
| Desktop 완전 종료·재실행 후 조회 | reading/revision 7 및 같은 기존 동기화 operation의 synced 유지 |

서버 직접 요청에는 기존 합성 논문과 키체인 기기 자격만 사용했다. 원격 상태 수정은
Desktop에서 확인한 동의 내용을 전송한 것이며, 검증용 직접 재전송은 동일 완료 요청과
거부되어야 하는 이전 reading 요청에 한정했다. 응답 비교는 공개 코드뿐 아니라 현재
상태 전체의 불변을 검사했다. 로컬과 서버 revision은 별도 증가하므로 숫자가 같아야
한다는 전제를 두지 않고 서버 source revision과 로컬 revision을 대조했다.

실제 질문의 외부 OpenAI 요청은 이번 1회, 이전 실행 포함 **2/4회**였다. 추가 번역이나
의미 검증은 호출하지 않았다. 등록된 합성 2청크와 앞 절의 실제 벡터 준비를 그대로
사용했으므로 새 PDF 등록·파싱의 처음부터 끝까지 검증은 아니다. 질문/답변은 동기화
전송 제외 대상이며 카드에도 안내됐다.

읽기 상태·자동 재시도·MCP→동의/outbox→HTTP 관련 자동 회귀 8개가 통과했다.
HTTP 인증·서버 저장 대역의 자동 회귀와 위 실제 staging 결과를 구분한다.
기존 anyio 폐기 예정 경고 1개가 있었고 실패·skip은 없었다. 임시 MCP 설정은 원본과
바이트 단위 일치하도록 복원하고 기본 Desktop을 다시 실행했다.

### 현재 종료 전 남은 항목

- A 협조: 실제 다른 계정의 메모 API 조회/갱신 거부, 미존재 대상과 오류 동등성 및 기존 기록 보존.
- C 후속: 새 PDF 등록·파싱부터 학습·완료·실제 질문·동의 전송·웹까지 단일 흐름. 이번에는 이미 준비한 합성 학습 자료를 재사용했다.
- 이번 최신 wheel 조건의 Windows 검증 또는 플랫폼별 지원 범위 합의. macOS 결과를 Windows 완료로 간주하지 않는다.
- 계정·기기·새 origin 간 원격 생성 의도 복구는 분리된 #502 범위이며 현재 같은 origin 복구 보장에 포함하지 않는다.

이 실행에서는 제품 코드 보완이 필요한 결함을 발견하지 않았다. #375와 #214는 위 범위가
남아 있어 열어둔다. 완료 상태의 늦은 reading 거부 및 macOS wheel의 질문→완료 취소→
서버/웹→재시작 부분은 더 이상 미검증 항목으로 나열하지 않는다.

## 새 합성 PDF부터 웹까지 단일 흐름 검증 (2026-11-16)

PR #506·#507 병합 후 main `4e9be39015feee3bddd1a5ac0c185f1414ae6015`에서 새 wheel을
빌드하고 빈 DATA_ROOT에 연결했다. macOS 26.7.1 arm64 / Claude Desktop 5.11492.5
일반 대화에서 실행했다. wheel SHA-256은
`237b4c5da062150929a8e51eeb71e70b9472159f103247caf7a9ba9ba05ae511`이다.
기존 제품 Python의 의존성과 캐시된 실제 임베딩 모델은 재사용했으므로 깨끗한 PC 설치 검증은 아니다.

새 1페이지 합성 PDF `c375-fresh-workflow.pdf`를 사용했다. 근거 확인→메모 작성,
완료 후 질문 가능, 재열기 시 메모 보존을 설명하는 짧은 영어 문장만 포함한다.
본문 추출과 렌더링을 확인한 뒤 제품 등록 파이프라인을 실행했으며, DB에 청크·번역·벡터를
직접 넣지 않았다. 기존 합성 학습 DB를 재사용한 앞 절과 구분한다.

| 순서 | 실제 결과 |
| --- | --- |
| 등록 전 안내 | 일반 대화 카드에 파일명·OpenAI 전송 데이터/시점/비용 안내 표시. 동의 전 등록되지 않음 |
| 카드 동의 후 등록 | 작업 ID 표시 후 ready/complete. 실제 파싱·번역·검색 색인 완료, 원문/번역 각 1청크 |
| 학습 시작 | 새 context 생성. 저장 요약·목차가 없는 상태를 빈 상태로 표시 |
| 메모 저장 | 위치 미연결 사용자 메모 1건, revision 1 |
| 명시적 완료 | reading/revision 2 → completed/revision 3, 이번 PDF 원문 버전 기준 |
| 완료 후 실제 질문 | “논문을 완료로 표시한 후에도 질문할 수 있나요?”를 1페이지 범위로 실행. 답변 ok, 완료 후 질문 가능 문장 인용 |
| 질문 근거 | get_evidence 카드에서 실제 원문·한국어 번역과 PDF 1페이지 표시. structural_only/not_used |
| 질문 후 읽기 상태 | completed/revision 3·수정 시각 유지 |
| 하이라이트 저장 | 완료 후에도 원문 문자 구간 [165, 225)에 사용자 하이라이트 저장, revision 1 |
| 실제 동의 후 서버 전송 | synced. 웹에서 읽기 완료·기준 버전·메모 1건/하이라이트 1건·본문/인용문/작성 주체 표시 일치 |
| PDF 제외 | 웹 원문 보기 비활성화 및 해당 버전 PDF 미업로드 안내. PDF 업로드는 수행하지 않음 |
| Desktop 완전 종료·재실행 | 기존 context+request_key로 같은 저장 답변 조회, 기존 operation은 synced. 상세 카드 기록 2건 및 completed/revision 3 유지 |
| 명시적 완료 취소 | reading/revision 4, action reopen. 기록 2건의 ID·본문·구간·revision 1 유지 |
| 새 동의 후 재동기화 | synced, 웹 읽는 중 및 기록 2건 유지. 실제 서버 GET은 reading/revision 2/source_revision 4로 로컬과 일치 |

승인된 신규 OpenAI 요청은 **2/8회**(번역 1회, 질문 1회)였다. 재시작 후 조회로 추가 호출하지
않았다. 프로세스 간 잠금 카운터로 8회를 넘는 요청은 차단하도록 했고, 기존 질문 승인 잔여
2회는 합산하지 않았다. 의미 검증·추가 요약 생성은 하지 않았다. 서버에는 동의된 합성
메타데이터/기록만 전송했으며 PDF·질문/답변·전체 원문/번역은 업로드하지 않았다.

Claude가 최초 등록에 지원하지 않는 제목 필드를 넣거나 질문 페이지 범위를 잘못 구성한
호출은 입력 검증에서 거부됐다. 수정된 입력으로 정상 실행했고 중복 등록/질문 실행은 없었다.
PDF 메타데이터 제목은 자동 반영되지 않아 제품과 웹은 파일명을 표시했다. 저장 요약이 없는
상세 카드에는 질문 근거가 자동 편입되지 않으므로 별도 get_evidence 카드로 확인했다.
이번 실행은 사용자 입력을 제공한 정상 생성/저장/조회 흐름이며, 오류 응답 주입이나
여러 origin 간 요청 키 자동 복구 검증으로 확대하지 않는다.

### 자동 회귀 및 정리

```sh
python -m pytest -q tests/unit/test_reading_state.py \
  tests/unit/mcp/test_server_sync_tools.py \
  tests/integration/test_annotation_reading_workflow.py
```

22 passed, 실패·skip 없음. 기존 anyio 폐기 예정 경고 1건. HTTP 대역의 자동 테스트와
위 실제 staging 결과를 구분한다. 제품 코드 결함은 새로 발견하지 않았다.
임시 Desktop 연결 설정은 원본 백업과 바이트 단위 일치하게 복원하고 기본 Desktop을
재실행했다. API 키·기기 자격·DB·PDF·스크린샷은 저장소에 포함하지 않는다.

### 이번 실행 이후 종료 전 남은 범위

- A: 실제 다른 계정의 **메모 API** 조회/갱신 거부, 미존재 대상과 오류 동등성, 소유 기록 보존.
- Windows에서 이번 새 PDF 단일 흐름 검증 또는 플랫폼별 지원 범위 합의.
- 원격 계정·기기·새 origin 간 생성 의도 복구는 #502 별도 범위다.

새 PDF 등록→실제 번역/색인→학습→완료→실제 질문→동의 전송→웹→재시작→완료 취소는
이번 macOS 실행으로 확인했다. #375와 #214는 위 남은 범위 때문에 열어둔다.
