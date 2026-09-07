import type { Evidence, PaperDetail, PathEdge, RelationshipType, WeightedTerm } from "./models.js";

export const SCORING_WEIGHTS = {
  citation: 0.4,
  topic: 0.25,
  keyword: 0.15,
  target: 0.15,
  chronology: 0.05,
} as const;

const normalizeTerm = (value: string): string => value
  .normalize("NFKC")
  .toLowerCase()
  .replace(/[^\p{L}\p{N}\s]/gu, " ")
  .replace(/\s+/g, " ")
  .trim();

const weightedJaccard = (left: WeightedTerm[], right: WeightedTerm[]): number => {
  const leftMap = new Map(left.map((term) => [term.id ?? normalizeTerm(term.displayName), Math.max(0, term.score)]));
  const rightMap = new Map(right.map((term) => [term.id ?? normalizeTerm(term.displayName), Math.max(0, term.score)]));
  const keys = new Set([...leftMap.keys(), ...rightMap.keys()]);
  let intersection = 0;
  let union = 0;
  for (const key of keys) {
    const a = leftMap.get(key) ?? 0;
    const b = rightMap.get(key) ?? 0;
    intersection += Math.min(a, b);
    union += Math.max(a, b);
  }
  return union === 0 ? 0 : intersection / union;
};

const targetRelevance = (paper: PaperDetail, targetQuery?: string): number => {
  if (!targetQuery) return 0;
  const terms = normalizeTerm(targetQuery).split(" ").filter((term) => term.length >= 2);
  if (terms.length === 0) return 0;
  const haystack = normalizeTerm([
    paper.title,
    paper.primaryTopic?.displayName ?? "",
    ...paper.topics.map((topic) => topic.displayName),
    ...paper.keywords.map((keyword) => keyword.displayName),
  ].join(" "));
  return terms.filter((term) => haystack.includes(term)).length / terms.length;
};

const chronologicalScore = (from: PaperDetail, to: PaperDetail, relationship: RelationshipType): number => {
  const forward = relationship !== "cites";
  if (from.publicationDate && to.publicationDate) {
    return forward ? (from.publicationDate <= to.publicationDate ? 1 : 0) : (from.publicationDate >= to.publicationDate ? 1 : 0);
  }
  if (from.publicationYear !== null && to.publicationYear !== null) {
    return forward ? (from.publicationYear <= to.publicationYear ? 1 : 0) : (from.publicationYear >= to.publicationYear ? 1 : 0);
  }
  return 0.5;
};

const relationshipOf = (from: PaperDetail, to: PaperDetail): { type: RelationshipType; inferred: boolean } => {
  if (from.referencedWorkIds.includes(to.id)) return { type: "cites", inferred: false };
  if (to.referencedWorkIds.includes(from.id)) return { type: "cited_by", inferred: false };
  if (from.relatedWorkIds.includes(to.id) || to.relatedWorkIds.includes(from.id)) {
    return { type: "related", inferred: true };
  }
  return { type: "topic_similar", inferred: true };
};

const commonNames = (left: WeightedTerm[], right: WeightedTerm[]): string[] => {
  const rightKeys = new Set(right.map((term) => term.id ?? normalizeTerm(term.displayName)));
  return left
    .filter((term) => rightKeys.has(term.id ?? normalizeTerm(term.displayName)))
    .map((term) => term.displayName)
    .slice(0, 3);
};

export const scoreEdge = (from: PaperDetail, to: PaperDetail, targetQuery?: string): PathEdge => {
  const relationship = relationshipOf(from, to);
  const citation = relationship.inferred ? 0 : 1;
  const topic = weightedJaccard(from.topics, to.topics);
  const keyword = weightedJaccard(from.keywords, to.keywords);
  const target = targetRelevance(to, targetQuery);
  const chronology = chronologicalScore(from, to, relationship.type);

  const weights = targetQuery
    ? SCORING_WEIGHTS
    : { citation: 0.4, topic: 0.35, keyword: 0.2, target: 0, chronology: 0.05 };
  const raw = citation * weights.citation
    + topic * weights.topic
    + keyword * weights.keyword
    + target * weights.target
    + chronology * weights.chronology;
  const score = Math.min(1, Math.max(0, raw));

  const commonTopics = commonNames(from.topics, to.topics);
  const commonKeywords = commonNames(from.keywords, to.keywords);
  const evidence: Evidence[] = [];
  if (!relationship.inferred) {
    evidence.push({ kind: "citation", value: `${from.id} → ${to.id}`, source: "openalex" });
  } else if (relationship.type === "related") {
    evidence.push({ kind: "provider_relation", value: "OpenAlex related_works", source: "openalex" });
  }
  for (const value of commonTopics) evidence.push({ kind: "topic", value, source: "openalex" });
  for (const value of commonKeywords) evidence.push({ kind: "keyword", value, source: "openalex" });
  evidence.push({
    kind: "chronology",
    value: `${from.publicationDate ?? from.publicationYear ?? "연도 미상"} → ${to.publicationDate ?? to.publicationYear ?? "연도 미상"}`,
    source: "derived",
  });

  const relationText: Record<RelationshipType, string> = {
    cites: `${from.title}이(가) ${to.title}을(를) 참고문헌으로 인용합니다.`,
    cited_by: `${to.title}이(가) ${from.title}을(를) 참고문헌으로 인용합니다.`,
    related: "OpenAlex가 두 논문을 관련 연구로 연결합니다. 이 관계는 추론입니다.",
    topic_similar: "두 논문에 공통 주제 또는 키워드가 있습니다. 이 관계는 추론입니다.",
  };
  const common = [...commonTopics, ...commonKeywords].slice(0, 3);
  const rationale = common.length > 0 ? `${relationText[relationship.type]} 공통 항목: ${common.join(", ")}.` : relationText[relationship.type];

  return {
    fromId: from.id,
    toId: to.id,
    relationship: relationship.type,
    evidence: evidence.slice(0, 5),
    score: Number(score.toFixed(6)),
    inferred: relationship.inferred,
    rationale,
  };
};

export const matchedTerms = (paper: PaperDetail, query?: string): string[] => {
  if (!query) return [];
  const haystack = [paper.title, ...paper.topics.map((item) => item.displayName), ...paper.keywords.map((item) => item.displayName)];
  const terms = normalizeTerm(query).split(" ").filter((term) => term.length >= 2);
  return terms.filter((term) => haystack.some((value) => normalizeTerm(value).includes(term))).slice(0, 10);
};

export const scoringInternals = { normalizeTerm, weightedJaccard, targetRelevance, chronologicalScore };
