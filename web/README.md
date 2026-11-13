# Solo Leveling 외부 페이지 (웹 아카이브)

로컬 MCP에서 학습한 논문·요약·메모를 로그인해서 다시 보는 웹 화면입니다.
빌드 도구 없이 **HTML·CSS·JavaScript(ES 모듈)**만 사용합니다.

기본 실행은 같은 origin의 `/v1` **실제 서버 API**에 연결됩니다. 서버 없이 화면 예시만 확인하려면
주소의 hash 앞에 `?mock=1`을 붙입니다(예: `http://localhost:5173/?mock=1#/papers`).

## 실행 (VS Code)

ES 모듈은 `file://`로 열면 동작하지 않아서 정적 서버가 필요합니다. 둘 중 편한 방법을 쓰면 됩니다.

**방법 1. Live Server 확장 (추천)**

1. VS Code 확장에서 **Live Server**(Ritwick Dey)를 설치합니다.
2. 탐색기에서 `web/index.html`을 오른쪽 클릭 → **Open with Live Server**.
3. 브라우저가 `http://127.0.0.1:5500/web/index.html`로 열립니다. 파일을 저장하면 자동으로 새로고침됩니다.

**방법 2. VS Code 터미널에서 파이썬 서버**

```bash
cd web
python3 -m http.server 5173
```

위 명령은 정적 파일만 제공합니다. 예시 데이터로 확인할 때는
`http://localhost:5173/?mock=1#/papers`를 엽니다. 실제 로그인과 논문 조회를 확인하려면
같은 origin에서 웹 파일을 제공하고 `/v1` 요청을 FastAPI로 전달하도록 프록시를 설정합니다.

`?mock=1` 예시 데이터는 메모리에만 있어서 새로고침하면 처음 상태로 돌아갑니다.

## 화면과 주소

| 화면 | 주소 | 사용하는 API (요청서 번호) |
|---|---|---|
| 논문 목록 | `#/papers?status=reading&page=2` | `GET /v1/papers` (2) |
| 논문 상세 | `#/papers/{논문 ID}?record=&version=&annotation=&mpage=` | `GET /v1/papers/{id}`, `GET /v1/annotations`, `GET /v1/annotations/{id}`, `GET …/file-access`, `POST /v1/resume-requests` (3, 9.4, 10.3) |
| 메모·하이라이트 | `#/notes?kind=&range=&from=&to=&sort=&page=` | `GET /v1/annotations` (4) |
| 컬렉션 목록 | `#/collections?page=` | `GET·POST /v1/collections` (5) |
| 컬렉션 상세 | `#/collections/{컬렉션 ID}?page=` | `GET·PATCH·DELETE /v1/collections/{id}`, `GET …/papers`, `PUT·DELETE …/papers/{논문 ID}`, `GET /v1/papers?unassigned=true` (6) |
| 검색 | `#/search?q=` | `GET /v1/papers?q=` + `GET /v1/annotations?q=` (11) |
| 계정·연결 기기 | `#/account` | `GET /v1/me`, `GET·DELETE /v1/devices`, `POST /v1/auth/logout` (7) |
| 로그인 | `#/login` | `GET /v1/auth/login` (7.2) |

필터와 페이지 번호는 주소에 남아서 새로고침하거나 링크를 공유해도 같은 화면이 열립니다.

### 뒤로 가기

상단의 ‘뒤로’ 버튼은 앱 안에서 바로 전에 본 화면으로 돌아갑니다(브라우저 뒤로 가기와 같음).
필터·페이지 번호를 바꾸는 것은 기록을 쌓지 않으므로, 상세에서 뒤로 가면 보던 목록의 같은 페이지·필터로 돌아옵니다.
링크로 바로 들어와 이전 화면이 없으면 상위 화면(예: 논문 상세 → 논문 목록)으로 이동합니다.

### 페이지 번호

