import { describe, expect, it } from "vitest";
import type { PaperDetail } from "../../src/domain/models.js";
import { scoreEdge, scoringInternals } from "../../src/domain/scoring.js";

const paper = (overrides: Partial<PaperDetail> = {}): PaperDetail => ({
  id: "W1",
  title: "Explainable Artificial Intelligence",
  publicationYear: 2020,
  publicationDate: "2020-01-01",
  authors: ["A. Author"],
  authorsTruncated: false,
  doi: "https://doi.org/10.1000/test",
  sourceUrl: "https://openalex.org/W1",
  citedByCount: 10,
  primaryTopic: { id: "T1", displayName: "Explainable AI", score: 0.9 },
  keywords: [{ displayName: "interpretability", score: 0.8 }],
  isRetracted: false,
  versionGroupKey: "doi:test",
  abstract: null,
  topics: [{ id: "T1", displayName: "Explainable AI", score: 0.9 }],
  referencedWorkIds: [],
  relatedWorkIds: [],
  ...overrides,
});

describe("scoreEdge", () => {
  it("실제 피인용 관계를 추론이 아닌 근거로 표시한다", () => {
    const from = paper();
    const to = paper({ id: "W2", title: "Applied XAI", publicationYear: 2022, referencedWorkIds: ["W1"] });
    const edge = scoreEdge(from, to, "explainable AI");
    expect(edge.relationship).toBe("cited_by");
    expect(edge.inferred).toBe(false);
    expect(edge.score).toBeGreaterThanOrEqual(0.4);
    expect(edge.evidence.some((item) => item.kind === "citation")).toBe(true);
  });

  it("관련 논문 관계는 추론으로 표시한다", () => {
    const from = paper({ relatedWorkIds: ["W2"] });
    const to = paper({ id: "W2", title: "Interpretable Models" });
    const edge = scoreEdge(from, to);
    expect(edge.relationship).toBe("related");
    expect(edge.inferred).toBe(true);
  });

  it("빈 용어 집합의 유사도는 0이다", () => {
    expect(scoringInternals.weightedJaccard([], [])).toBe(0);
  });
});
