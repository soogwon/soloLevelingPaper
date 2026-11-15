"""모델·ONNX 설치 없이 실제 파일 지문과 session 관측 거부를 확인한다."""

from types import SimpleNamespace as NS

import pytest

from evaluation.embedding import artifacts
from evaluation.embedding.comparison import EvaluationError


def graph(location='weights.data'):
    tensor = NS(external_data=[NS(key='location', value=location)], data_type=1, ListFields=lambda: [])
    return NS(producer_name='exporter', producer_version='2', opset_import=[NS(domain='', version=17)],
              ListFields=lambda: [(NS(message_type=object(), is_repeated=True), [tensor])])


@pytest.fixture
def folder(tmp_path, monkeypatch):
    (tmp_path / 'onnx').mkdir()
    (tmp_path / 'onnx/model.onnx').write_bytes(b'graph')
    (tmp_path / 'onnx/weights.data').write_bytes(b'weights')
    (tmp_path / 'tokenizer.json').write_text('{}')
    monkeypatch.setattr(artifacts, '_load_graph', lambda _: graph())
    return tmp_path


def test_graph_external_weights_input_files_and_export_metadata_are_bound(folder, monkeypatch):
    a = artifacts.onnx_manifest(folder, 'onnx/model.onnx')
    assert a['opsets'] == [('ai.onnx', 17)]
    assert a['producer_version'] == '2'
    assert len(a['files']) == 2
    (folder / 'onnx/weights.data').write_bytes(b'changed')
    b = artifacts.onnx_manifest(folder, 'onnx/model.onnx')
    assert a != b
    assert a['input_artifacts_sha256'] == b['input_artifacts_sha256']
    (folder / 'tokenizer.json').write_text('{"changed":true}')
    assert artifacts.onnx_manifest(folder, 'onnx/model.onnx')['input_artifacts_sha256'] != b['input_artifacts_sha256']
    monkeypatch.setattr(artifacts, '_load_graph', lambda _: NS(**{**graph().__dict__, 'producer_version': '3'}))
    assert artifacts.onnx_manifest(folder, 'onnx/model.onnx')['producer_version'] == '3'


@pytest.mark.parametrize('location', ['../outside', '/outside', 'missing.data', ''])
def test_external_data_escape_and_missing_files_fail_closed(folder, monkeypatch, location):
    monkeypatch.setattr(artifacts, '_load_graph', lambda _: graph(location))
    with pytest.raises(EvaluationError, match='ONNX_EXTERNAL_DATA_INVALID'):
        artifacts.onnx_manifest(folder, 'onnx/model.onnx')


def test_missing_export_metadata_is_not_assumed(folder, monkeypatch):
    g = graph()
    g.producer_version = ''
    monkeypatch.setattr(artifacts, '_load_graph', lambda _: g)
    with pytest.raises(EvaluationError, match='ONNX_EXPORT_METADATA_MISSING'):
        artifacts.onnx_manifest(folder, 'onnx/model.onnx')


@pytest.mark.parametrize('providers,intra,inter', [(['CUDAExecutionProvider'], 1, 1),
                                                (['CPUExecutionProvider'], 0, 1),
                                                (['CPUExecutionProvider'], 1, 2)])
def test_actual_ort_provider_and_threads_must_match(providers, intra, inter):
    session = NS(get_providers=lambda: providers, get_session_options=lambda: NS(
        intra_op_num_threads=intra, inter_op_num_threads=inter, execution_mode=NS(name='ORT_SEQUENTIAL')))
    with pytest.raises(EvaluationError, match='ORT_SESSION_MISMATCH'):
        artifacts.observe_ort_session(session, 1)


def test_same_capture_protocol_and_inputs_are_required_for_performance_comparison():
    from copy import deepcopy
    from pathlib import Path
    from evaluation.embedding.comparison import compare, load_json, synthetic_capture
    from tests.unit.test_embedding_evaluation import measured
    data = load_json(Path(__file__).resolve().parents[2] / 'evaluation/embedding/cases.json')
    a = measured(synthetic_capture(data), 'pytorch')
    b = deepcopy(a)
    for row in (a, b):
        row['measurements'].update(protocol_version=2, input_artifacts_sha256='same', procedure=artifacts.PROCEDURE)
    assert compare(data, a, b)['performance_comparable'] is True
    b['measurements']['input_artifacts_sha256'] = 'other'
    assert compare(data, a, b)['performance_comparable'] is False
    del b['measurements']['protocol_version']
    assert compare(data, a, b)['performance_comparable'] is False
