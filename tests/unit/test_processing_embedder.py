"""모델·네트워크를 차단한 #517 고정 PyTorch adapter 계약 검증."""

import builtins
from dataclasses import FrozenInstanceError, replace
import json
import socket
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from evaluation.embedding.pytorch_capture import artifact_digest
from solo_leveling.domain.remote_processing import EmbeddingSpec, ProcessedTranslation, ProcessingError
from solo_leveling.infrastructure.embeddings import processing_embedder as module
from tests.unit.test_remote_processing_stages import setup as setup


@pytest.fixture(name='processing_setup')
def shared_processing_setup(request):
    return request.getfixturevalue('setup')


@pytest.fixture(autouse=True)
def forbid_network_and_models(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('실제 네트워크·모델 실행 금지')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.split('.')[0] in ('torch', 'sentence_transformers', 'transformers', 'onnxruntime'):
            forbidden()
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)


class FakeArray:
    dtype = SimpleNamespace(name='float32')

    def __init__(self, rows):
        self.rows = rows

    def tolist(self):
        return self.rows


class FakeModel:
    def __init__(self):
        self.device = 'cpu'
        self.backend = 'torch'
        self.max_seq_length = 512
        self.default_prompt_name = None
        self.parameter = SimpleNamespace(dtype='torch.float32', device='cpu')
        self.pooling = dict(word_embedding_dimension=2, pooling_mode_mean_tokens=True,
                            pooling_mode_cls_token=False, pooling_mode_max_tokens=False)
        self.encoder = Mock(side_effect=lambda texts, **kwargs: FakeArray([[1.0, 0.0] for _ in texts]))

    def __len__(self): return 2
    def get_backend(self): return self.backend
    def get_embedding_dimension(self): return 2
    def parameters(self): return iter([self.parameter])
    def __getitem__(self, index): return SimpleNamespace(get_config_dict=lambda: self.pooling)
    def encode(self, *args, **kwargs): return self.encoder(*args, **kwargs)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    folder = tmp_path / ('a' * 40)
    (folder / '1_Pooling').mkdir(parents=True)
    for name in ('config.json', 'modules.json', 'tokenizer.json', '1_Pooling/config.json'):
        (folder/name).write_text('{}', encoding='utf-8')
    (folder/'modules.json').write_text(json.dumps([
        dict(idx=0, name='0', path='', type='sentence_transformers.models.Transformer'),
        dict(idx=1, name='1', path='1_Pooling', type='sentence_transformers.models.Pooling'),
    ]), encoding='utf-8')
    (folder/'model.safetensors').write_bytes(b'synthetic weight bytes, not a real model')
    monkeypatch.setattr(module, '_runtime_version', lambda: 'synthetic-runtime')
    monkeypatch.setattr(module, '_loaded_runtime_version', lambda: 'synthetic-runtime')
    spec = EmbeddingSpec('synthetic/model', folder.name, 'synthetic/model', folder.name,
        'mean', 'l2', 128, 2, 'cosine', 'float32', 'pytorch', 'synthetic-runtime',
        module.processing_artifact_sha256(folder))
    model = FakeModel()
    factory = Mock(return_value=model)
    adapter = module.PyTorchProcessingEmbedder(model_dir=folder, model_factory=factory,
                                              check_allowed=Mock(return_value=True))
    items = (ProcessedTranslation('c1', '첫 합성 번역', None), ProcessedTranslation('c2', '둘째 합성 번역', None))
    return adapter, spec, items, model, factory


def test_matches_evaluation_artifact_digest_without_loading_model(fixture):
    adapter, spec, *_ = fixture
    assert module.processing_artifact_sha256(adapter.model_dir) == artifact_digest(adapter.model_dir) == spec.artifact_sha256


