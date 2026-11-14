"""검수한 개념·선수 관계 시드를 검증하고 목적별 학습 경로를 계산한다.

DB·네트워크에 의존하지 않는 순수 모듈이다. 로컬 MCP와 중앙 서버가 같은 시드와
같은 계산을 공유한다. 선수 관계는 교육적 판단이며 논문 본문에 명시돼 있다고 가정하지 않는다.
"""

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Iterable, Literal

SEARCH_PHRASE_MAX = 60  # 짧은 용어·소제목만. 문장 인용을 막는다.
GOALS = ('understand', 'implement', 'skim')
Mode = Literal['compact', 'expanded']

_ID = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
_SHA256 = re.compile(r'[0-9a-f]{64}')


class SeedValidationError(ValueError):
    """시드 문제를 모두 모아 한 번에 알린다."""

    def __init__(self, problems: list[str]):
        super().__init__('; '.join(problems))
        self.problems = problems


class ConceptNotFoundError(LookupError):
    pass


def normalize_name(value: str) -> str:
    return ' '.join(value.casefold().split())


@dataclass(frozen=True)
class Concept:
    concept_id: str
    name_ko: str
    name_en: str
    aliases: tuple[str, ...]
    description_ko: str


@dataclass(frozen=True)
class Prerequisite:
    prerequisite_id: str
    concept_id: str
    rationale_ko: str
    source_reference: str | None


@dataclass(frozen=True)
class PaperConcept:
    concept_id: str
    pdf_pages: tuple[int, ...]
    section_hint: str | None
    # 사용자가 자기 PDF에서 Ctrl+F로 찾을 짧은 용어·소제목. 논문 문장은 넣지 않는다.
    search_phrase: str | None = None


@dataclass(frozen=True)
class GoalPlan:
    targets: tuple[str, ...]
    optional: tuple[str, ...]
    reasons: dict[str, str]


@dataclass(frozen=True)
class PaperGraph:
    paper_key: str
    label: str
    file_sha256: str | None
    source_url: str | None
    concepts: dict[str, PaperConcept]
    goals: dict[str, GoalPlan]

    @property
    def arxiv_id(self) -> str | None:
        """source_url의 arXiv 번호(판본 제외). 같은 논문의 다른 판본을 알아보는 데 쓴다."""
        return arxiv_base_id(self.source_url or '')


_ARXIV = re.compile(r'(?:arxiv\.org/(?:abs|pdf)/|arxiv:\s*)(\d{4}\.\d{4,5})(?:v\d+)?', re.IGNORECASE)


def arxiv_base_id(text: str) -> str | None:
    """'arXiv:1706.03762v7'이나 arXiv URL에서 판본을 뺀 번호(1706.03762)를 꺼낸다."""
    match = _ARXIV.search(text or '')
    return match.group(1) if match else None


@dataclass(frozen=True)
class KnowledgeGraph:
    revision_id: str
    concepts: dict[str, Concept]
    prerequisites: tuple[Prerequisite, ...]
    papers: tuple[PaperGraph, ...]
    warnings: tuple[str, ...] = ()
    _names: dict[str, str] = field(default_factory=dict, repr=False, compare=False)
    _requires: dict[str, tuple[Prerequisite, ...]] = field(default_factory=dict, repr=False, compare=False)
    _required_by: dict[str, tuple[Prerequisite, ...]] = field(default_factory=dict, repr=False, compare=False)

    def resolve(self, name: str) -> str | None:
        """개념 id·한영 이름·별칭을 정규화해 정확히 일치하는 id를 찾는다."""
        return self._names.get(normalize_name(name))

    def requires(self, concept_id: str) -> tuple[Prerequisite, ...]:
        return self._requires.get(concept_id, ())

    def required_by(self, concept_id: str) -> tuple[Prerequisite, ...]:
        return self._required_by.get(concept_id, ())

    def paper_by_hash(self, file_hash: str) -> PaperGraph | None:
        key = (file_hash or '').lower()
        return next((p for p in self.papers if p.file_sha256 == key), None)

    def paper_by_arxiv(self, arxiv_id: str | None) -> PaperGraph | None:
        """검수 판본과 파일은 다르지만 arXiv 번호가 같은 논문을 찾는다."""
        if not arxiv_id:
            return None
        return next((p for p in self.papers if p.arxiv_id == arxiv_id), None)


