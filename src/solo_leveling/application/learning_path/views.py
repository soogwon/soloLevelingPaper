"""KG 계산 결과를 MCP·HTTP 공통 JSON 형태로 바꾼다."""

from solo_leveling.domain.knowledge_graph import (
    Concept, KnowledgeGraph, LearningPath, PaperConcept, PaperGraph,
)


def concept_view(concept: Concept) -> dict:
    return {'concept_id': concept.concept_id, 'name_ko': concept.name_ko,
            'name_en': concept.name_en, 'description_ko': concept.description_ko}


def locations_view(location: PaperConcept | None) -> list[dict]:
    if location is None:
        return []
    return [{'pdf_page': page, 'section_hint': location.section_hint,
             'search_phrase': location.search_phrase} for page in location.pdf_pages]


def path_view(path: LearningPath) -> dict:
    return {
        'steps': [{
            'order': step.order, **concept_view(step.concept), 'role': step.role,
            'reason_ko': step.reason_ko, 'prerequisite_ids': list(step.prerequisite_ids),
            'paper_locations': locations_view(step.locations),
        } for step in path.steps],
        'skipped': [{'concept_id': s.concept.concept_id, 'name_ko': s.concept.name_ko,
                     'reason': s.reason} for s in path.skipped],
        'unresolved_concepts': list(path.unresolved),
    }


def prerequisites_view(graph: KnowledgeGraph, concept_id: str, paper: PaperGraph | None) -> dict:
    concept = graph.concepts[concept_id]
    return {
        'concept': concept_view(concept),
        'prerequisites': [{
            'concept_id': e.prerequisite_id, 'name_ko': graph.concepts[e.prerequisite_id].name_ko,
            'rationale_ko': e.rationale_ko, 'source_reference': e.source_reference,
        } for e in graph.requires(concept_id)],
        'required_by': [{
            'concept_id': e.concept_id, 'name_ko': graph.concepts[e.concept_id].name_ko,
        } for e in graph.required_by(concept_id)],
        'paper_locations': locations_view(paper.concepts.get(concept_id) if paper else None),
    }


def paper_summary_view(paper: PaperGraph) -> dict:
    return {'paper_key': paper.paper_key, 'label': paper.label, 'source_url': paper.source_url,
            'concept_count': len(paper.concepts)}


def paper_graph_view(graph: KnowledgeGraph, paper: PaperGraph) -> dict:
    """논문에 쓰인 개념과 그 선수 폐쇄 집합, 그 사이의 관계를 그림용으로 돌려준다."""
    nodes: set[str] = set()
    pending = list(paper.concepts)
    while pending:
        node = pending.pop()
        if node in nodes:
            continue
        nodes.add(node)
        pending.extend(e.prerequisite_id for e in graph.requires(node))
    return {
        **paper_summary_view(paper),
        'concepts': [{**concept_view(graph.concepts[c]), 'in_paper': c in paper.concepts,
                      'paper_locations': locations_view(paper.concepts.get(c))}
                     for c in sorted(nodes)],
        'prerequisites': [{'prerequisite_id': e.prerequisite_id, 'concept_id': e.concept_id,
                           'rationale_ko': e.rationale_ko}
                          for e in graph.prerequisites if e.concept_id in nodes],
        'goals': {goal: {'targets': list(plan.targets), 'optional': list(plan.optional)}
                  for goal, plan in paper.goals.items()},
    }
