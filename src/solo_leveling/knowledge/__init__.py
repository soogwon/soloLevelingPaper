"""패키지에 포함한 검수 KG 시드를 읽는다. 로컬 MCP와 중앙 서버가 같은 파일을 사용한다."""

from importlib.resources import files
from pathlib import Path

from solo_leveling.domain.knowledge_graph import KnowledgeGraph, parse_seed

SEED_ENV = 'SOLO_LEVELING_KG_SEED'


def default_seed_bytes() -> bytes:
    return files('solo_leveling.knowledge').joinpath('seed.json').read_bytes()


def load_graph(path: str | Path | None = None) -> KnowledgeGraph:
    """경로를 주면 그 파일을, 없으면 패키지 기본 시드를 검증해 읽는다."""
    raw = Path(path).read_bytes() if path else default_seed_bytes()
    return parse_seed(raw)
