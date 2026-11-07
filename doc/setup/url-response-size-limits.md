# URL 응답 수신 크기 제한 (#199)

URL 등록은 응답을 메모리에 모두 받은 뒤 저장 크기를 확인하지 않는다. `register_and_ingest_url`의
`max_pdf_bytes`를 URL 수집 계층에 전달하고, PDF 응답을 스트리밍하는 동안 실제 수신 바이트를 누적해 같은 상한을
적용한다.

PDF 후보 판정은 최종 응답의 `Content-Type`, 최종 URL의 `.pdf` 확장자, 또는 처음 수신한 `%PDF-` magic bytes 중
하나를 사용한다. 최종 content 분류와 저장 단계도 `%PDF-`를 같은 magic 기준으로 사용한다. 메타데이터로 종류를
확정할 수 없는 응답은 작은 쪽 상한만으로 `Content-Length`를 조기 거부하지 않고, 최대 5바이트 prefix를 먼저 확인한
뒤 PDF 또는 기타 상한을 선택한다. 두 상한을 모두 넘는 선언 길이는 prefix 수신 전에도 안전하게 거부한다.
`Content-Length`가 없거나 실제보다 작고, chunked 전송이거나 리다이렉트 뒤의 응답이어도 실제
수신량 검사는 동일하다. 정확히 상한인 응답은 허용하고 한 바이트를 넘으면 `URL_RESPONSE_TOO_LARGE`로 중단한다.
오류에는 URL, 쿼리 문자열, 서버 헤더를 넣지 않는다.

HTML과 기타 응답은 PDF 저장 설정과 분리된 `other_max_bytes`를 사용하며 기본값은 기존과 같은 50 MiB다. 따라서
작은 PDF 저장 상한이 정상 HTML 페이지 등록까지 제한하지 않는다. 두 제한 모두 1 이상의 정수여야 한다.
