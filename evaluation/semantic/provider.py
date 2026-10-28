"""기존 어댑터 연결. 실행 여부와 외부 전송 설정을 모두 확인한다."""

from copy import deepcopy

from evaluation.semantic.runner import evaluate


def select_cases(data, *, split='development', case_ids=(), include_drafts=False):
    if split not in ('development', 'holdout', 'all') or type(include_drafts) is not bool:
        raise ValueError('EVALUATION_OPTIONS_INVALID')
    if len(set(case_ids)) != len(case_ids) or not set(case_ids) <= {c['id'] for c in data['cases']}:
        raise ValueError('EVALUATION_CASE_SELECTION_INVALID')
    selected = deepcopy(data)
    selected['cases'] = [c for c in data['cases']
                         if (split == 'all' or c['split'] == split)
                         and (not case_ids or c['id'] in case_ids)
                         and (c['review_status'] == 'reviewed'
                              or include_drafts and c['review_status'] == 'draft')]
    if case_ids and set(case_ids) != {c['id'] for c in selected['cases']}:
        raise ValueError('EVALUATION_CASE_NOT_ELIGIBLE')
    return selected


def execution_plan(data, *, repeats, max_calls):
    if type(repeats) is not int or repeats < 1 or type(max_calls) is not int or max_calls < 1:
        raise ValueError('EVALUATION_OPTIONS_INVALID')
    total = len(data['cases']) * repeats
    if total > max_calls:
        raise ValueError('EVALUATION_CALL_LIMIT')
    return {'mode': 'plan', 'external_calls': 0, 'selected_cases': len(data['cases']),
            'planned_calls': total, 'repeats': repeats, 'max_calls': max_calls,
            'draft_cases': sum(c['review_status'] == 'draft' for c in data['cases'])}


def run_provider(data, digest, values, *, repeats, max_calls, execute=False):
    # 설정·제공자를 읽기 전에 명시적 실행 여부와 실행 수 상한을 확인한다.
    plan = execution_plan(data, repeats=repeats, max_calls=max_calls)
    if execute is not True:
        return plan
    if not plan['selected_cases']:
        raise ValueError('EVALUATION_EMPTY_SELECTION')
    from solo_leveling.infrastructure.generation.openai_generator import OpenAIGenerationSettings
    from solo_leveling.infrastructure.generation.semantic_verifier import OpenAISemanticVerifier, SemanticSettings
    try:
        generation = OpenAIGenerationSettings.from_env(values)
        settings = SemanticSettings.from_env(values)
        if not settings.enabled or not generation.allow_external_api or generation.local_only:
            raise ValueError('disabled')
        if not settings.model:
            raise ValueError('model required')
    except (ValueError, TypeError, AttributeError):
        raise ValueError('EVALUATION_PROVIDER_CONFIG_INVALID') from None
    verifier = OpenAISemanticVerifier(generation, settings)
    result = evaluate(data, digest, verifier, split='all', repeats=repeats, max_calls=max_calls,
                      max_claims=settings.max_claims, max_input_chars=settings.max_input_chars)
    result.update(mode='provider', quality_measurement=True,
                  transport_max_attempts=1, timeout_seconds=settings.timeout_seconds,
                  max_output_tokens=settings.max_output_tokens,
                  notice='초안 포함 결과는 잠정 집계입니다. 실제 호출 성공은 모델 품질 통과를 뜻하지 않습니다.')
    return result
