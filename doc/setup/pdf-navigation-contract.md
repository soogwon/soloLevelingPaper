# #232 PDF 위치·폴백 — B 구현 범위

## A 합의 재사용

- 저장 인용문 이동과 실제 PDF 페이지 이동은 구분한다.
- 이동 기준은 `version_id + parse_revision_id + pdf_page`이며, 페이지는 1부터 시작한다.
- `printed_page_label`은 표시용이다. 페이지 계산에 사용하지 않는다.
- 좌표 데이터가 없으므로 `coordinate_target`은 항상 `null`이다. 텍스트 오프셋이나
  텍스트 검색으로 좌표를 추측하지 않는다. 정확한 강조는 별도 A 구현 범위다.
- 파일 없음·접근 거부·만료·다른 버전에서는 저장 인용문을 유지한다.
- 상세 조회 자체는 파일 열기·업로드·다운로드·URL 발급을 시작하지 않는다.

## 구현과 호출 순서

`PdfNavigationService`는 읽기 전용 `SQLitePdfSourceReader`로 기존 DB와 context·출처를 먼저
확인한다. 없는 DB나 손상된 출처에서 기존 근거 조회를 시작하지 않는다.
이후 기존 `EvidenceService.get`의 context 소속·근거 ID·원문/번역 인용 검사를 그대로 수행한다.
projection은 기존 테이블의 원문 버전·파일 해시·파싱 페이지 수만 조회한다.
`stored_path`를 조회하지 않으며 스키마나 저장 함수를 바꾸지 않는다.

```text
context + evidence ID
  → 읽기 전용 DB·context·출처 확인
  → 기존 근거 검증
  → 저장 버전·파싱 리비전·물리 페이지 범위 검증
  → 접근 결과 없음: excerpt_only
  → 사용자 명시 동작 후 C의 file-access 결과가 있을 때만 비교
       → 같은 버전·해시·페이지 수: page
       → 접근 오류·버전/해시 불일치: excerpt_only + 고정 사유 코드
       → 페이지 범위·페이지 수 불일치: DataIntegrityError, PDF 이동 금지
```

두 조회는 별도 읽기 스냅샷이다. 서비스는 반환된 context·버전·파싱 연결을 대조하지만
하나의 원자적 DB 스냅샷이나 실제 파일 접근 권한을 보장하는 서비스는 아니다.
기존 리비전·원문 파일의 불변 계약을 전제로 하며, 검증과 실제 열기 사이의 파일 교체·권한 변경은
#215의 파일 핸들/접근 계약에서 방어해야 한다.

## C 연결에 제공하는 내부 인터페이스

```python
target = service.get(context_id, evidence_id)  # 파일 접근을 시도하지 않음
target = service.get(context_id, evidence_id, access=verified_access)
output = target.to_dict()
```

`verified_access`는 `FileAccessResult`다. 성공이면 C의 신뢰하는 file-access 경로가 확인한
`version_id`, `file_hash`, 실제 `page_count`를 받는다. 실패면 고정 상태만 받는다.
URL·로컬 경로·예외 본문은 이 인터페이스에 포함하지 않는다.

**이는 #215의 확정된 외부 응답 형식이나 새 MCP 입력 계약이 아니다.**
모델/사용자가 임의로 전달한 버전·해시는 파일 접근 증명으로 사용할 수 없다.
C가 성공/만료/권한 상태와 실제 파일의 검증값을 확인한 뒤 내부 객체로 변환해야 한다.
파일 해시는 기존 저장값과 정확히 비교하며 임의 정규화하지 않는다.
서비스가 URL의 만료 시각이나 파일 내용을 직접 검사하지는 않는다.

기존 상세 응답·MCP 도구·카드·runtime에는 아직 연결하지 않았다.
C의 명시 동작 연결은 #215 계약과 함께 진행하며, 이번 코드는 위치 판정 서비스만 제공한다.

## 출력 및 B 추가 제안

정상 예시(좌표 없음):

```json
{
  "version_id": "v1",
  "parse_revision_id": "p1",
  "pdf_page": 6,
  "printed_page_label": "iv",
  "navigation_level": "page",
  "coordinate_target": null,
  "fallback": "stored_excerpt",
  "reason_code": null
}
```