요청서 1.5에 맞춰 **모든 목록을 페이지 번호 방식**으로 조회합니다. ‘더 보기’·무한 스크롤은 없습니다.

모든 목록은 **한 페이지에 5개**씩 보여줍니다. 개수는 `js/config.js`의 `PAGE_SIZE` 하나로 바꿀 수 있고, 요청할 때 `limit`으로 보냅니다(서버 기본값 20, 최대 100).

| 화면 | 한 페이지 개수 |
|---|---|
| 논문 목록 | 5개 |
| 논문 상세의 메모·하이라이트 | 3개 (`paper-detail.js`의 `MEMO_PAGE_SIZE`) |
| 메모·하이라이트 | 5개 |
| 컬렉션 목록 | 5개 |
| 컬렉션 상세 | 5개 |
| 컬렉션 상세 → 논문 추가 대화상자 | 5개 |
| 검색 결과 미리보기 | 논문·메모 각 5개 (‘모두 보기’로 목록 화면 이동) |

- 필터·검색어·정렬을 바꾸면 1페이지부터 다시 조회합니다.
- 범위를 넘는 페이지(예: 마지막 페이지의 항목을 모두 뺀 경우)는 서버가 빈 목록을 주면 마지막 페이지로 다시 요청합니다.

## 폴더 구조

```text
web/
├─ index.html              공통 틀(사이드바·검색창)
├─ css/style.css           색·간격 토큰과 전체 스타일
└─ js/
   ├─ config.js            API 주소, 예시 데이터 사용 여부, 목록 개수
   ├─ main.js              로그인 확인, 화면 전환, 전역 이벤트
   ├─ router.js            해시 주소 → 화면
   ├─ api/
   │  ├─ index.js          화면이 쓰는 API 함수 모음 (여기만 바꾸면 연결 끝)
   │  ├─ client.js         fetch, 쿠키 세션, CSRF, 오류 변환
   │  ├─ mock-server.js    요청서 규칙대로 동작하는 가짜 서버
   │  └─ mock-data.js      예시 데이터
   ├─ lib/                 HTML 이스케이프, 날짜·위치 표시, 공통 UI
   └─ pages/               화면별 코드
```

## 백엔드와 연결하기

1. 같은 origin 배포는 별도 설정 없이 `/v1`을 사용합니다.
2. API origin이 다르면 HTML이 모듈을 불러오기 전에
   `globalThis.SOLO_LEVELING_CONFIG = { API_BASE_URL: 'https://api.example.com/v1' }`를 설정합니다.
3. 응답 필드 이름이 아래 가정과 다르면 `js/api/index.js`에서 변환합니다. 화면 코드는 고치지 않아도 됩니다.
4. 프론트와 API 도메인이 다르면 서버에서 허용 출처(CORS)와 쿠키 정책을 설정해야 합니다 (요청서 7.3).

### 프론트가 가정한 응답 형태

요청서에 필드 이름이 정해져 있지 않아서 아래처럼 가정했습니다. 팀원과 이름을 맞춰 주세요.