def test_uses_pinned_local_factory_and_explicit_encoding_without_default_cache(fixture):
    adapter, spec, items, model, factory = fixture
    result = adapter.embed(items, spec)
    factory.assert_called_once_with(adapter.model_dir.resolve())
    assert model.max_seq_length == 128
    model.encoder.assert_called_once_with([item.text for item in items], batch_size=8,
        normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False, prompt='')
    assert result.spec == spec and result.chunk_ids == ('c1', 'c2')
    assert result.vectors == ((1.0, 0.0), (1.0, 0.0))
    with pytest.raises(FrozenInstanceError): adapter.batch_size = 1


@pytest.mark.parametrize('change', [dict(runtime='onnx'), dict(precision='float16'), dict(normalization='none'),
    dict(model_revision='b'*40), dict(tokenizer_revision='b'*40), dict(tokenizer_id='other'),
    dict(max_length=0), dict(dimension=True), dict(distance='dot')])
def test_invalid_fixed_contract_is_rejected_before_factory(fixture, change):
    adapter, spec, items, _, factory = fixture
    with pytest.raises(ProcessingError, match='^PROCESSING_EMBEDDING_CONFIG_INVALID$'):
        adapter.embed(items, replace(spec, **change))
    factory.assert_not_called()


def test_loader_file_mutation_must_be_detected_before_encode(fixture):
    adapter, spec, items, model, factory = fixture
    def load(folder):
        (folder/'model.safetensors').write_bytes(b'changed during synthetic model loading')
        return model
    factory.side_effect = load
    with pytest.raises(ProcessingError): adapter.embed(items, spec)
    model.encoder.assert_not_called()


@pytest.mark.parametrize('change', ['hash', 'missing-tokenizer', 'missing-weight', 'runtime-version'])
def test_artifact_and_version_mismatch_fail_before_factory(fixture, change):
    adapter, spec, items, _, factory = fixture
    if change == 'hash': spec = replace(spec, artifact_sha256='0'*64)
    elif change == 'missing-tokenizer': (adapter.model_dir/'tokenizer.json').unlink()
    elif change == 'missing-weight': (adapter.model_dir/'model.safetensors').unlink()
    else: spec = replace(spec, runtime_version='wrong-version')
    with pytest.raises(ProcessingError): adapter.embed(items, spec)
    factory.assert_not_called()


@pytest.mark.parametrize('mode', ['backend', 'gpu', 'dtype', 'parameter-gpu', 'pooling', 'pooling-dimension', 'prompt'])
def test_actual_model_observation_is_checked_before_encode(fixture, mode):
    adapter, spec, items, model, _ = fixture
    if mode == 'backend': model.backend = 'onnx'
    elif mode == 'gpu': model.device = 'cuda:0'
    elif mode == 'dtype': model.parameter.dtype = 'torch.float16'
    elif mode == 'parameter-gpu': model.parameter.device = 'cuda:0'
    elif mode == 'pooling': model.pooling['pooling_mode_max_tokens'] = True
    elif mode == 'pooling-dimension': model.pooling['word_embedding_dimension'] = 3
    else: model.default_prompt_name = 'hidden-prompt'
    with pytest.raises(ProcessingError, match='^PROCESSING_EMBEDDING_RUNTIME_MISMATCH$'):
        adapter.embed(items, spec)
    model.encoder.assert_not_called()


def test_canonical_pooling_config_is_supported(fixture):
    adapter, spec, items, model, _ = fixture
    spec = replace(spec, pooling=json.dumps(model.pooling, sort_keys=True))
    assert adapter.embed(items, spec).spec == spec


@pytest.mark.parametrize('mode', ['empty', 'missing', 'duplicate', 'failed'])
def test_invalid_translation_set_never_loads_model(fixture, mode):
    adapter, spec, items, _, factory = fixture
    if mode == 'empty': items = ()
    elif mode == 'missing': items = (replace(items[0], text=None),)
    elif mode == 'duplicate': items = (items[0], items[0])
    else: items = (replace(items[0], failure_code='provider_unavailable'),)
    with pytest.raises(ProcessingError): adapter.embed(items, spec)
    factory.assert_not_called()