def parse_seed(raw: bytes | str) -> KnowledgeGraph:
    """JSON 시드를 읽어 검증한다. 문제가 하나라도 있으면 아무것도 반환하지 않는다."""
    try:
        text = raw.decode('utf-8') if isinstance(raw, bytes) else raw
        data = json.loads(text)
    except UnicodeDecodeError:
        raise SeedValidationError(['시드는 UTF-8이어야 합니다.']) from None
    except json.JSONDecodeError as error:
        raise SeedValidationError([f'JSON 형식 오류: {error.msg} (line {error.lineno})']) from None
    revision = 'kg-' + hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]
    return build_graph(data, revision)


def build_graph(data: dict, revision_id: str) -> KnowledgeGraph:
    problems: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict) or data.get('schema_version') != 1:
        raise SeedValidationError(['schema_version은 1이어야 합니다.'])

    concepts: dict[str, Concept] = {}
    names: dict[str, str] = {}
    for index, item in enumerate(_list(data, 'concepts', problems)):
        where = f'concepts[{index}]'
        cid = item.get('id') if isinstance(item, dict) else None
        if not isinstance(cid, str) or not _ID.fullmatch(cid):
            problems.append(f'{where}: id는 소문자·숫자·하이픈만 허용합니다.')
            continue
        if cid in concepts:
            problems.append(f'{where}: 개념 id 중복 {cid}')
            continue
        fields = {key: item.get(key) for key in ('name_ko', 'name_en', 'description_ko')}
        if any(not isinstance(v, str) or not v.strip() for v in fields.values()):
            problems.append(f'{where}({cid}): name_ko·name_en·description_ko는 비어 있지 않은 문자열이어야 합니다.')
            continue
        aliases = item.get('aliases', [])
        if not isinstance(aliases, list) or any(not isinstance(a, str) or not a.strip() for a in aliases):
            problems.append(f'{where}({cid}): aliases는 문자열 목록이어야 합니다.')
            continue
        concept = Concept(cid, fields['name_ko'].strip(), fields['name_en'].strip(),
                          tuple(a.strip() for a in aliases), fields['description_ko'].strip())
        concepts[cid] = concept
        for name in {cid, concept.name_ko, concept.name_en, *concept.aliases}:
            key = normalize_name(name)
            owner = names.get(key)
            if owner is not None and owner != cid:
                problems.append(f'{where}({cid}): 이름·별칭 "{name}"이 {owner}와 겹칩니다.')
            names[key] = cid

    prerequisites: list[Prerequisite] = []
    seen_edges = set()
    for index, item in enumerate(_list(data, 'prerequisites', problems)):
        where = f'prerequisites[{index}]'
        if not isinstance(item, dict):
            problems.append(f'{where}: 객체여야 합니다.')
            continue
        source, target = item.get('prerequisite'), item.get('concept')
        missing = [v for v in (source, target) if not isinstance(v, str) or v not in concepts]
        if missing:
            problems.append(f'{where}: 없는 개념 참조 {", ".join(map(str, missing))}')
            continue
        if source == target:
            problems.append(f'{where}: 자기 자신을 선수로 둘 수 없습니다 ({source}).')
            continue
        if (source, target) in seen_edges:
            problems.append(f'{where}: 중복 관계 {source} → {target}')
            continue
        rationale = item.get('rationale_ko')
        reference = item.get('source_reference')
        if not isinstance(rationale, str) or not rationale.strip():
            problems.append(f'{where}: rationale_ko가 필요합니다.')
            continue
        if reference is not None and (not isinstance(reference, str) or not reference.strip()):
            problems.append(f'{where}: source_reference는 null 또는 문자열이어야 합니다.')
            continue
        seen_edges.add((source, target))
        prerequisites.append(Prerequisite(source, target, rationale.strip(), reference))

    requires: dict[str, list[Prerequisite]] = {}
    required_by: dict[str, list[Prerequisite]] = {}
    for edge in prerequisites:
        requires.setdefault(edge.concept_id, []).append(edge)
        required_by.setdefault(edge.prerequisite_id, []).append(edge)
    cycle = _find_cycle(concepts, requires)
    if cycle:
        problems.append('선수 관계 순환: ' + ' → '.join(cycle))

    papers: list[PaperGraph] = []
    hashes = set()
    for index, item in enumerate(_list(data, 'papers', problems)):
        paper = _build_paper(index, item, concepts, problems, warnings)
        if paper is None:
            continue
        if paper.paper_key in {p.paper_key for p in papers}:
            problems.append(f'papers[{index}]: key 중복 {paper.paper_key}')
        if paper.file_sha256 is not None:
            if paper.file_sha256 in hashes:
                problems.append(f'papers[{index}]: file_sha256 중복')
            hashes.add(paper.file_sha256)
        papers.append(paper)

    if problems:
        raise SeedValidationError(problems)
    return KnowledgeGraph(
        revision_id, concepts, tuple(prerequisites), tuple(papers), tuple(warnings),
        names, {k: tuple(v) for k, v in requires.items()}, {k: tuple(v) for k, v in required_by.items()})


