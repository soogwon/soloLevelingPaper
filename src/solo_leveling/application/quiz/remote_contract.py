"""원격 퀴즈 생성의 고정 입력과 구조 검증. 실행·자동 생성·동의 기록은 하지 않는다."""
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json

from solo_leveling.application.evidence_qa.remote_contract import RemoteSearchBinding
from solo_leveling.application.summary.remote_contract import SummarySource
from solo_leveling.domain.learning_progress import LEARNING_STEPS
from solo_leveling.domain.quiz import QuizQuestion, QUESTION_TYPES

NOTICE = 'remote-quiz-generation-v1'


def require(condition):
    if not condition:
        raise ValueError('REMOTE_QUIZ_GENERATION_INVALID')


def encode(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=True,allow_nan=False,separators=(',',':'))


def load(raw):
    def pairs(values):
        result={}
        for key,value in values:
            require(key not in result)
            result[key]=value
        return result
    try:
        return json.loads(raw,object_pairs_hook=pairs,
            parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
    except (ValueError,TypeError):
        raise ValueError('REMOTE_QUIZ_GENERATION_INVALID') from None


@dataclass(frozen=True,repr=False)
class RemoteQuizGenerationInput:
    encoded:str

    @classmethod
    def create(cls,binding,sources,*,step,question_count,question_type,provider,model,prompt_version,
               language,budget_policy_version,max_input_tokens,max_output_tokens):
        require(type(binding) is RemoteSearchBinding and type(sources) is tuple
                and all(type(s) is SummarySource for s in sources))
        return cls(encode(dict(schema_version=1,notice_version=NOTICE,operation='generate_quiz',
            binding=asdict(binding),sources=[asdict(s) for s in sources],step=step,
            question_count=question_count,question_type=question_type,provider=provider,model=model,
            prompt_version=prompt_version,language=language,budget_policy_version=budget_policy_version,
            max_calls=1,max_input_tokens=max_input_tokens,max_output_tokens=max_output_tokens)))

    def __post_init__(self):
        self.unpack()

    def unpack(self):
        require(type(self.encoded) is str and len(self.encoded.encode())<=1048576)
        value=load(self.encoded)
        require(type(value) is dict and set(value)=={'schema_version','notice_version','operation','binding',
            'sources','step','question_count','question_type','provider','model','prompt_version','language',
            'budget_policy_version','max_calls','max_input_tokens','max_output_tokens'})
        require(type(value['schema_version']) is int and value['schema_version']==1
                and value['notice_version']==NOTICE and value['operation']=='generate_quiz'
                and type(value['max_calls']) is int and value['max_calls']==1)
        try:
            binding=RemoteSearchBinding(**value['binding'])
            require(binding.publication_policy=='complete')
            require(type(value['sources']) is list and 1<=len(value['sources'])<=20)
            sources=tuple(SummarySource(**s) for s in value['sources'])
        except (TypeError,ValueError):
            raise ValueError('REMOTE_QUIZ_GENERATION_INVALID') from None
        require(len({s.evidence_id for s in sources})==len(sources)
                and len({s.chunk_id for s in sources})==len(sources))
        require(value['step'] in LEARNING_STEPS and value['question_type'] in QUESTION_TYPES
                and type(value['question_count']) is int and 1<=value['question_count']<=20)
        require(all(type(value[k]) is str and 0<len(value[k].strip())<=512 for k in
                    ('provider','model','prompt_version','language','budget_policy_version')))
        require(all(type(value[k]) is int and 1<=value[k]<=1000000 for k in ('max_input_tokens','max_output_tokens')))
        return binding,sources,value

    @property
    def input_hash(self):
        return NOTICE+':'+hashlib.sha256(encode(self.unpack()[2]).encode()).hexdigest()

    def transfer_notice(self):
        binding,sources,value=self.unpack()
        return dict(notice_version=NOTICE,input_hash=self.input_hash,context_id=binding.context_id,
            publication_id=binding.publication_id,server_version_id=binding.server_version_id,
            step=value['step'],question_count=value['question_count'],question_type=value['question_type'],
            source_count=len(sources),provider=value['provider'],model=value['model'],
            max_calls=1,max_input_tokens=value['max_input_tokens'],max_output_tokens=value['max_output_tokens'],
            storage_consent_required=True,ai_consent_required=True,automatic_generation=False,
            storage_notice='퀴즈 생성 요청·문항·정답·근거 연결을 저장합니다. 답안 제출 저장은 별도 동의입니다.',
            ai_notice='선택 근거의 원문·번역·ID와 퀴즈 생성 지시를 지정 제공자에 전송합니다. 비용이 발생합니다.')


@dataclass(frozen=True,repr=False)
class QuizGenerationConsent:
    owner_user_id:str
    context_id:str
    input_hash:str
    purpose:str
    notice_version:str
    expires_at:datetime
    revoked:bool=False


def require_generation_consents(request,consents,*,now):
    require(type(request) is RemoteQuizGenerationInput and type(consents) is tuple and len(consents)==2
            and isinstance(now,datetime) and now.utcoffset() is not None)
    binding,_,_=request.unpack()
    for c in consents:
        require(type(c) is QuizGenerationConsent and c.owner_user_id==binding.owner_user_id
                and c.context_id==binding.context_id and c.input_hash==request.input_hash
                and c.notice_version==NOTICE and c.purpose in ('quiz_generation_storage','quiz_generation_ai')
                and c.revoked is False and isinstance(c.expires_at,datetime) and c.expires_at.utcoffset() is not None
                and c.expires_at>now)
    require({c.purpose for c in consents}=={'quiz_generation_storage','quiz_generation_ai'})


def validate_generated_questions(request,items):
    """모든 문항 검사 후에만 반환한다. 의미적 근거 일치나 정답 품질을 검증했다고 주장하지 않는다."""
    require(type(request) is RemoteQuizGenerationInput)
    _,sources,settings=request.unpack()
    require(type(items) is list and len(items)==settings['question_count'])
    known={s.evidence_id for s in sources};questions=[];prompts=set()
    for item in items:
        require(type(item) is dict and set(item)=={'question_type','prompt','evidence_ids','choices','correct_index','answer','grading_criteria'})
        require(item['question_type']==settings['question_type'] and type(item['evidence_ids']) is list
                and bool(item['evidence_ids']) and all(type(e) is str and e in known for e in item['evidence_ids'])
                and len(set(item['evidence_ids']))==len(item['evidence_ids'])
                and type(item['choices']) is list and len(item['choices'])<=6)
        require(all(type(item[k]) is str and len(item[k])<=8192 for k in ('prompt','answer','grading_criteria'))
                and all(type(c) is str and len(c)<=8192 for c in item['choices']))
        try:
            question=QuizQuestion(**item)
        except (TypeError,ValueError):
            raise ValueError('REMOTE_QUIZ_GENERATION_INVALID') from None
        require(question.prompt.strip() not in prompts)
        # 객관식 정답 문구가 함께 주어졌으면 선택지와 모순되는 응답도 거부한다.
        require(question.question_type!='multiple_choice' or item['answer']==question.answer)
        prompts.add(question.prompt.strip());questions.append(question)
    return tuple(questions)
