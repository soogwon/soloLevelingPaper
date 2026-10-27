"""모델 판정을 승인하지 않고 좁은 실패 패턴만 차단하는 보조 규칙."""

import re

def metric_substitution(expected, actual):
    """명시적인 계산량 요구를 순차 연산만으로 대체한 좁은 패턴을 차단한다.

    단어가 있다는 사실로 의미 충족을 승인하지 않는다. 불명확한 표현은 모델 검사에 맡긴다.
    """
    complexity = re.compile(r'계산\s*복잡도|계산\s*복잡성|총\s*(?:계산량|연산량)|'
                            r'층당\s*계산량|computational\s+complexity|complexity\s+per\s+layer|total\s+computation', re.I)
    sequential = re.compile(r'순차(?:적)?(?:으로)?\s*(?:실행(?:되는)?\s*)?(?:연산|계산)|'
                            r'sequential(?:ly\s+executed)?\s+operations?', re.I)
    text = ' '.join(actual)
    # 두 기준을 모두 요구하거나 답한 경우에는 이 좁은 규칙으로 결론을 내리지 않는다.
    return bool(complexity.search(expected) and not sequential.search(expected)
                and sequential.search(text) and not complexity.search(text))


def variable_only_condition(question, text):
    """조건 질문에 변수 이름을 조건처럼 붙인 비교 주장을 좁게 차단한다.

    비교 기호가 존재한다고 정답으로 승인하지 않는다. 실제 의미·원문 검사는 별도로 유지한다.
    """
    if not re.search(r'조건|어떤\s*경우|언제|under\s+what\s+conditions?|when', question, re.I):
        return False
    variable = r'[A-Za-z](?:_[A-Za-z0-9]+)?'
    placeholder = re.compile(
        rf'(?:길이|차원|크기|수)\s*(?:가|이|는|은)?\s*{variable}\s*(?:일\s*때|인\s*경우|일\s*경우|에\s*대해)|'
        rf'(?:when\s+(?:the\s+)?(?:sequence\s+)?(?:length|dimension|size)\s+is\s+{variable}\b)', re.I)
    comparison = re.compile(r'[<>≤≥=]|보다\s*(?:작|크|적|많)|미만|이하|이상|초과|'
                            r'less\s+than|smaller\s+than|greater\s+than|larger\s+than', re.I)
    for sentence in re.split(r'(?<=[.!?。！？])\s+|\n+', text):
        if (placeholder.search(sentence)
                and re.search(r'더\s*(?:빠르|빠른|낮|높)|faster|lower|higher', sentence, re.I)
                and not comparison.search(sentence)):
            return True
    return False
