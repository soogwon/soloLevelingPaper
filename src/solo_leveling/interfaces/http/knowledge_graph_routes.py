"""검수 KG를 외부 웹에 읽기 전용으로 제공한다.

그래프는 사용자와 무관한 정적 시드라서 DB에 저장하지 않고 시작 시 메모리에 올린다.
로컬 MCP와 같은 시드 파일·같은 경로 계산을 사용한다. 개인 known_concepts는 저장하지 않는다.
"""

import os
from typing import Any

from solo_leveling.application.learning_path.views import (
    paper_graph_view, paper_summary_view, path_view,
)
from solo_leveling.domain.knowledge_graph import KnowledgeGraph, learning_path

MAX_KNOWN = 50


def load_server_graph() -> KnowledgeGraph | None:
    from solo_leveling.knowledge import SEED_ENV, load_graph
    try:
        return load_graph(os.environ.get(SEED_ENV) or None)
    except Exception:
        return None


def create_knowledge_graph_router(require_principal: Any, graph: KnowledgeGraph | None):
    from typing import Literal

    from fastapi import APIRouter, Depends, HTTPException, Query, status

    router = APIRouter(prefix="/v1/knowledge-graph", tags=["knowledge-graph"])

    def selected() -> KnowledgeGraph:
        if graph is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="knowledge_graph_unavailable")
        return graph

    def paper_or_404(paper_key: str):
        current = selected()
        paper = next((p for p in current.papers if p.paper_key == paper_key), None)
        if paper is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="paper_not_found")
        return current, paper

    @router.get("/papers")
    def list_papers(_principal=Depends(require_principal)) -> dict:
        current = selected()
        return {"graph_revision_id": current.revision_id,
                "items": [paper_summary_view(p) for p in current.papers]}

    @router.get("/papers/{paper_key}")
    def get_paper(paper_key: str, _principal=Depends(require_principal)) -> dict:
        current, paper = paper_or_404(paper_key)
        return {"graph_revision_id": current.revision_id, **paper_graph_view(current, paper)}

    @router.get("/papers/{paper_key}/path")
    def get_path(
        paper_key: str,
        goal: Literal["understand", "implement", "skim"] = "understand",
        mode: Literal["compact", "expanded"] = "compact",
        known: list[str] = Query(default_factory=list, max_length=MAX_KNOWN),
        _principal=Depends(require_principal),
    ) -> dict:
        current, paper = paper_or_404(paper_key)
        # 웹은 쉼표로 묶어 보내고, 다른 클라이언트는 known을 여러 번 보낼 수 있다.
        names = [part.strip() for value in known for part in value.split(",") if part.strip()]
        if len(names) > MAX_KNOWN:
            raise HTTPException(422, detail="too_many_known")
        if any(len(value) > 200 for value in names):
            raise HTTPException(422, detail="known_too_long")
        path = learning_path(current, paper, goal, list(dict.fromkeys(names)), mode)
        return {"graph_revision_id": current.revision_id, "paper_key": paper.paper_key,
                "goal": goal, "mode": mode, **path_view(path)}

    return router
