"""검증된 저장 요약만 Markdown 텍스트로 내보낸다. 외부 조회·생성은 없다."""
from html import escape
from uuid import UUID


def export_summary(saved):
    result = saved['result']
    context, key = str(UUID(saved['context_id'])), str(UUID(saved['request_key']))
    if saved['state'] != 'completed' or result['context_id'] != context or result['binding']['context_id'] != context:
        raise ValueError('summary_export_invalid')
    def safe(value):
        # Markdown의 링크·이미지·HTML을 실행 가능한 내용으로 내보내지 않는다.
        text = escape(str(value), quote=False)
        for char in ('\\', '`', '*', '_', '[', ']', '#', '!', '|', '>'):
            text = text.replace(char, '\\' + char)
        return text.replace('\r', '').replace('\n', '  \n')
    b = result['binding']
    lines = ['# 저장된 원격 요약', '', f"요청 키: {key}", f"학습 맥락: {context}"]
    for label, value in [('논문',b['paper_id']),('판본',b['server_version_id']),('게시',b['publication_id']),
                         ('파싱 리비전',b['parse_revision_id']),('번역 리비전',b['translation_revision_id']),
                         ('범위',result['scope']),('섹션',result['section_id']),('모델',result['model']),
                         ('프롬프트',result['prompt_version']),('생성 시각',result['generated_at'])]:
        lines.append(f'{label}: {safe(value)}')
    lines += ['', '검증 범위: 구조·근거 ID 소속 확인. 의미적 정확성은 별도 검증이 필요합니다.', '']
    for item in result['items']:
        lines += [f"## {safe(item['kind'])}", '', safe(item['text']), '',
                  '근거 ID: '+', '.join(safe(e) for e in item['evidence_ids']), '']
    return dict(status='ok',context_id=context,request_key=key,publication_id=b['publication_id'],
                media_type='text/markdown',filename=f'summary-{key}.md',markdown='\n'.join(lines))
