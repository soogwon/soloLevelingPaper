import { describe, expect, it } from "vitest";
import type { PaperDetail, RequestUsage, SearchResult } from "../../src/domain/models.js";
import type { ProviderSearchInput, ScholarlyProvider } from "../../src/providers/scholarly-provider.js";
import { PaperService, paperServiceInternals } from "../../src/services/paper-service.js";

const paper = (id: string): PaperDetail => ({
  id, title: "Same Title", publicationYear: 2020, publicationDate: null,
  authors: [id], authorsTruncated: false, doi: null, sourceUrl: `https://openalex.org/${id}`,
  citedByCount: 0, primaryTopic: null, keywords: [], isRetracted: false, versionGroupKey: id,
  abstract: null, topics: [], referencedWorkIds: [], relatedWorkIds: [],
});

class DuplicateTitleProvider implements ScholarlyProvider {
  public searchCount = 0;
  public getWorkCount = 0;
  public async searchWorks(_input: ProviderSearchInput): Promise<SearchResult> {
    this.searchCount += 1;
    return { papers: [paper("W1"), paper("W2")] };
  }
  public async getWork(): Promise<PaperDetail | null> {
    this.getWorkCount += 1;
    return null;
  }
  public async getWorksByIds(): Promise<PaperDetail[]> { return []; }
  public async getCitingWorks(): Promise<PaperDetail[]> { return []; }
  public getUsage(): RequestUsage {
    return { requestCount: 0, cacheHitCount: 0, creditsUsed: 0, rateLimitRemaining: null, creditEstimateDelta: 0, estimatedCostUsd: 0 };
  }
  public resetUsage(): void {}
}

describe("PaperService", () => {
  it("정규화된 제목이 같은 논문이 여러 편이면 모호함으로 반환한다", async () => {
    const result = await new PaperService(new DuplicateTitleProvider()).resolve("Same Title");
    expect(result.status).toBe("ambiguous");
    expect(result.candidates).toHaveLength(2);
  });

  it("제목 중간의 W숫자를 OpenAlex ID로 오인하지 않는다", async () => {
    const provider = new DuplicateTitleProvider();
    await new PaperService(provider).resolve("Understanding W3 Models");
    expect(provider.searchCount).toBe(1);
    expect(provider.getWorkCount).toBe(0);
  });

  it("완전한 OpenAlex ID와 URL만 안정 식별자로 처리한다", () => {
    expect(paperServiceInternals.looksLikeStableIdentifier("W123")).toBe(true);
    expect(paperServiceInternals.looksLikeStableIdentifier("https://openalex.org/W123/")).toBe(true);
    expect(paperServiceInternals.looksLikeStableIdentifier("prefix W123 suffix")).toBe(false);
  });
});
