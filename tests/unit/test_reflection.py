"""읽기 전·후 질문 템플릿과 답변 검증 규칙(M1-07, #52)."""
import pytest

from solo_leveling.domain.learning_progress import LEARNING_STEPS
from solo_leveling.domain.reflection import (
    AFTER_READING_TEMPLATES, BEFORE_READING_TEMPLATES, COMPARISON_GROUPS, MAX_ANSWER_LENGTH, PHASE_AFTER, PHASE_BEFORE,
    QuestionTemplate, ReflectionValidationError, TEMPLATES, get_template, templates_for, validate_answer,
)


def test_before_reading_templates_cover_background_hypothesis_expectation():
    assert [t.category for t in BEFORE_READING_TEMPLATES] == ['background', 'hypothesis', 'expectation']
    assert all(t.phase == PHASE_BEFORE and t.prompt.strip() for t in BEFORE_READING_TEMPLATES)


def test_template_ids_are_unique_and_steps_are_valid():
    ids = [t.template_id for t in BEFORE_READING_TEMPLATES]
    assert len(set(ids)) == len(ids)
    assert all(t.step in LEARNING_STEPS for t in BEFORE_READING_TEMPLATES)


def test_templates_are_provided_per_step():
    assert templates_for(PHASE_BEFORE, 'preview') == BEFORE_READING_TEMPLATES
    assert templates_for(PHASE_BEFORE, 'deep_read') == ()
    assert templates_for(PHASE_BEFORE) == BEFORE_READING_TEMPLATES


def test_after_reading_templates_cover_claim_evidence_limitation_application():
    assert [t.category for t in AFTER_READING_TEMPLATES] == ['claim', 'evidence', 'limitation', 'application']
    assert all(t.phase == PHASE_AFTER and t.step == 'review' and t.prompt.strip() for t in AFTER_READING_TEMPLATES)
    assert templates_for(PHASE_AFTER) == AFTER_READING_TEMPLATES
    assert templates_for(PHASE_AFTER, 'review') == AFTER_READING_TEMPLATES
    assert templates_for(PHASE_AFTER, 'preview') == ()


def test_template_ids_are_unique_across_phases():
    ids = [t.template_id for templates in TEMPLATES.values() for t in templates]
    assert len(set(ids)) == len(ids)


def test_comparison_groups_link_fixed_ids_one_before_to_one_or_more_after():
    assert [(g.group_id, g.before_id, g.after_ids) for g in COMPARISON_GROUPS] == [
        ('hypothesis_vs_paper', 'before.hypothesis', ('after.claim', 'after.evidence')),
        ('background_vs_limitation', 'before.background', ('after.limitation',)),
        ('expectation_vs_application', 'before.expectation', ('after.application',))]
    # 읽기 전 질문 3개와 읽기 후 질문 4개가 묶음에서 각각 정확히 한 번씩 나온다.
    assert sorted(g.before_id for g in COMPARISON_GROUPS) == sorted(t.template_id for t in BEFORE_READING_TEMPLATES)
    assert sorted(i for g in COMPARISON_GROUPS for i in g.after_ids) == sorted(
        t.template_id for t in AFTER_READING_TEMPLATES)


def test_comparison_groups_follow_ids_not_question_order():
    # 화면 순서와 별개로 ID로 연결되므로, 질문 순서를 바꿔도 묶음은 그대로다.
    assert [t.template_id for t in AFTER_READING_TEMPLATES] == [
        'after.claim', 'after.evidence', 'after.limitation', 'after.application']
    assert {i: g.group_id for g in COMPARISON_GROUPS for i in g.after_ids}['after.limitation'] == 'background_vs_limitation'


def test_template_shape_is_unchanged_so_the_mcp_output_stays_the_same():
    from dataclasses import fields
    assert [f.name for f in fields(QuestionTemplate)] == ['template_id', 'phase', 'category', 'prompt', 'step']


def test_after_answers_validate_with_after_ids_only():
    assert validate_answer(PHASE_AFTER, 'after.claim', '주장', 'draft').template_id == 'after.claim'
    with pytest.raises(ReflectionValidationError):
        validate_answer(PHASE_AFTER, 'before.background', '답', 'draft')
    with pytest.raises(ReflectionValidationError):
        validate_answer(PHASE_BEFORE, 'after.claim', '답', 'draft')
    with pytest.raises(ReflectionValidationError):
        validate_answer(PHASE_AFTER, 'after.claim', '  ', 'completed')


@pytest.mark.parametrize('phase', ['during', '', None])
def test_unknown_phase_is_rejected(phase):
    with pytest.raises(ReflectionValidationError):
        templates_for(phase)


def test_unknown_step_is_rejected():
    with pytest.raises(ReflectionValidationError):
        templates_for(PHASE_BEFORE, 'summary')


def test_template_rejects_bad_fields():
    with pytest.raises(ReflectionValidationError):
        QuestionTemplate('x', 'during', 'c', '질문', 'preview')
    with pytest.raises(ReflectionValidationError):
        QuestionTemplate('x', PHASE_BEFORE, 'c', '질문', 'nope')
    with pytest.raises(ReflectionValidationError):
        QuestionTemplate(' ', PHASE_BEFORE, 'c', '질문', 'preview')
    with pytest.raises(ReflectionValidationError):
        QuestionTemplate('x', PHASE_BEFORE, 'c', '  ', 'preview')


def test_get_template_rejects_unknown_id_and_other_phase_ids():
    assert get_template(PHASE_BEFORE, 'before.hypothesis').category == 'hypothesis'
    with pytest.raises(ReflectionValidationError):
        get_template(PHASE_BEFORE, 'nope')
    with pytest.raises(ReflectionValidationError):
        get_template(PHASE_AFTER, 'before.hypothesis')


def test_validate_answer_accepts_draft_and_completed():
    assert validate_answer(PHASE_BEFORE, 'before.background', '안다', 'completed').template_id == 'before.background'
    validate_answer(PHASE_BEFORE, 'before.background', '', 'draft')  # 작성 중에는 비워도 된다


def test_completed_requires_text():
    for blank in ('', '   ', '\n'):
        with pytest.raises(ReflectionValidationError):
            validate_answer(PHASE_BEFORE, 'before.background', blank, 'completed')


def test_length_limit_and_types():
    validate_answer(PHASE_BEFORE, 'before.background', 'a' * MAX_ANSWER_LENGTH, 'draft')
    with pytest.raises(ReflectionValidationError):
        validate_answer(PHASE_BEFORE, 'before.background', 'a' * (MAX_ANSWER_LENGTH + 1), 'draft')
    with pytest.raises(ReflectionValidationError):
        validate_answer(PHASE_BEFORE, 'before.background', 123, 'draft')
    with pytest.raises(ReflectionValidationError):
        validate_answer(PHASE_BEFORE, 'before.background', '답', 'submitted')