@pytest.mark.parametrize('row', [[0.0, 0.0], [float('nan'), 0.0], [float('inf'), 0.0], [True, 0.0],
                                  [2.0, 0.0], [1.0], [1.0, 0.0, 0.0]])
def test_vector_contract_is_not_satisfied_by_declared_fingerprint_alone(fixture, row):
    adapter, spec, items, model, _ = fixture
    model.encoder.side_effect = lambda *args, **kwargs: FakeArray([row, row])
    with pytest.raises(ProcessingError, match='^PROCESSING_EMBEDDING_INVALID$'):
        adapter.embed(items, spec)


@pytest.mark.parametrize('mode', ['artifact', 'runtime', 'length', 'backend'])
def test_mutation_during_encoding_is_rejected(fixture, monkeypatch, mode):
    adapter, spec, items, model, _ = fixture
    def encode(*args, **kwargs):
        if mode == 'artifact': (adapter.model_dir/'model.safetensors').write_bytes(b'changed synthetic weights')
        elif mode == 'runtime': monkeypatch.setattr(module, '_runtime_version', lambda: 'changed-runtime')
        elif mode == 'length': model.max_seq_length = 256
        else: model.backend = 'onnx'
        return FakeArray([[1.0, 0.0], [1.0, 0.0]])
    model.encoder.side_effect = encode
    with pytest.raises(ProcessingError): adapter.embed(items, spec)


@pytest.mark.parametrize('error_type', [RuntimeError, ProcessingError])
def test_private_loader_failure_is_sanitized_without_logging(fixture, capsys, error_type):
    adapter, spec, items, _, factory = fixture
    factory.side_effect = error_type('private source text; service key')
    with pytest.raises(ProcessingError, match='^PROCESSING_EMBEDDING_FAILED$') as error:
        adapter.embed(items, spec)
    assert error.value.__suppress_context__
    assert capsys.readouterr() == ('', '')


