"""재시작 후에도 같은 질문·검색 조건·처리 설정을 복원하는 불변 입력."""
from dataclasses import asdict, dataclass
import json

from .remote_contract import RemoteSearchBinding, RemoteSearchFilters, RemoteRetrievalContractError
from .remote_input import question_input_hash, _unique, _reject_constant


@dataclass(frozen=True, repr=False)
class RemoteQuestionInput:
    encoded: str

    @classmethod
    def create(cls, binding, question, filters, top_k, processing_config):
        question_input_hash(binding, question, filters, top_k, processing_config)
        value = dict(schema_version=1, binding=asdict(binding), question=question,
                     filters=asdict(filters), top_k=top_k, processing_config=processing_config)
        return cls(json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(',', ':')))

    def __post_init__(self):
        self.unpack()

    def unpack(self):
        try:
            if not isinstance(self.encoded, str) or len(self.encoded.encode('utf-8')) > 262144:
                raise ValueError()
            value = json.loads(self.encoded, object_pairs_hook=_unique, parse_constant=_reject_constant)
            if (set(value) != {'schema_version','binding','question','filters','top_k','processing_config'}
                    or type(value['schema_version']) is not int or value['schema_version'] != 1):
                raise ValueError()
            binding = RemoteSearchBinding(**value['binding'])
            focus = value['filters']
            if (set(focus) != {'pdf_pages','section_ids','strict_pages'}
                    or not isinstance(focus['pdf_pages'],list) or not isinstance(focus['section_ids'],list)):
                raise ValueError()
            filters = RemoteSearchFilters(tuple(focus['pdf_pages']),tuple(focus['section_ids']),focus['strict_pages'])
            args = binding,value['question'],filters,value['top_k'],value['processing_config']
            question_input_hash(*args)
            return args
        except (TypeError, ValueError, KeyError):
            raise RemoteRetrievalContractError('REMOTE_INPUT_INVALID') from None

    @property
    def input_hash(self):
        return question_input_hash(*self.unpack())