def _list(data: dict, key: str, problems: list[str]) -> list:
    value = data.get(key)
    if not isinstance(value, list):
        problems.append(f'{key}는 목록이어야 합니다.')
        return []
    return value


def _find_cycle(concepts, requires) -> list[str] | None:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for edge in requires.get(node, ()):
            nxt = edge.prerequisite_id
            if state.get(nxt) == 1:
                return stack[stack.index(nxt):] + [nxt]
            if nxt not in state:
                found = visit(nxt)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return None

    for node in sorted(concepts):
        if node not in state:
            found = visit(node)
            if found:
                return found
    return None


def _build_paper(index, item, concepts, problems, warnings) -> PaperGraph | None:
    where = f'papers[{index}]'
    if not isinstance(item, dict):
        problems.append(f'{where}: 객체여야 합니다.')
        return None
    key, label = item.get('key'), item.get('label')
    if not isinstance(key, str) or not _ID.fullmatch(key):
        problems.append(f'{where}: key는 소문자·숫자·하이픈만 허용합니다.')
        return None
    where = f'papers[{index}]({key})'
    if not isinstance(label, str) or not label.strip():
        problems.append(f'{where}: label이 필요합니다.')
        label = ''
    file_hash = item.get('file_sha256')
    if file_hash is None:
        warnings.append(f'{where}: file_sha256이 없어 등록 논문과 연결되지 않습니다.')
    elif not isinstance(file_hash, str) or not _SHA256.fullmatch(file_hash.lower()):
        problems.append(f'{where}: file_sha256은 64자 16진수 또는 null이어야 합니다.')
        file_hash = None
    else:
        file_hash = file_hash.lower()
    source_url = item.get('source_url')
    if source_url is not None and not isinstance(source_url, str):
        problems.append(f'{where}: source_url은 문자열 또는 null이어야 합니다.')
        source_url = None

    paper_concepts: dict[str, PaperConcept] = {}
    for c_index, entry in enumerate(_list(item, 'concepts', problems)):
        cid = entry.get('id') if isinstance(entry, dict) else None
        if not isinstance(cid, str) or cid not in concepts:
            problems.append(f'{where}.concepts[{c_index}]: 없는 개념 {cid}')
            continue
        if cid in paper_concepts:
            problems.append(f'{where}.concepts[{c_index}]: 중복 개념 {cid}')
            continue
        pages = entry.get('pdf_pages', [])
        if (not isinstance(pages, list)
                or any(isinstance(p, bool) or not isinstance(p, int) or p < 1 for p in pages)):
            problems.append(f'{where}.concepts[{c_index}]({cid}): pdf_pages는 양의 정수 목록이어야 합니다.')
            continue
        hint = entry.get('section_hint')
        if hint is not None and (not isinstance(hint, str) or not hint.strip()):
            problems.append(f'{where}.concepts[{c_index}]({cid}): section_hint는 null 또는 문자열이어야 합니다.')
            continue
        phrase = entry.get('search_phrase')
        if phrase is not None and (not isinstance(phrase, str) or not 2 <= len(phrase.strip()) <= SEARCH_PHRASE_MAX
                                   or len(phrase.splitlines()) != 1):
            problems.append(f'{where}.concepts[{c_index}]({cid}): search_phrase는 null 또는 '
                            f'한 줄 {SEARCH_PHRASE_MAX}자 이하 문자열이어야 합니다.')
            continue
        paper_concepts[cid] = PaperConcept(cid, tuple(sorted(set(pages))), hint,
                                           phrase.strip() if phrase else None)

    goals: dict[str, GoalPlan] = {}
    raw_goals = item.get('goals')
    if not isinstance(raw_goals, dict) or set(raw_goals) != set(GOALS):
        problems.append(f'{where}: goals에는 understand·implement·skim이 모두 있어야 합니다.')
        raw_goals = {}
    for goal, plan in raw_goals.items():
        gw = f'{where}.goals.{goal}'
        if not isinstance(plan, dict):
            problems.append(f'{gw}: 객체여야 합니다.')
            continue
        targets, optional = plan.get('targets'), plan.get('optional', [])
        reasons = plan.get('reasons', {})
        if not isinstance(targets, list) or not targets:
            problems.append(f'{gw}: target이 1개 이상 필요합니다.')
            continue
        if not isinstance(optional, list) or not isinstance(reasons, dict):
            problems.append(f'{gw}: optional은 목록, reasons는 객체여야 합니다.')
            continue
        if any(not isinstance(cid, str) for cid in [*targets, *optional]):
            problems.append(f'{gw}: 개념 id는 문자열이어야 합니다.')
            continue
        both = set(targets) & set(optional)
        if both:
            problems.append(f'{gw}: target과 optional에 동시에 있음 {", ".join(sorted(both))}')
        if len(set(targets)) != len(targets) or len(set(optional)) != len(optional):
            problems.append(f'{gw}: 중복 항목이 있습니다.')
        for cid in [*targets, *optional]:
            if cid not in paper_concepts:
                problems.append(f'{gw}: {cid}가 이 논문의 concepts에 없습니다.')
            reason = reasons.get(cid)
            if not isinstance(reason, str) or not reason.strip():
                problems.append(f'{gw}: {cid}의 reasons가 필요합니다.')
        goals[goal] = GoalPlan(tuple(targets), tuple(optional),
                               {k: v.strip() for k, v in reasons.items() if isinstance(v, str)})
    return PaperGraph(key, (label or '').strip(), file_hash, source_url, paper_concepts, goals)


