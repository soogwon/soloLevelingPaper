# #222 실행 서비스와 #223 MCP 연결

기준 main `61bdc29`의 A 저장 계약을 사용한다. 실제 API 없이 임시 SQLite/Chroma와 합성
번역·임베딩으로 검증한다. A의 `repository.py`·`schema.py`는 변경하지 않는다.
실제 Desktop 검증은 남아 있다.

## B 실행 경계 (#222)

`workers.translation_recovery.TranslationRecoveryService`가 접수·실행·조회·명시적 정리를 제공한다.

| 메서드 | 동작 |
|---|---|
| `preview(version_id, base_embedding_set_id=None, request_key='preview')` | 기본 색인·성공 수·실패 수·실행 가능 여부만 조회. 생략한 기준은 현재 기본 색인으로 확정해 반환 |
| `prepare(version_id, base_embedding_set_id, request_key)` | 입력 해시와 A 접수 API로 작업 확보. 번역·임베딩 호출 없음 |
| `run(job_id)` | 이 인스턴스에서 신규 접수한 실행권만 한 번 소비. 다른 인스턴스·중복 호출은 조회만 수행 |
| `lookup(version_id, base_embedding_set_id, request_key)` | 이미 저장된 요청만 조회. 없으면 NOT_FOUND, 입력 불일치면 REQUEST_CONFLICT |
| `cancel_before_start(job_id)` | 아직 실행하지 않은 로컬 실행권만 취소하고 고정 실패 코드 저장 |
| `cleanup(job_id)` | 이 인스턴스가 실행 종료를 확인했고 실패/중단 확정된 작업의 미게시 예약 컬렉션만 명시적으로 정리 |

번역 설정은 기준 리비전과 같은 provider/model/prompt_version/target_language여야 한다.
구성된 제공자가 없거나 실행을 차단한 설정이면 신규 접수 전에 거부한다. 완료 요청의 재생은 현재
제공자 구성과 무관하게 가능하다. 원문 실패 청크만 TranslationService에 전달하므로 기존 제한
재시도가 유지되며 성공 청크를 재번역하지 않는다.

복구 번역은 A의 `save_recovery_translation`으로 새 리비전에 저장한다. 기존 성공 번역·context·
인용은 덮어쓰지 않는다. 성공 0개면 임베딩 없이 실패 상태와 NO_PROGRESS를 반환하고 기존 기본
색인을 유지한다. 1개 이상 복구되면 기존+새 성공 번역 전체를 기준 모델로 임베딩한다. 차원·개수·
유한 숫자(bool 제외)를 검사하고, A API로 새 색인 ID를 예약한 뒤 별도 Chroma 컬렉션에 쓴다.
저장된 ID·문서·벡터·메타데이터를 재검증한 후 A의 원자 게시 API를 사용한다.
빈 페이지 등 기존 추출 제한은 새 작업에도 보존하고, 이전 번역 실패 표지만 새 리비전의 실패
목록으로 교체한다. FULL은 실패 번역의 복구 완료이며 원본의 추출 제한까지 해소했다는 뜻이 아니다.

외부 번역·임베딩·벡터 처리 중 DB 쓰기 트랜잭션은 유지하지 않는다. 게시 함수가 커밋 뒤 예외를
내면 작업의 게시 결과를 다시 조회하며, 게시됐으면 실패 처리하거나 컬렉션을 삭제하지 않는다.
DB를 확인할 수 없으면 UNKNOWN으로 남기며 새 작업·자동 정리를 하지 않는다.

미게시 정리는 실행 실패만으로 자동 수행하지 않는다. 별도 명시적 `cleanup`에서 작업 상태·영속
예약·미게시를 재검사하고 해당 컬렉션만 삭제하며 실제 제거도 확인한다. 조회/삭제 실패는
INDEX_CLEANUP_FAILED로 처리한다. 게시 여부 불명이면 보존한다. 이 메서드를 공개 MCP 도구로
노출하지 않는다.

**재검토 보강:** DB의 interrupted 표지만으로는 늦은 벡터 writer의 종료를 증명하지 않는다.
동일 서비스 인스턴스가 해당 실행의 벡터 호출 완료와 소유 클라이언트 정리를 확인한 경우만
정리 자격을 기억한다. 실행 중, 다른 인스턴스, 재시작 뒤 종료 확인이 없는 작업, 소유 클라이언트
닫기 실패/미지원은 정리하지 않는다. A의 영속 예약·미게시 검사는 그대로 재사용한다. 재시작 후
잔여 컬렉션은 실제 이전 writer 종료를 확인하는 별도 운영 계약 없이는 보존하는 보수적 한계가 있다.

### C에 전달하는 결과

`RecoveryResult`는 키·원문·번역문·예외를 포함하지 않는다.

