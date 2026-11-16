# #214 후속 통합 검증 — #375

2026-11-14에 Windows 합성 데이터 자동 검증을 수행했고, 2026-11-16에 실제 Windows
Claude Desktop과 staging 웹 검증을 추가했다. 최신 main `437131e`에서는 A의 staging migration
읽기 전용 확인과 #489·#491의 실제 PostgreSQL CI 검증이 완료됐다. 실제 인증 API 격리와
macOS Desktop→서버→웹 전체 검증은 남아 있으므로 #375 전체 완료로 기록하지 않는다.

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

## 최신 검증 체크리스트

DB 검증의 실행 환경과 제품 종단 검증을 구분한다. 아래 완료 표시는 A의 실제 DB 보고와
CI 결과를 대조한 것이며, 이 문서 갱신 중 DB나 Desktop을 다시 실행한 것은 아니다.

- [x] staging migration 전체 이력·0032 제약·제품 도구 `--check`: A 읽기 전용 확인.
- [x] 기존 데이터 이관·제품 도구 재실행 불변성: #489의 폐기 가능한 PostgreSQL CI.
- [x] 실제 PostgreSQL 읽기 상태 CAS·동시 재생·완료 보존·owner/source 저장소 경계: #491.
- [ ] 실제 인증 API의 owner/source 격리와 거부 상태 코드·실패 후 기록 보존.
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

- [x] staging 이력·0032 적용/제약은 A가 읽기 전용 확인했다. 재실행 안전성은 staging 원본 대신 #489의 일회용 DB에서 확인했다.
- [ ] macOS Claude Desktop에서 실제 서버 동기화와 웹 대조까지 확인한다.
- [ ] Windows 현재 OS/Claude·설치 소스 대응은 후속 코멘트에서 회수했다. 당시 wheel 해시·실험별 배포 SHA는 확정되지 않아 다음 종단 검증에서 시작/종료 시각과 함께 고정한다.
- [ ] 실제 배포 환경에서 context 없는 초기 not_started 전송, 완료 후 추가 질문, 지연된 상태 전송의 CAS 충돌을 확인한다.

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
계정·source 경계는 실제 인증 API 증거가 필요하다. PostgreSQL 저장소 CAS/행 잠금·migration
안전성의 후속 증거는 아래 #489·#491 결과로 추가됐다.
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

## DB 검증 완료 근거 — main 437131e

| 항목 | 완료 증거 | 한계 |
| --- | --- | --- |
| staging 이력·0032 제약 | [A 읽기 전용 결과](https://github.com/soogwon/soloLevelingPaper/issues/375#issuecomment-6907483564): PostgreSQL 18.6, main af64094 파일 기준 32건 연속, name/checksum 일치, `server schema is current`, unknown 기본값·NOT NULL·허용값 CHECK 확인 | DB 변경·재배포·staging 원본 이관 재실행은 하지 않음 |
| 제품 도구 기존 행 이관·재실행 | [PR #489](https://github.com/soogwon/soloLevelingPaper/pull/489): 0032 전 합성 3행 → unknown 이관 → user/ai/unknown 보존, 전체 행·이력·체크섬·적용 시각 불변. CI 1 passed / 0 skipped 강제 | 폐기 가능한 PostgreSQL 16 CI이며 운영 데이터 이관 시험은 아님 |
| 읽기 상태 실제 트랜잭션 | [PR #491](https://github.com/soogwon/soloLevelingPaper/pull/491): 동시 CAS 하나 성공, 같은 operation 동시 재생, 늦은 start·낮은 source revision·다른 basis reopen 거부, owner/source 거부. CI 2 passed / 0 skipped 강제 | 저장소 직접 호출이며 실제 인증 HTTP 경계 검증은 아님 |

#489와 #491은 각각 최신 head의 전체 CI 9개가 통과한 뒤 main에 병합됐다.
현재 main과 A가 검사한 af64094 사이에는 서버 migration 변경이 없지만, 다음 실험 시점의
실제 배포 SHA와 이력은 다시 고정해야 한다. 저장소 main을 현재 배포 버전으로 추정하지 않는다.

## A에게 필요한 실제 인증 API 격리 확인

테스트 계정 2개와 별도 합성 자료/등록된 source를 사용하고, 토큰·DSN·원문·개인 식별값은
기록에 넣지 않는다. 실제 유효한 인증 수단을 사용하며 TestClient/합성 인증기를 대체 증거로 쓰지 않는다.

| 사례 | 확인할 결과 |
| --- | --- |
| 정상 소유자 대조군 | 자신의 메모·논문·읽기 상태 조회/허용된 갱신 성공, 대상 ID·revision 확인 |
| 다른 계정의 직접 요청 | 동일 대상의 목록/상세 조회와 갱신 거부, 실제 HTTP 코드·고정 오류 기록. 존재하지 않는 대상과 비교해 본문·존재 여부가 노출되지 않음 |
| 같은 계정의 다른 source | source 제한이 있는 실제 동기화·읽기 상태 API에서 다른 등록 source로 접근/갱신 거부. 웹 계정 조회와 기기 source 권한을 혼동하지 않음 |
| 누락·무효·폐기 자격 | 라우트의 인증 계약에 맞게 거부. 새 인증 자격을 만들거나 권한을 넓혀 우회하지 않음 |
| 거부·충돌 후 불변 | 정상 계정으로 다시 조회하여 메모 건수·본문·구간·origin·읽기 상태·revision이 이전과 같음 |
| 지연·재생 요청 | 승인된 범위의 동일 operation 재생은 추가 revision/중복 없음. stale revision·다른 basis 변경은 거부되고 completed 보존 |

결과에는 서버/웹 SHA·migration·시작/종료 시각, 인증 종류, 합성 대상 식별 별칭,
기대/실제 HTTP 코드와 안전한 오류 코드, 전후 건수/revision을 남긴다.
웹 화면의 ‘기록 없음’이나 저장소 PaperNotFound만으로 이 표를 완료 처리하지 않는다.

## 남은 C 종단 검증과 종료 판단

1. A의 API 결과와 같은 배포를 고정하고 macOS 설치 wheel SHA·OS/Claude 버전을 기록한다.
2. 별도 합성 자료로 메모·원문/번역문 하이라이트 저장 → 명시적 동의 → 서버 → 웹을 대조한다.
   건수·ID·본문·구간·원래 시각·신고 주체·revision을 확인한다.
3. context 없는 not_started 전송 → 학습 시작 → 완료 → 추가 질문 → 재시작 → 명시적 reopen의
   상태와 기준 버전을 확인한다. 질문의 실제 유료 API 전송이 필요하면 자료/비용 범위를 먼저 승인받는다.
4. 전송 실패 후 로컬 기록 보존과 같은 승인 작업 재개 시 서버 중복 없음도 확인한다.
5. 응답 유실 후 키 복구의 지원 범위를 확정한다. #490은 C 내부 모듈이며 제품 카드 미연결이다.
   새 시크릿 origin·저장소 유실·여러 기기 복구는 해결되지 않았다. 이를 제외하려면 #375 선행 조건에
   따른 명시적 범위 합의와 후속 티켓 연결이 필요하며, 단순 검증 통과로 대체하지 않는다.

위 항목을 확인하거나 명시적으로 범위를 합의한 뒤 #375 종료를 판단한다. #214 종료는 별도로
완료 조건 전체를 대조한다. 삭제 전파 #368과 PDF 마우스 선택 자동 수집은 계속 별도 범위다.