- **목록 공통 (페이지 번호 방식)**: 요청 `page`(1부터), `limit` → 응답 `{ items, total_count, page, page_size, total_pages }`. `total_pages`가 없으면 프론트가 `total_count`로 계산합니다. 범위를 넘는 `page`는 빈 `items`를 돌려주면 프론트가 마지막 페이지로 다시 요청합니다.
- **오류**: `{ "error": { "code", "message", "field", "request_id" } }`
- **논문 목록 항목**: `paper_id`, `title`, `file_name`, `authors`(문자열 배열) 또는 `author_display`, `published { year, month, day, precision }`, `selected_learning_record_id`, `selected_version_id`, `last_position`, `last_studied_at`, `reading_status`(`not_started|reading|completed|unknown`), `collection_id`
- **위치**: `{ pdf_page, printed_page_label, section_number, section_title, chunk_id }`
- **논문 상세**: 목록 항목 + `version { version_id, file_name, created_at }`, `summary`(없으면 `null`), `file_access { available, reason, message }`, `resume { available, reason, message }`
- **원문 열람 (9.4)**: `{ status: available|not_uploaded, url, expires_at, file_name, file_size, content_type }`. PDF가 없으면 오류가 아니라 `status: not_uploaded`, `url: null`
- **컬렉션 포함 논문 (6.3)**: `GET /v1/collections/{id}/papers` 방식을 사용합니다. 항목은 `paper_id, title, file_name, authors, published, added_at`
- **요약**: `{ summary_id, created_at, author_type, items: [{ kind: claim|method|result|limitation, text, evidence_ids, evidence? }] }`
- **메모·하이라이트**: `annotation_id`, `kind`(`note|highlight`), `body`, `segments: [{ target: original|translation, quote, position }]`, `paper { paper_id, title }`, `version_id`, `learning_record_id`, `created_at`(모르면 `null`), `updated_at`, `uploaded_at`, `author_type`(`user|ai|null`)
- **메모 목록 추가 값**: `date_counts { "YYYY-MM-DD": n, "unknown": n }`. 현재 필터 전체 기준의 날짜별 개수입니다 (요청서 4.5).
- **이어하기**: `{ resume_request_id, code, expires_at, prompt_text, status, reason }`

### 요청 방식

- 로그인은 동일 출처 쿠키 세션을 가정했습니다(`credentials: 'include'`). 변경 요청에는 `csrf_token` 쿠키 값을 `X-CSRF-Token` 헤더로 보냅니다.
- 컬렉션 생성과 이어하기 요청에는 `Idempotency-Key` 헤더로 중복 요청 키를 보냅니다.
- 메모 날짜 필터는 `from`, `to`(`YYYY-MM-DD`, 한국 날짜, 양 끝 포함)로 보냅니다.

## 팀에 확인할 것

- 요약 근거를 펼쳐 볼 원문 인용(`evidence`)을 상세 응답에 넣을지, 아니면 별도 조회 API를 둘지 정해야 합니다. 지금은 `evidence`가 있으면 펼쳐 보이고, 없으면 연결된 근거 개수만 표시합니다.
- 메모 작성 주체(`author_type`)는 현재 공통 DB에 없습니다. 값이 없으면 배지를 표시하지 않습니다.
- MCP의 PC 연결 승인 화면(요청서 7.4)은 인증 담당자의 연결 흐름이 정해진 뒤 추가합니다.
- 원문 열람 주소(`file-access`)는 임시 URL이라 매번 새로 요청합니다. 예시 데이터에는 실제 파일이 없습니다.
- 요청서에 ‘다음 커서’가 남아 있는 곳: 3.3 필요한 응답, 5.2 응답, 6.3 요청. 프론트는 모두 페이지 번호 방식으로 구현했습니다.

메모 조회 예시: `GET /v1/annotations?kind=highlight&from=2026-11-14&to=2026-11-14&sort=newest&page=2&limit=20`.
날짜 범위는 한국 시간의 원래 작성일을 기준으로 양 끝을 포함한다. 같은 날 25건이면 두 번째 페이지의
`items`는 5건이고 `date_counts`는 `{"2026-11-14":25}`이다. 세 번째 페이지는 같은 집계와 빈
`items`를 반환한다. 검색 결과가 전혀 없으면 `total_count`와 `total_pages`는 0이고 `date_counts`는 빈 객체다.
`page=0`, `limit=101`, 역순 날짜 범위는 422를 반환한다. 현재 동기화 계약은 원래 작성 시각을
필수로 받으므로 작성일 미상 기록을 생성하지 않는다. 위치 없는 메모의 `segments`는 빈 배열이다.
목록 항목과 `GET /v1/annotations/{annotation_id}` 응답의 형식은 같다.
