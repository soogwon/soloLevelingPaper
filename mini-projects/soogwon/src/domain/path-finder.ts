import type { ConceptPath, PaperDetail, PathEdge, PathNode } from "./models.js";
import { matchedTerms } from "./scoring.js";

export type Graph = {
  papers: Map<string, PaperDetail>;
  edges: Map<string, PathEdge[]>;
};

type CandidatePath = { ids: string[]; edges: PathEdge[]; score: number };

const scorePath = (edges: PathEdge[], targetMatchCount: number): number => {
  if (edges.length === 0) return 0;
  const product = edges.reduce((total, edge) => total * Math.max(edge.score, 0.000001), 1);
  const geometricMean = product ** (1 / edges.length);
  const lengthPenalty = Math.max(0, edges.length - 3) * 0.02;
  const targetBonus = Math.min(0.1, targetMatchCount * 0.02);
  return Number(Math.max(0, Math.min(1, geometricMean - lengthPenalty + targetBonus)).toFixed(6));
};

export const findBestPath = (
  graph: Graph,
  seedId: string,
  maxPathLength: number,
  targetQuery?: string,
): ConceptPath | null => {
  const candidates: CandidatePath[] = [];

  const visit = (currentId: string, ids: string[], edges: PathEdge[]): void => {
    if (ids.length >= 2) {
      const last = graph.papers.get(currentId);
      const targetMatches = last ? matchedTerms(last, targetQuery).length : 0;
      candidates.push({ ids: [...ids], edges: [...edges], score: scorePath(edges, targetMatches) });
    }
    if (ids.length >= maxPathLength) return;
    for (const edge of graph.edges.get(currentId) ?? []) {
      if (edge.score < 0.25 || ids.includes(edge.toId)) continue;
      visit(edge.toId, [...ids, edge.toId], [...edges, edge]);
    }
  };

  visit(seedId, [seedId], []);
  candidates.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    const directB = b.edges.filter((edge) => !edge.inferred).length;
    const directA = a.edges.filter((edge) => !edge.inferred).length;
    if (directB !== directA) return directB - directA;
    return a.ids.join("|").localeCompare(b.ids.join("|"));
  });

  const longCandidates = candidates.filter((candidate) => candidate.ids.length >= 3);
  const preferredCandidates = longCandidates.filter((candidate) => candidate.score >= 0.35);
  const comparableCandidates = preferredCandidates.length > 0 ? preferredCandidates : candidates;
  const best = comparableCandidates[0];
  if (!best || best.score < 0.35) return null;
  const second = comparableCandidates.find((candidate) => (
    candidate !== best
    && candidate.ids[1] !== best.ids[1]
  ));
  const nodes: PathNode[] = best.ids.map((id, index) => ({
    index,
    paper: graph.papers.get(id)!,
    matchedTerms: matchedTerms(graph.papers.get(id)!, targetQuery),
  }));
  return {
    nodes,
    edges: best.edges,
    score: best.score,
    scoreMargin: second ? Number((best.score - second.score).toFixed(6)) : null,
  };
};
