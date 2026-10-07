"""청크 원문의 잘린 앞뒤 판정을 실제 사례로 검증한다."""

import pytest

from solo_leveling.application.evidence_qa.fragments import fragment_flags
from solo_leveling.domain.evidence_qa import EvidenceInput


@pytest.mark.parametrize('text,expected', [
    # 겹침 구간이 단어 중간에서 시작해 "위치의 상대성"으로 오역된 청크
    ('ation of positions in the input\nand output sequences, the easier it is to learn '
     'long-range dependencies [12]. In terms of\ncomputational complexity, self-attention layers',
     (True, True)),
    # 앞 문장만 온전한 청크: 뒤가 잘렸다
    ('The third is the path length between long-range dependencies in the network. '
     'The shorter these paths between any combination of positions in the input\n'
     'and output sequences, the easier it is to learn', (False, True)),
    # 끝의 페이지 번호 줄을 제외하면 "sequence"에서 끊긴다
    ('uential operations. In terms of\ncomputational complexity, self-attention layers '
     'are faster than recurrent layers when the sequence\n6', (True, True)),
    # 페이지 번호 줄을 제외하면 온전한 문장으로 끝난다
    ('Self-attention has been used successfully in a variety of tasks.\n7', (False, False)),
    ('It is easier to learn long-range dependencies [12].', (False, False)),
    ('(see Table 3 row (E)).', (False, False)),
    ('", as memory constraints limit batching across examples.', (False, False)),
    (', as memory constraints limit batching across examples.', (True, False)),
    ('Self-attention has been used successfully in a variety of tasks including read',
     (False, True)),
    ('12', (False, True)),
])
def test_fragment_flags(text, expected):
    assert fragment_flags(text) == expected


def test_evidence_input_rejects_non_bool_flags():
    with pytest.raises(ValueError):
        EvidenceInput('e1', 'c1', '본문', 'Original.', None, 1, 1, False)
