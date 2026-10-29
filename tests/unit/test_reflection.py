"""읽기 전·후 질문 템플릿과 답변 검증 규칙(M1-07, #52)."""
import pytest

from solo_leveling.domain.learning_progress import LEARNING_STEPS
from solo_leveling.domain.reflection import (
    BEFORE_READING_TEMPLATES, MAX_ANSWER_LENGTH, PHASE_AFTER, PHASE_BEFORE, QuestionTemplate,
    ReflectionValidationError, get_template, templates_for, validate_answer,
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


def test_after_phase_exists_but_has_no_templates_until_m1_08():
    assert templates_for(PHASE_AFTER) == ()


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
