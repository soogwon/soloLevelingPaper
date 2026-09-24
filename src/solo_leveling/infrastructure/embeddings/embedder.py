"""
infrastructure/embeddings — sentence-transformers 래퍼

22번 문서: "정확한 모델 ID·리비전·차원·CPU 성능"은 미결정 사항으로 명시됨.
여기서는 levelup-paper 프로토타입에서 실측 검증(한국어↔영어 교차 언어 매칭
유사도 0.61)했던 다국어 모델을 기본값으로 두되, 팀 합의 전까지는 잠정값임을
명확히 한다.
"""
from typing import List

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

# TODO(팀 합의 필요): 정확한 모델 ID·리비전 확정 전까지의 잠정값.
DEFAULT_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

_model_cache: dict = {}


def _get_model(model_name: str) -> "SentenceTransformer":
    if model_name not in _model_cache:
        from sentence_transformers import SentenceTransformer

        _model_cache[model_name] = SentenceTransformer(model_name)
    return _model_cache[model_name]


def embed_texts(texts: List[str], model_name: str = DEFAULT_MODEL_NAME) -> List[List[float]]:
    if not texts:
        return []
    model = _get_model(model_name)
    vectors = model.encode(texts, convert_to_numpy=True)
    return [v.tolist() for v in vectors]


def embedding_dimension(model_name: str = DEFAULT_MODEL_NAME) -> int:
    model = _get_model(model_name)
    return model.get_sentence_embedding_dimension()
