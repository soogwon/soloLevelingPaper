"""정상 의미 검증의 코드 진단과 본문 유출 차단을 검증한다."""
from contextlib import redirect_stderr
from io import StringIO
import json
import pytest
from solo_leveling.diagnostics import traced, semantic_diagnostic, semantic_retention_diagnostic
from solo_leveling.interfaces.cli.safe_diagnostic_stream import SafeDiagnosticStream
from solo_leveling.domain.evidence_qa import Claim
from solo_leveling.domain.semantic_verification import (
    VerificationReport, ClaimJudgment, SemanticVerdict as V, SemanticReason as R,
    AnswerAudit, QuestionCoverage, RepairPreservation,
)


def test_completed_report_records_checks_and_final_exclusion_without_bodies():
    claims = tuple(Claim(f'private-id-{n}', 'PRIVATE_CLAIM', ('PRIVATE_EVIDENCE',)) for n in range(3))
    report = VerificationReport('b' * 32, 'private-model', 'semantic-v7', 10, (
        ClaimJudgment(claims[0].claim_id, V.PARTIALLY_SUPPORTED, R.MISSING_CONDITION,
                      (('original.conditions', 'unsupported'),)),
        ClaimJudgment(claims[1].claim_id, V.SUPPORTED, R.FULL_SUPPORT),
        ClaimJudgment(claims[2].claim_id, V.SUPPORTED, R.FULL_SUPPORT),
    ), answer_audit=AnswerAudit(
        (QuestionCoverage('PRIVATE_QUESTION', 'partial', (claims[2].claim_id,),
                          (('coverage.qualifiers', 'missing'),)),),
        (RepairPreservation('PRIVATE_SOURCE', 'changed', (('repairs.comparison_basis', 'changed'),)),)))
    output = StringIO()
    @traced('ask_paper', request=True)
    def run():
        semantic_diagnostic(report)
        semantic_retention_diagnostic(report, claims, {claims[2].claim_id})
    with redirect_stderr(SafeDiagnosticStream(output)):
        run()
    assert 'PRIVATE' not in output.getvalue() and 'private-' not in output.getvalue()
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assessments = [r for r in records if r.get('event') == 'assessment']
    assert any(r['check_item'] == 'original.conditions' and r['status'] == 'unsupported' for r in assessments)
    assert any(r['check_item'] == 'repairs.comparison_basis' and r['status'] == 'changed' for r in assessments)
    assert any(r['check_item'] == 'coverage.qualifiers' and r['status'] == 'missing' for r in assessments)
    final = [r for r in assessments if r['check_item'] == 'claim.final']
    assert [(r['item_number'], r['reason_code']) for r in final] == [
        (1, 'SEMANTIC_NOT_SUPPORTED'), (2, 'REPAIR_CHANGED'), (3, 'RETAINED')]


@pytest.mark.parametrize('change', [
    {'body': 'PRIVATE'}, {'claim_id': 'PRIVATE'}, {'check_item': 'PRIVATE'},
    {'status': 'PRIVATE'}, {'reason_code': 'PRIVATE'}, {'item_number': True},
    {'item_number': 0}, {'request_id': 'PRIVATE'}, {'elapsed_ms': True},
    {'error_code': 'PRIVATE'}, {'check_item': 'coverage.topic', 'status': 'changed'},
    {'check_item': 'claim.verdict', 'status': 'supported', 'reason_code': 'missing_condition'},
])
def test_unsafe_assessment_is_dropped(change):
    record = dict(request_id='a' * 32, span_id='b' * 32, stage='semantic_diagnostic',
        event='assessment', elapsed_ms=0, error_code=None, item_number=1,
        check_item='original.conditions', status='unsupported', reason_code=None)
    output = StringIO()
    SafeDiagnosticStream(output).write(json.dumps(record | change) + '\n')
    assert not output.getvalue()


def test_basic_and_detailed_logs_keep_same_decisions_without_duplicate_events():
    from solo_leveling.diagnostics import diagnostic_verbosity
    report = VerificationReport('b' * 32, 'm', 'semantic-v7', 1, (
        ClaimJudgment('private', V.SUPPORTED, R.FULL_SUPPORT,
            (('original.conditions', 'supported'), ('original.causality', 'not_applicable'))),))
    results = []
    for detailed in (False, True):
        output = StringIO()
        @traced('ask_paper', request=True)
        def run():
            semantic_diagnostic(report)
        with diagnostic_verbosity(detailed=detailed), redirect_stderr(SafeDiagnosticStream(output, detailed=detailed)):
            run()
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        assert not any(r.get('event') in ('judgment', 'completed') for r in records)
        assert 'private' not in output.getvalue()
        results.append([r['check_item'] for r in records if r.get('event') == 'assessment'])
    assert results == [['claim.verdict'], ['claim.verdict', 'original.conditions', 'original.causality']]
