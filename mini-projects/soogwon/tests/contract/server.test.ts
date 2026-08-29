import { Client } from "@modelcontextprotocol/client";
import { InMemoryTransport } from "@modelcontextprotocol/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { AppConfig } from "../../src/config.js";
import type { PaperDetail, RequestUsage, SearchResult } from "../../src/domain/models.js";
import type { ProviderSearchInput, ScholarlyProvider } from "../../src/providers/scholarly-provider.js";
import { createServer } from "../../src/server.js";

const config: AppConfig = {
  openAlexBaseUrl: "https://api.openalex.org",
  requestTimeoutMs: 1_000,
  cacheTtlSeconds: 60,
  cacheMaxEntries: 10,
  logLevel: "silent",
  maxCreditsPerTool: 100,
  maxEstimatedCostUsd: 0.01,
  scoringVersion: "1.0.0",
};

const paper = (id: string, referencedWorkIds: string[] = []): PaperDetail => ({
  id,
  title: `Paper ${id}`,
  publicationYear: 2020 + Number(id.slice(1)),
  publicationDate: null,
  authors: ["Researcher"],
  authorsTruncated: false,
  doi: `https://doi.org/10.1000/${id.toLowerCase()}`,
  sourceUrl: `https://openalex.org/${id}`,
  citedByCount: 1,
  primaryTopic: { id: "T1", displayName: "AI", score: 0.9 },
  keywords: [{ displayName: "explainability", score: 0.8 }],
  isRetracted: false,
  versionGroupKey: id,
  abstract: null,
  topics: [{ id: "T1", displayName: "AI", score: 0.9 }],
  referencedWorkIds,
  relatedWorkIds: [],
});

class MockProvider implements ScholarlyProvider {
  #usage: RequestUsage = { requestCount: 0, creditsUsed: 0, estimatedCostUsd: 0 };
  readonly #papers = new Map([paper("W1"), paper("W2", ["W1"]), paper("W3", ["W2"])].map((item) => [item.id, item]));

  public async searchWorks(_input: ProviderSearchInput): Promise<SearchResult> {
    this.#usage.requestCount += 1;
    return { papers: [...this.#papers.values()], totalCandidates: 3 };
  }
  public async getWork(identifier: string): Promise<PaperDetail | null> {
    this.#usage.requestCount += 1;
    return this.#papers.get(identifier.match(/W\d+/)?.[0] ?? "") ?? null;
  }
  public async getWorksByIds(ids: string[]): Promise<PaperDetail[]> {
    this.#usage.requestCount += 1;
    return ids.map((id) => this.#papers.get(id)).filter((item): item is PaperDetail => Boolean(item));
  }
  public async getCitingWorks(id: string): Promise<PaperDetail[]> {
    this.#usage.requestCount += 1;
    return [...this.#papers.values()].filter((item) => item.referencedWorkIds.includes(id));
  }
  public getUsage(): RequestUsage { return { ...this.#usage }; }
  public resetUsage(): void { this.#usage = { requestCount: 0, creditsUsed: 0, estimatedCostUsd: 0 }; }
}

describe("MCP server contract", () => {
  let client: Client;
  let server: ReturnType<typeof createServer>;

  beforeEach(async () => {
    client = new Client({ name: "test-client", version: "1.0.0" });
    server = createServer({ config, provider: new MockProvider() });
    const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
    await server.connect(serverTransport);
    await client.connect(clientTransport);
  });

  afterEach(async () => {
    await client.close();
    await server.close();
  });

  it("세 MCP 도구를 공개한다", async () => {
    const result = await client.listTools();
    expect(result.tools.map((tool) => tool.name).sort()).toEqual([
      "resolve_paper",
      "search_papers",
      "trace_concept_path",
    ]);
  });

  it("논문 확인 도구가 structuredContent를 반환한다", async () => {
    const result = await client.callTool({ name: "resolve_paper", arguments: { identifier: "https://openalex.org/W1" } });
    expect(result.isError).not.toBe(true);
    expect(result.structuredContent).toMatchObject({
      status: "exact",
      paper: { id: "W1", publication_year: 2021, source_url: "https://openalex.org/W1" },
    });
  });

  it("잘못된 입력을 스키마에서 거부한다", async () => {
    const result = await client.callTool({ name: "search_papers", arguments: { query: "x" } });
    expect(result.isError).toBe(true);
    expect(result.content[0]).toMatchObject({ type: "text" });
  });
});
