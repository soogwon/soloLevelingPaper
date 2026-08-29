import { describe, expect, it } from "vitest";
import type { PaperDetail, PathEdge } from "../../src/domain/models.js";
import { findBestPath, type Graph } from "../../src/domain/path-finder.js";

const paper = (id: string): PaperDetail => ({
  id,
  title: `Paper ${id}`,
  publicationYear: 2020,
  publicationDate: null,
  authors: [],
  authorsTruncated: false,
  doi: null,
  sourceUrl: `https://openalex.org/${id}`,
  citedByCount: 0,
  primaryTopic: null,
  keywords: [],
  isRetracted: false,
  versionGroupKey: id,
  abstract: null,
  topics: [],
  referencedWorkIds: [],
  relatedWorkIds: [],
});

const edge = (fromId: string, toId: string, score: number): PathEdge => ({
  fromId,
  toId,
  relationship: "cited_by",
  evidence: [{ kind: "citation", value: `${fromId}-${toId}`, source: "openalex" }],
  score,
  inferred: false,
  rationale: "direct citation",
});

describe("findBestPath", () => {
  it("점수가 높은 결정론적 경로를 선택한다", () => {
    const graph: Graph = {
      papers: new Map(["W1", "W2", "W3", "W4", "W5"].map((id) => [id, paper(id)])),
      edges: new Map([
        ["W1", [edge("W1", "W2", 0.8), edge("W1", "W3", 0.5)]],
        ["W2", [edge("W2", "W4", 0.9)]],
        ["W3", [edge("W3", "W4", 0.5)]],
      ]),
    };
    const result = findBestPath(graph, "W1", 4);
    expect(result?.nodes.map((node) => node.paper.id)).toEqual(["W1", "W2", "W4"]);
    expect(result?.scoreMargin).not.toBeNull();
  });

  it("순환을 다시 방문하지 않는다", () => {
    const graph: Graph = {
      papers: new Map(["W1", "W2"].map((id) => [id, paper(id)])),
      edges: new Map([
        ["W1", [edge("W1", "W2", 0.8)]],
        ["W2", [edge("W2", "W1", 0.8)]],
      ]),
    };
    expect(findBestPath(graph, "W1", 5)?.nodes).toHaveLength(2);
  });

  it("최선 경로의 접두 경로를 대안 점수 차이에 사용하지 않는다", () => {
    const graph: Graph = {
      papers: new Map(["W1", "W2", "W3", "W4", "W5"].map((id) => [id, paper(id)])),
      edges: new Map([
        ["W1", [edge("W1", "W2", 0.8), edge("W1", "W4", 0.5)]],
        ["W2", [edge("W2", "W3", 0.9)]],
        ["W4", [edge("W4", "W5", 0.5)]],
      ]),
    };
    const result = findBestPath(graph, "W1", 4);
    expect(result?.nodes.map((node) => node.paper.id)).toEqual(["W1", "W2", "W3"]);
    expect(result?.scoreMargin).toBeGreaterThan(0.2);
  });

  it("3편 경로 우선 정책에서 더 높은 2편 경로 때문에 음수 margin을 만들지 않는다", () => {
    const graph: Graph = {
      papers: new Map(["W1", "W2", "W3", "W4", "W5"].map((id) => [id, paper(id)])),
      edges: new Map([
        ["W1", [edge("W1", "W4", 0.95), edge("W1", "W2", 0.7), edge("W1", "W5", 0.5)]],
        ["W2", [edge("W2", "W3", 0.7)]],
      ]),
    };
    const result = findBestPath(graph, "W1", 4);
    expect(result?.nodes.map((node) => node.paper.id)).toEqual(["W1", "W2", "W3"]);
    expect(result?.scoreMargin).toBeNull();
  });
});