# ---------------------------------------------------------------- 경로 계산

@dataclass(frozen=True)
class PathStep:
    order: int
    concept: Concept
    role: Literal['target', 'required', 'optional']
    reason_ko: str
    prerequisite_ids: tuple[str, ...]
    locations: PaperConcept | None
    # required일 때 reason_ko가 설명하는 관계의 상대, 즉 이 개념을 경로에서 처음 필요로 하는 개념
    needed_for: Concept | None = None


@dataclass(frozen=True)
class SkippedConcept:
    concept: Concept
    reason: Literal['known', 'only_needed_by_known']


@dataclass(frozen=True)
class LearningPath:
    steps: tuple[PathStep, ...]
    skipped: tuple[SkippedConcept, ...]
    unresolved: tuple[str, ...]


def resolve_known(graph: KnowledgeGraph, names: Iterable[str]) -> tuple[set[str], tuple[str, ...]]:
    known: set[str] = set()
    unresolved: list[str] = []
    for name in names:
        cid = graph.resolve(name)
        if cid is None:
            unresolved.append(name)
        else:
            known.add(cid)
    return known, tuple(unresolved)


def _closure(graph: KnowledgeGraph, starts: Iterable[str], stop: set[str]) -> set[str]:
    seen: set[str] = set()
    pending = [s for s in starts if s not in stop]
    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node)
        pending.extend(e.prerequisite_id for e in graph.requires(node)
                       if e.prerequisite_id not in stop and e.prerequisite_id not in seen)
    return seen


