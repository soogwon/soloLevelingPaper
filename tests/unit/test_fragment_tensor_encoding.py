"""실제 모델·torch 없이 토큰 ID 전달과 배치 경계를 관찰한다."""
from contextlib import nullcontext
from types import SimpleNamespace as NS
import sys
import json

import pytest

from solo_leveling.infrastructure.embeddings.processing_embedder import _encode_token_windows, _prepare_window
from solo_leveling.infrastructure.embeddings.token_windows import split_token_windows
from solo_leveling.domain.remote_processing import ProcessingError
from tests.unit.test_embedding_token_windows import Tokenizer


class Rows:
    def __init__(self, rows): self.rows = rows
    def detach(self): return self
    def cpu(self): return self
    def numpy(self): return self.rows


def test_new_backend_template_preserves_ids_without_legacy_prepare():
    template = dict(type='TemplateProcessing',single=[{'SpecialToken':dict(id='<s>',type_id=0)},
        {'Sequence':dict(id='A',type_id=0)}, {'SpecialToken':dict(id='</s>',type_id=0)}],
        special_tokens={'<s>':dict(ids=[0]),'</s>':dict(ids=[2])})
    tokenizer = NS(backend_tokenizer=NS(post_processor=NS(__getstate__=lambda:json.dumps(template).encode())),
                   num_special_tokens_to_add=lambda **kwargs:2,model_input_names=['input_ids','attention_mask'])
    assert _prepare_window(tokenizer,(5,9,11)) == dict(input_ids=[0,5,9,11,2],attention_mask=[1]*5)
    template['single'].insert(2,{'Sequence':dict(id='A',type_id=0)})
    with pytest.raises(ProcessingError): _prepare_window(tokenizer,(5,9,11))


def test_tensor_encoding_preserves_ids_without_text_roundtrip(monkeypatch):
    seen, checks = [], []
    class PreparedTokenizer(Tokenizer):
        def prepare_for_model(self, ids, **kwargs):
            assert kwargs == dict(add_special_tokens=True,truncation=False,return_attention_mask=True)
            seen.append(tuple(ids))
            return dict(input_ids=[1000,*ids,1001],attention_mask=[1]*(len(ids)+2))
        def pad(self, inputs, **kwargs):
            assert kwargs == dict(padding=True,return_tensors='pt')
            return inputs
    class Model:
        tokenizer = PreparedTokenizer()
        def eval(self): pass
        def __call__(self, features):
            return {'sentence_embedding':Rows([[1.,0.] for _ in features])}
    torch = NS(inference_mode=nullcontext,nn=NS(functional=NS(normalize=lambda v, **kw:v)),
               cat=lambda rows,**kwargs:Rows([row for batch in rows for row in batch.rows]))
    monkeypatch.setitem(sys.modules,'torch',torch)
    windows = split_token_windows(Tokenizer(),'248',parent_chunk_id='c',embedding_set_id='s',max_length=128)
    result = _encode_token_windows(Model(),windows,max_length=128,batch_size=2,
                                  check_allowed=lambda: checks.append(True) or True)
    assert result == [[1.,0.]]*3
    assert seen == [w.token_ids for w in windows]
    assert len(checks) == 4
    with pytest.raises(ProcessingError, match='INPUT_TOO_LONG'):
        _encode_token_windows(Model(),windows,max_length=100,batch_size=2,check_allowed=lambda:True)