| 필드 | 의미 |
|---|---|
| `status` | ACCEPTED / IN_PROGRESS / COMPLETED / FAILED / INTERRUPTED / UNKNOWN / NO_TARGET / BASE_CHANGED / REQUEST_CONFLICT |
| `version_id`, `base_embedding_set_id`, `job_id` | 고정된 입력 범위와 저장 작업. 접수되지 않았으면 job_id가 없을 수 있음 |
| `new_embedding_set_id`, `result_available` | 확인된 게시 결과가 있을 때만 설정. 이후 다른 복구로 기본이 바뀌어도 해당 작업 결과 재생 |
| `outcome` | 확인된 완료의 FULL/PARTIAL, 성공 0개의 NO_PROGRESS. 그 밖에는 null |
| `target_count`, `recovered_count`, `remaining_failed_count` | 기준 실패 수·게시 증가 수·게시 후 실패 수. 확인 못한 수를 0으로 추측하지 않고 null |
| `reason_code` | 고정 오류 코드 또는 null |

단계는 translation_recovery / recovery_translation / recovery_embedding / recovery_index의 기존
안전 진단 형식으로 시간과 함께 기록한다. 고정 실패 코드만 허용하며 사용자 키·본문·원본 예외는
출력하지 않는다. 별도 저장 표나 로그 본문 수집을 추가하지 않는다.

## C 연결 (#223)

| MCP 도구 | 용도 |
|---|---|
| `prepare_translation_recovery` | version_id·request_key와 선택적 base_embedding_set_id로 수·전송·비용 안내. 작업 생성 없음 |
| `recover_translation` | 카드 전용. 위 안내에서 확정한 version_id·base_embedding_set_id·request_key와 일회성 토큰으로 접수 |
| `cancel_translation_recovery` | 카드 전용. 접수 전 동의 토큰을 취소하며 이미 접수된 작업의 중단 도구가 아님 |
| `get_translation_recovery` | 동일한 세 입력으로 상태만 조회. 새 작업/번역 없음 |

준비 응답의 본문은 상태·성공/실패 수·전송 안내와 세 입력을 제공한다. 일회성 토큰은 모델용
structuredContent/텍스트에 넣지 않고 카드 전용 `_meta`로만 보낸다. 기존 등록 카드의
RegistrationGate를 재사용해 범위·키 바꿔치기, 재사용, 만료(10분), 취소를 거부한다.
`confirmed=true` 입력은 승인 증거가 아니며 새 도구에도 그런 입력을 두지 않는다.

카드는 체크와 클릭 전 접수하지 않는다. 클릭 시 **토큰을 제외한 입력 세 개를 로컬에 먼저 보관**하고
그 저장이 실패하면 접수하지 않는다. 응답 유실·조회 실패·UNKNOWN에서는 키를 보존하고 자동
재시도/폴링하지 않는다. 새 카드도 보관한 요청을 조회할 뿐 새 접수를 하지 않는다. 확정된 종료 결과를
명시적으로 조회한 뒤 보관 요청을 해제하며, 새 복구는 새 안내와 동의가 필요하다.
단, 접수 응답이 NO_TARGET/BASE_CHANGED/REQUEST_CONFLICT이고 작업이 생성되지 않았음이
확인되면 그 로컬 기록을 해제한다. 오류 응답·응답 유실·UNKNOWN·모순된 응답은 이 예외에 포함하지 않는다.
늦은 응답은 변경된 카드에 적용하지 않는다. 기존 context 이동·새 context 생성은 자동 수행하지 않는다.

이 방식은 기존 등록과 마찬가지로 **호스트가 app 전용 메타데이터와 도구 가시성을 분리한다는 전제**다.
동일 사용자 권한의 악성 호스트를 인증하거나 모델의 대화 문구로 사용자 신원을 증명하는 장치가 아니다.
카드가 표시되지 않으면 텍스트로 대상·제한·이미 접수한 상태를 안내하고, 동의를 대신했다고 주장하거나
confirmed=true/DB 직접 호출로 우회 접수하지 않는다. 카드 메타데이터 중계 문제는 #242와 함께 확인한다.

RecoveryManager는 단일 작업 스레드와 최대 16개 대기/실행 Future를 관리한다. A의 버전별 접수
잠금이 중복 번역을 막고, 동일 키는 기존 결과를 재생한다. 제출 실패·종료 전 취소는 미시작 작업만
고정 실패로 확정한다. 런타임은 등록 작업과 복구 작업 모두 끝나기 전 공유 embedder/Chroma를 닫지 않는다.
원래 LOCAL_ONLY·외부 API 차단은 카드 동의로 변경되지 않는다.

## 검증과 남은 작업

- 합성 98개 청크에서 96 → 97 → 98개 게시, 이전 context·번역 보존
- 성공 0개, 설정 불일치/외부 호출 차단, 중복·동시 접수, 중단 후 상태 재생
- 잘못된 벡터, 저장 검증 실패, 게시 직전 기준 변경, 커밋 후 응답 유실, DB 상태 불명
- 명시적 소유 컬렉션 정리와 삭제 no-op/실패 방어, 다른 컬렉션 보존
- MCP 프로토콜에서 동의 전 번역 0회, 토큰 위조·교체·재사용·취소, FULL/PARTIAL/NO_PROGRESS
- UI의 키 보관 실패, 응답 유실 후 재조회, 늦은 응답, 본문 HTML 미실행, 기존 카드 회귀
- 기존 요청·오류 처리 및 공유 자원 지연 종료 회귀

