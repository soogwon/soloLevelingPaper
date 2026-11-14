"""외부 웹용 KG 읽기 API를 확인한다."""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from solo_leveling.domain.knowledge_graph import build_graph
from solo_leveling.interfaces.http.knowledge_graph_routes import (
    create_knowledge_graph_router, load_server_graph,
)
from tests.unit.test_knowledge_graph import seed


def client(graph, authenticated=True):
    def principal():
        if not authenticated:
            raise HTTPException(401, detail="unauthorized")
        return object()

    app = FastAPI()
    app.include_router(create_knowledge_graph_router(principal, graph))
    return TestClient(app)


@pytest.fixture
def api():
    return client(build_graph(seed(), "kg-test"))


def test_lists_papers_and_returns_graph_for_drawing(api):
    listing = api.get("/v1/knowledge-graph/papers").json()
    assert listing == {"graph_revision_id": "kg-test", "items": [
        {"paper_key": "paper", "label": "Paper", "source_url": None, "concept_count": 3}]}
    paper = api.get("/v1/knowledge-graph/papers/paper").json()
    ids = {c["concept_id"]: c["in_paper"] for c in paper["concepts"]}
    assert ids == {"vector": False, "dot": False, "softmax": False, "attention": True,
                   "transformer": True, "extra": True}
    assert {"prerequisite_id": "dot", "concept_id": "attention", "rationale_ko": "dot→attention"} in paper["prerequisites"]
    assert paper["goals"]["skim"] == {"targets": ["transformer"], "optional": ["extra"]}


def test_path_uses_goal_mode_and_known(api):
    result = api.get("/v1/knowledge-graph/papers/paper/path",
                     params={"goal": "skim", "mode": "expanded", "known": ["내적, 내적", "없음"]}).json()
    assert [s["concept_id"] for s in result["steps"]] == ["softmax", "attention", "transformer", "extra"]
    assert result["unresolved_concepts"] == ["없음"]
    assert result["goal"] == "skim" and result["mode"] == "expanded"


@pytest.mark.parametrize("path, params, code", [
    ("/v1/knowledge-graph/papers/missing", {}, 404),
    ("/v1/knowledge-graph/papers/paper/path", {"goal": "memorize"}, 422),
    ("/v1/knowledge-graph/papers/paper/path", {"mode": "full"}, 422),
    ("/v1/knowledge-graph/papers/paper/path", {"known": "x" * 201}, 422),
])
def test_invalid_requests(api, path, params, code):
    assert api.get(path, params=params).status_code == code


def test_requires_principal_and_reports_missing_graph():
    assert client(build_graph(seed(), "kg"), authenticated=False).get(
        "/v1/knowledge-graph/papers").status_code == 401
    response = client(None).get("/v1/knowledge-graph/papers")
    assert response.status_code == 503 and response.json()["detail"] == "knowledge_graph_unavailable"


def test_server_loads_packaged_seed(monkeypatch, tmp_path):
    monkeypatch.delenv("SOLO_LEVELING_KG_SEED", raising=False)
    assert len(load_server_graph().papers) == 3
    bad = tmp_path / "seed.json"
    bad.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("SOLO_LEVELING_KG_SEED", str(bad))
    assert load_server_graph() is None
