# 번역문 하이라이트 지원안 — #373

#214의 번역문 지원 범위를 검토하기 위한 구현안이다. A·B·C의 합의가 완료됐다고 간주하지
않으며 Draft PR에서 저장·번역 리비전 계약을 검토한다.

- MCP create_annotation의 target=translation과 translation_revision_id를 지원한다.
- context의 번역 리비전과 같아야 하며 해당 리비전의 파싱 대상도 청크와 같아야 한다.
- translation_results의 성공한 저장 번역문만 기준으로 삼는다. 현재 chunks.text로 대체하지 않는다.
- 문자 위치는 코드 포인트 [시작, 끝)이며 정확한 인용문 일치가 필수다. 문자열을 정규화하지 않는다.
- 번역 누락은 TRANSLATION_NOT_AVAILABLE, 리비전 불일치는 TRANSLATION_REVISION_MISMATCH,
  위치 초과는 OFFSET_OUT_OF_RANGE, 인용 불일치는 QUOTE_MISMATCH다.
- 저장한 인용·리비전은 변경하지 않는다. 현재 번역이 없거나 바뀌면 조회 시 위치를 사용 불가로 표시한다.
- 원문·번역 구간을 한 메모에 함께 넣을 수 있으나 생성 후 구간 수정은 여전히 금지한다.
- 서버 전송은 기존 segments 계약을 재사용해 target·리비전·인용·위치를 보존한다.

상세 카드는 저장 번역에서 인용 위치가 유일하게 확인되는 경우 번역문 인용 전체 하이라이트를
제공한다. 원문 구간별 기록 건수에는 섞지 않고 context 전체 기록에서 조회한다.
단건 메모 카드는 원문과 저장 번역문을 구분한다. PDF 마우스 선택이나 PDF 좌표 이동은 제공하지 않는다.
실제 Desktop 버튼 동작 및 서버·웹 표시 검증은 #375에서 별도로 확인한다.