재실행:

```powershell
python -m pytest tests/integration/test_translation_recovery_service.py tests/integration/test_translation_recovery_mcp.py tests/unit/test_translation_recovery.py tests/integration/test_recovery_readers.py -q
python -m pytest tests/unit/mcp/test_server.py tests/unit/mcp/test_runtime.py tests/unit/mcp/test_runtime_lifecycle.py tests/unit/mcp/test_registration_consent.py tests/unit/test_safe_diagnostic_stream.py -q
```

`ui/paper-detail`에서는 `npm test`와 `npm run build`를 실행한다. 네트워크/원본 데이터 없이
수행한 자동 검증이며 실제 Desktop 화면·로컬 저장 허용·호스트 메타데이터 격리를 대신하지 않는다.
실제 모델/API 실측은 하지 않는다. Desktop 확인 결과는 아래에 별도로 기록하며,
#222·#223은 이 문서만으로 닫지 않는다.
로컬 M1 단일 런타임의 시작 시 interrupted 확정 계약을 유지하며 다중 서버 임대·자동 재시작·주기적
복구·자동 맥락 전환은 구현하지 않는다.

자동 검증 기록 (Windows / Python 3.12.14):

| 검증 범위 | 결과 |
|---|---:|
| 재검토 보강 후 위 Python 두 명령의 관련 서비스·저장소·MCP·런타임·안전 필터 회귀를 한 번에 실행 | 409 passed |
| 기존 상세·등록·질문 카드와 신규 복구 카드 전체 UI | 45 passed |

Python 결과는 두 범위를 합친 한 번의 실행(96.49초)이다. wheel 빌드와 새 카드 HTML·
라이선스 및 세 Python 모듈의 패키지 포함·바이트 일치도 확인한다. wheel 검증은 Desktop 설치나
실제 호스트의 리소스 요청 검증이 아니다.

재검토에서 추가한 회귀는 중단 표식과 늦은 벡터 쓰기의 정리 경합, 다른 서비스 인스턴스의
종료 추측 거부, 소유 클라이언트 닫기 실패, 추출 제한 보존, 기존 context의 실제 검색·인용·요약
불변, 접수 거부 뒤 새 동의 안내 및 모순된 거부 응답의 키 보존이다. 동의 전 실행 0회, 같은 키
재생, 기준 색인 재확인, 복구 성공 0건 처리, 게시 후 응답 유실 방어는 기존대로 유지한다.

### 합성 Desktop 확인 (2026-11-09)

검토한 wheel을 별도 패키지 경로에 설치하고 `solo-paper-223-test` 전용 서버를 구성했다.
원본 DB 대신 합성 98개 청크(기존 성공 96개, 실패 2개)를 사용하며, 실제 제공자 생성과
외부 HTTP를 차단한다. Desktop 기존 커넥터 정의는 보존했다.

| 확인 경로 | 관측 결과 |
|---|---|
| 로컬 stdio MCP 클라이언트 | 준비 UI 메타데이터, 응답의 카드 전용 확인 토큰, HTML 리소스 반환 정상 |
| 추적 적용 후 재시작한 Desktop | 준비 조회 1회, AVAILABLE·대상 2개·성공 96개; UI 메타데이터와 확인 토큰을 서버에서 반환 |
| 같은 Desktop 추적 구간 | resources/read 요청 0회, 화면에 동의 카드 미표시 |
| 동의 전 부작용 | 복구 접수·합성 번역·임베딩 호출 0회, 복구 작업 0건 |
| 합성 DB 보존 | context 1개·색인 1개·성공 96개, 기존 context의 번역·색인 연결 유지 |

추적은 FastMCP에 이미 등록된 프로토콜 핸들러를 감싸고 기존 입력·출력 검증을 그대로
호출한다. 본문·확인 토큰·HTML은 기록하지 않는다. Python 메서드만 교체했던 이전 추적은
실제 프로토콜 요청을 포착하지 못했으므로 요청 부재의 근거에서 제외했다.

서버에서 보낸 메타데이터가 호스트·중계의 어느 단계에서 처리되지 않는지는 아직 확정하지
못했다. 카드 미표시 문제는 #242와 연결해 확인한다. 동의 토큰 추출이나 모델의 대리 호출로
접수를 우회하지 않았다. **사용자 동의 → 접수 → 상태 조회의 Desktop 전체 검증은 남아 있다.**
이 확인에서는 실제 API 호출과 원본 데이터 변경, 커밋·푸시를 하지 않았다.

PR 준비 시 재실행: 관련 Python 회귀 409 passed (72.30초), UI 45 passed,
카드 빌드와 오프라인 wheel 빌드를 확인했다. wheel의 복구 모듈 3개·카드 HTML·라이선스가
소스와 바이트 단위로 일치했다. 기본 pytest 임시 폴더의 접근 거부는 작업 영역 안의 새
전용 경로를 `--basetemp`로 지정해 검증했으며, 제품 코드는 이 문제로 변경하지 않았다.