기본 위치 필드는 A 제안을 따른다. 아래 `reason_code`는 **B 내부 판정의 추가 제안**이며,
최종 MCP/UI 코드 매핑은 C와 확인해야 한다.

| 상황 | 이동 수준 | reason_code |
|---|---|---|
| 아직 접근 확인 안 함 | excerpt_only | access_not_checked |
| 파일 없음 | excerpt_only | file_missing |
| 권한 거부 | excerpt_only | access_denied |
| 접근 URL 만료 | excerpt_only | access_expired |
| 그 밖의 접근 불가 | excerpt_only | access_unavailable |
| 다른 원문 버전 | excerpt_only | version_mismatch |
| 같은 버전 ID지만 다른 파일 해시 | excerpt_only | file_hash_mismatch |
| 같은 버전·해시·페이지 수 | page | null |

`fallback`은 정상일 때도 `stored_excerpt`로 유지한다. PDF 이동 실패가 기존 인용문을 지우는
이유가 되지 않기 때문이다. 반환 객체는 원문·번역·경로·URL·파일 해시를 포함하지 않는다.

없는 context·근거는 기존 `ResourceNotFoundError`를 유지한다. 저장 데이터의 파싱 연결 오류,
페이지 범위 오류, 파일과 파싱의 전체 페이지 수 불일치, 손상된 출처는
고정 문구의 `DataIntegrityError`로 중단한다. 일반적인 접근 실패와 무결성 오류를 섞지 않는다.
C는 이 오류를 받으면 PDF 이동을 금지하고 기존에 표시한 유효한 저장 인용문을 유지해야 한다.
다른 버전이나 최신 파싱으로 자동 대체하지 않는다.

만료 후 재시도도 사용자가 명시적으로 요청했을 때 C가 수행한다. B 서비스는 접근 재시도,
재등록, 재번역, URL 재발급을 수행하지 않는다.

## 검증

```powershell
python -m pytest tests/unit/test_pdf_navigation.py -q
```

합성 객체·임시 SQLite에서 정상/접근 미확인/오류별 폴백, 잘못된 페이지·버전·해시·파싱 연결,
원문·번역 변조, context 격리, 잘못된 근거 ID, 오류 후 명시적 정상 재확인, DB 바이트 불변,
없는 DB 미생성과 출력의 경로·해시·본문 미노출을 확인한다.

기존 상세·메모·MCP 회귀도 함께 실행한다. 실제 API·원본 DB·실제 PDF 파일은 사용하지 않는다.

### 2026-11-08 검증 결과

- Windows / Python 3.12.14에서 기존 상세·메모·MCP·키워드 출처 회귀 219개가 통과했다.
- 신규 테스트 42개를 함께 실행한 통합 결과는 261 passed였다.
- 추가 검토에서 실제 존재하는 다른 context의 근거 거부를 보강하고, 매 호출 파일 해시
  재검증 사례를 추가한 뒤 최종 신규 테스트 43개가 모두 통과했다.
- A의 DB 파일·기존 검증·MCP 계약·카드 코드는 변경하지 않았다.
- 실제 API 호출·원본 데이터 변경·Desktop 설치·커밋·푸시는 수행하지 않았다.

### 재검토에서 보완한 사항

- projection 단독 검사는 없는 DB를 만들지 않았지만, 서비스 전체에서는 기존 근거 조회기가
  먼저 DB를 열어 빈 파일을 만들고 SQLite 오류를 반환할 수 있었다. 서비스 수준에서 재현한 뒤
  읽기 전용 출처 확인을 앞당겨, 해당 경우 근거 조회를 시작하지 않도록 보완했다.
- A의 “페이지 범위 초과는 데이터 무결성 오류” 계약에 맞춰 파일의 페이지 범위·전체 페이지 수
  불일치를 일반 폴백 사유에서 `DataIntegrityError`로 분리했다. 버전·해시 불일치의 폴백은 유지한다.
- 저장 요약·메모가 있는 context에서 폴백과 무결성 오류를 각각 발생시킨 뒤 상세 응답과 메모
  목록·DB 바이트가 동일함을 확인했다. 신규 회귀는 보완 후 45개가 통과했다.
- 최종 통합 재실행은 신규 45개와 기존 상세·메모·MCP·출처 회귀 219개를 합쳐
  **264 passed**였다. 실제 API·원본 DB는 사용하지 않았다.