def learning_path(graph: KnowledgeGraph, paper: PaperGraph, goal: str,
                  known_names: Iterable[str] = (), mode: Mode = 'compact') -> LearningPath:
    """목표 개념에서 선수 관계를 거꾸로 모아 위상 정렬한다.

    known 개념과 그 아래 선수는 내려가지 않는다. 건너뛰기는 이해의 증거로 취급하지 않는다.
    같은 입력에는 항상 같은 순서를 돌려준다.
    """
    plan = paper.goals[goal]
    known, unresolved = resolve_known(graph, known_names)
    starts = list(plan.targets) + (list(plan.optional) if mode == 'expanded' else [])

    rank: dict[str, int] = {}
    for position, start in enumerate(starts):
        for node in _closure(graph, [start], set()):
            rank[node] = min(rank.get(node, position), position)

    target_set = set(plan.targets)
    core = _closure(graph, plan.targets, known)
    included = set(core)
    if mode == 'expanded':
        included |= _closure(graph, plan.optional, known)
    everything = _closure(graph, starts, set())

    role = {c: ('target' if c in target_set else 'required' if c in core else 'optional')
            for c in included}

    # 시드에 적은 선수 순서대로 깊이 우선 후위 순회한다. 위상 순서를 지키면서
    # 한 개념에 필요한 선수들이 그 개념 바로 앞에 모이도록 해 학습 흐름이 끊기지 않는다.
    ordered: list[str] = []
    visited: set[str] = set()

    def visit(node: str) -> None:
        visited.add(node)
        for edge in graph.requires(node):
            if edge.prerequisite_id in included and edge.prerequisite_id not in visited:
                visit(edge.prerequisite_id)
        ordered.append(node)

    for start in starts:
        if start in included and start not in visited:
            visit(start)
    order_of = {c: i for i, c in enumerate(ordered)}

    steps = []
    for index, cid in enumerate(ordered):
        needed_for = None
        if cid in plan.reasons and role[cid] != 'required':
            reason = plan.reasons[cid]
        else:
            users = sorted((e for e in graph.required_by(cid) if e.concept_id in order_of),
                           key=lambda e: order_of[e.concept_id])
            reason = users[0].rationale_ko if users else plan.reasons.get(cid, '')
            needed_for = graph.concepts[users[0].concept_id] if users else None
        steps.append(PathStep(
            order=index + 1, concept=graph.concepts[cid], role=role[cid], reason_ko=reason,
            prerequisite_ids=tuple(sorted((e.prerequisite_id for e in graph.requires(cid)
                                           if e.prerequisite_id in included),
                                          key=lambda p: order_of[p])),
            locations=paper.concepts.get(cid), needed_for=needed_for))

    skipped = []
    for cid in sorted(everything - included, key=lambda c: (rank.get(c, 0), c)):
        if mode == 'compact' and cid not in known and cid not in _closure(graph, plan.targets, set()):
            continue  # optional에서만 닿는 개념은 compact에서 건너뛴 것이 아니다.
        skipped.append(SkippedConcept(graph.concepts[cid],
                                      'known' if cid in known else 'only_needed_by_known'))
    return LearningPath(tuple(steps), tuple(skipped), unresolved)
