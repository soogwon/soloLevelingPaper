"""공개 API를 호출하지 않는 합성 Responses 본문. 실제 의미 판정/토큰 추정기가 아니다."""
import json

from solo_leveling.application.evidence_qa.answer_audit import COVERAGE_CHECKS


def synthetic_answer_response(payload):
    data = json.loads(payload['input'][0]['content'])
    kind = payload['text']['format']['name']
    if kind == 'evidence_claims':
        item = data['evidence'][0]
        content = {'claims': [{'text': item['text_ko'], 'evidence_ids': [item['evidence_id']],
            'supports': [{'evidence_id': item['evidence_id'], 'quote_original': item['original_text']}]}]}
    elif kind == 'semantic_judgments':
        judgments = []
        for claim in data['claims']:
            # 이 fixture는 한 완결 문장만 사용하는 합성 사례다.
            quote = next(q for q in data['original_quotes'] if q['evidence_id'] in claim['evidence_ids'])
            checks = {name: {'status': 'not_applicable', 'quote_ids': []}
                      for name in ('content', 'direction', 'conditions', 'causality', 'metrics')}
            checks['content'] = {'status': 'supported', 'quote_ids': [quote['quote_id']]}
            judgments.append({'claim_id': claim['claim_id'], 'checks': checks,
                              'verdict': 'supported', 'reason': 'full_support'})
        content = {'judgments': judgments, 'answer_audit': {'coverage': [{
            'question_part': data['question'], 'status': 'answered',
            'claim_ids': [c['claim_id'] for c in data['claims']],
            'checks': {name: {'status': 'satisfied', 'quote_ids': [data['answer_quotes'][0]['quote_id']]}
                       for name in COVERAGE_CHECKS}}], 'repairs': []}}
    else:
        raise AssertionError('unexpected synthetic provider stage')
    return {'status': 'completed', 'usage': {'input_tokens': 8, 'output_tokens': 4},
        'output': [{'type': 'message', 'role': 'assistant', 'status': 'completed',
                    'content': [{'type': 'output_text', 'text': json.dumps(content, ensure_ascii=False)}]}]}