def test_default_factory_requires_local_cpu_and_no_remote_code(monkeypatch, tmp_path):
    factory = Mock(return_value=object())
    guarded = builtins.__import__
    def synthetic_import(name, *args, **kwargs):
        if name == 'sentence_transformers':
            return SimpleNamespace(SentenceTransformer=factory)
        return guarded(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', synthetic_import)
    module._load_model(tmp_path)
    factory.assert_called_once_with(str(tmp_path), device='cpu', backend='torch',
                                    local_files_only=True, trust_remote_code=False)


def test_adapter_runs_inside_existing_stages_without_changing_source(fixture, processing_setup):
    adapter, spec, _, model, _ = fixture
    stages, source, pdf, ledger, post, *_ = processing_setup
    stages.embedder, stages.spec = adapter, spec
    result = stages.run(source, pdf)
    assert result.status == 'prepared' and result.source == source
    assert result.embeddings.spec == spec
    assert result.embeddings.chunk_ids == tuple(c.chunk_id for c in result.document.chunks)
    assert model.encoder.call_args.args[0] == [item.text for item in result.translations]
    assert post.call_count == len(ledger.observed) == 2


@pytest.mark.parametrize('mode', ['usage-unknown', 'denied', 'partial', 'checkpoint-stop'])
def test_stages_do_not_load_model_after_uncertainty_denial_or_partial(fixture, processing_setup, mode):
    from solo_leveling.application.provider_dispatch import DispatchError
    from tests.unit.test_remote_processing_stages import envelope
    adapter, spec, _, _, factory = fixture
    stages, source, pdf, ledger, post, response, _ = processing_setup
    stages.embedder, stages.spec = adapter, spec
    if mode == 'usage-unknown': post.side_effect = requests.Timeout('synthetic private timeout')
    elif mode == 'denied': ledger.allowed = False
    elif mode == 'partial': response.json.return_value = envelope('')
    else: stages.check_allowed.side_effect = lambda source, stage: stage != 'embedding'
    if mode == 'partial':
        assert stages.run(source, pdf).status == 'translation_incomplete'
    else:
        with pytest.raises((ProcessingError, DispatchError)): stages.run(source, pdf)
        if mode == 'usage-unknown':
            with pytest.raises(DispatchError): stages.run(source, pdf)
            assert len(ledger.requests) == len(ledger.unknowns) == post.call_count == 1
    factory.assert_not_called()


@pytest.mark.parametrize('mode', ['revoke', 'usage-pending', 'lookup-error'])
def test_authority_change_during_file_hash_blocks_model_loading(fixture, monkeypatch, mode):
    adapter, spec, items, _, factory = fixture
    state = {'allowed': True, 'error': False}
    def check():
        if state['error']: raise RuntimeError('private ledger details')
        return state['allowed']
    adapter = replace(adapter, check_allowed=check)
    original = module.processing_artifact_sha256
    def hash_then_change(folder):
        digest = original(folder)
        state['allowed'] = False
        state['error'] = mode == 'lookup-error'
        return digest
    monkeypatch.setattr(module, 'processing_artifact_sha256', hash_then_change)
    code = 'PROCESSING_CHECK_UNAVAILABLE' if mode == 'lookup-error' else 'PROCESSING_NOT_ALLOWED'
    with pytest.raises(ProcessingError, match=f'^{code}$'): adapter.embed(items, spec)
    factory.assert_not_called()


@pytest.mark.parametrize('point', ['loader', 'pre-encode-hash', 'encode', 'final-hash'])
def test_checkpoint_stops_encoding_or_discards_result_at_each_boundary(fixture, monkeypatch, point):
    adapter, spec, items, model, factory = fixture
    state = {'allowed': True}
    adapter = replace(adapter, check_allowed=lambda: state['allowed'])
    if point == 'loader':
        def load(folder):
            state['allowed'] = False
            return model
        factory.side_effect = load
    elif point == 'encode':
        def encode(*args, **kwargs):
            state['allowed'] = False
            return FakeArray([[1.0, 0.0], [1.0, 0.0]])
        model.encoder.side_effect = encode
    else:
        original = module.processing_artifact_sha256
        calls = []
        def hash_then_change(folder):
            digest = original(folder)
            calls.append(folder)
            if len(calls) == (2 if point == 'pre-encode-hash' else 3): state['allowed'] = False
            return digest
        monkeypatch.setattr(module, 'processing_artifact_sha256', hash_then_change)
    with pytest.raises(ProcessingError, match='^PROCESSING_NOT_ALLOWED$'): adapter.embed(items, spec)
    assert factory.call_count == 1
    assert model.encoder.call_count == int(point in ('encode', 'final-hash'))


@pytest.mark.parametrize('value', [False, None, 1, 'true'])
def test_missing_or_nonboolean_authorization_cannot_load_model(fixture, value):
    adapter, spec, items, _, factory = fixture
    adapter = replace(adapter, check_allowed=lambda: value)
    with pytest.raises(ProcessingError, match='^PROCESSING_NOT_ALLOWED$'): adapter.embed(items, spec)
    factory.assert_not_called()


def test_authority_callback_is_required_and_immutable(fixture):
    adapter, *_ = fixture
    with pytest.raises(TypeError): module.PyTorchProcessingEmbedder(model_dir=adapter.model_dir)
    with pytest.raises(ProcessingError): replace(adapter, check_allowed=None)
    with pytest.raises(FrozenInstanceError): adapter.check_allowed = lambda: True


@pytest.mark.parametrize('mode', ['external-path', 'custom-type', 'extra-module', 'extra-key', 'duplicate-key', 'boolean-index'])
def test_manifest_cannot_enable_unhashed_module_code_or_external_paths(fixture, mode):
    adapter, spec, items, _, factory = fixture
    path = adapter.model_dir/'modules.json'
    modules = json.loads(path.read_text(encoding='utf-8'))
    if mode == 'external-path': modules[0]['path'] = '../outside-model'
    elif mode == 'custom-type': modules[0]['type'] = 'private_package.ArbitraryModule'
    elif mode == 'extra-module': modules.append(dict(idx=2, name='2', path='2_Dense', type='custom.Dense'))
    elif mode == 'extra-key': modules[0]['kwargs'] = {'model_name_or_path': 'remote-or-external'}
    elif mode == 'boolean-index': modules[0]['idx'] = False
    if mode == 'duplicate-key':
        path.write_text('[{"idx":0,"idx":1}]', encoding='utf-8')
    else:
        path.write_text(json.dumps(modules), encoding='utf-8')
    # Hash equality alone must not imply that the module loading layout is supported.
    spec = replace(spec, artifact_sha256=module.processing_artifact_sha256(adapter.model_dir))
    with pytest.raises(ProcessingError, match='^PROCESSING_EMBEDDING_ARTIFACT_MISMATCH$'):
        adapter.embed(items, spec)
    factory.assert_not_called()


def test_safetensors_directory_does_not_count_as_a_weight_file(fixture):
    adapter, spec, items, _, factory = fixture
    weight = adapter.model_dir/'model.safetensors'
    weight.unlink()
    weight.mkdir()
    with pytest.raises(ProcessingError, match='^PROCESSING_EMBEDDING_ARTIFACT_MISMATCH$'):
        adapter.embed(items, spec)
    factory.assert_not_called()


@pytest.mark.parametrize('mode', ['installed-version', 'loaded-version'])
def test_runtime_change_inside_loader_is_detected_before_encode(fixture, monkeypatch, mode):
    adapter, spec, items, model, factory = fixture
    def load(folder):
        attr = '_runtime_version' if mode == 'installed-version' else '_loaded_runtime_version'
        monkeypatch.setattr(module, attr, lambda: 'changed-runtime')
        return model
    factory.side_effect = load
    with pytest.raises(ProcessingError, match='^PROCESSING_EMBEDDING_RUNTIME_MISMATCH$'):
        adapter.embed(items, spec)
    model.encoder.assert_not_called()


@pytest.mark.parametrize('missing', [False, True])
def test_loaded_version_observation_reads_modules_without_importing_models(monkeypatch, missing):
    for name, value in [('torch', 'test-torch'), ('sentence_transformers', 'test-st'), ('transformers', 'test-tf')]:
        monkeypatch.setitem(sys.modules, name, SimpleNamespace(__version__=value))
    if missing:
        monkeypatch.delitem(sys.modules, 'transformers')
        with pytest.raises(ProcessingError, match='^PROCESSING_EMBEDDING_RUNTIME_MISMATCH$'):
            module._loaded_runtime_version()
    else:
        assert module._loaded_runtime_version() == 'torch=test-torch;sentence-transformers=test-st;transformers=test-tf'


def test_boolean_dimension_observation_is_not_an_integer(fixture):
    adapter, spec, items, model, _ = fixture
    model.get_embedding_dimension = lambda: True
    spec = replace(spec, dimension=1)
    with pytest.raises(ProcessingError, match='^PROCESSING_EMBEDDING_RUNTIME_MISMATCH$'):
        adapter.embed(items, spec)
    model.encoder.assert_not_called()


@pytest.mark.parametrize('suffix', ['.txt', '.model', '.bin', '.safetensors', '.json'])
def test_all_evaluation_artifact_file_types_affect_the_loading_gate(fixture, suffix):
    adapter, spec, items, _, factory = fixture
    (adapter.model_dir/f'new-artifact{suffix}').write_bytes(b'changed synthetic input')
    assert module.processing_artifact_sha256(adapter.model_dir) == artifact_digest(adapter.model_dir)
    assert module.processing_artifact_sha256(adapter.model_dir) != spec.artifact_sha256
    with pytest.raises(ProcessingError, match='^PROCESSING_EMBEDDING_ARTIFACT_MISMATCH$'):
        adapter.embed(items, spec)
    factory.assert_not_called()


@pytest.mark.parametrize('kind', ['symlink', 'junction'])
def test_directory_link_observation_cannot_hide_loader_inputs_from_manifest(fixture, monkeypatch, kind):
    adapter, spec, items, _, factory = fixture
    path_type = type(adapter.model_dir)
    attr = 'is_symlink' if kind == 'symlink' else 'is_junction'
    original = getattr(path_type, attr, lambda path: False)
    def observed_link(path):
        return path == adapter.model_dir/'1_Pooling' or original(path)
    monkeypatch.setattr(path_type, attr, observed_link, raising=False)
    with pytest.raises(ProcessingError, match='^PROCESSING_EMBEDDING_ARTIFACT_MISMATCH$'):
        adapter.embed(items, spec)
    factory.assert_not_called()


def test_windows_reparse_directory_is_rejected_without_path_is_junction_api(fixture, monkeypatch):
    adapter, spec, items, _, factory = fixture
    # Python 3.11에는 Path.is_junction이 없다. 실제 OS 링크를 만들지 않고 lstat 관측을 대체한다.
    monkeypatch.delattr(module.Path, 'is_junction', raising=False)
    path_type = type(adapter.model_dir)
    original = path_type.lstat
    class ReparseStat:
        def __init__(self, result): self.result = result
        @property
        def st_file_attributes(self): return 0x10 | 0x400
        def __getattr__(self, name): return getattr(self.result, name)
    def observed(path):
        result = original(path)
        return ReparseStat(result) if path == adapter.model_dir/'1_Pooling' else result
    monkeypatch.setattr(path_type, 'lstat', observed)
    with pytest.raises(ProcessingError, match='^PROCESSING_EMBEDDING_ARTIFACT_MISMATCH$'):
        adapter.embed(items, spec)
    factory.assert_not_called()


def test_plain_snapshot_keeps_same_hash_without_path_is_junction_api(fixture, monkeypatch):
    adapter, spec, items, _, factory = fixture
    monkeypatch.delattr(module.Path, 'is_junction', raising=False)
    assert module.processing_artifact_sha256(adapter.model_dir) == spec.artifact_sha256
    assert adapter.embed(items, spec).spec == spec
    factory.assert_called_once()


@pytest.mark.parametrize('canonical', [False, True])
def test_compact_mean_pooling_runtime_config_supported(fixture, canonical):
    adapter, spec, items, model, _ = fixture
    model.pooling = dict(embedding_dimension=2, pooling_mode='mean', include_prompt=True)
    if canonical: spec = replace(spec, pooling=json.dumps(model.pooling, sort_keys=True))
    assert adapter.embed(items, spec).spec == spec


@pytest.mark.parametrize('change', [
    dict(pooling_mode='max'), dict(pooling_mode=['mean', 'max']), dict(pooling_mode=['mean']),
    dict(embedding_dimension=True), dict(embedding_dimension=3), dict(include_prompt='true'),
    dict(pooling_mode_mean_tokens=True), dict(pooling_mode_max_tokens=False), dict(word_embedding_dimension=2),
])
def test_compact_runtime_rejects_non_mean_or_mixed_config_before_encode(fixture, change):
    adapter, spec, items, model, _ = fixture
    model.pooling = dict(embedding_dimension=2, pooling_mode='mean', include_prompt=True)
    model.pooling.update(change)
    with pytest.raises(ProcessingError, match='^PROCESSING_EMBEDDING_RUNTIME_MISMATCH$'):
        adapter.embed(items, spec)
    model.encoder.assert_not_called()
