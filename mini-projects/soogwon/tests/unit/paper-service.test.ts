import { describe, expect, it } from "vitest";
import type { PaperDetail, RequestUsage, SearchResult } from "../../src/domain/models.js";
import { AppError, type ErrorCode } from "../../src/infrastructure/errors.js";
import type { ProviderSearchInput, ScholarlyProvider } from "../../src/providers/scholarly-provider.js";
import { PaperService, paperServiceInternals } from "../../src/services/paper-service.js";

const paper = (id: string, doi: string | null = null): PaperDetail => ({
  id, title: "Same Title", publicationYear: 2020, publicationDate: null,
  authors: [id], authorsTruncated: false, doi, sourceUrl: `https://openalex.org/${id}`,
  citedByCount: 0, primaryTopic: null, keywords: [], isRetracted: false, versionGroupKey: id,
  abstract: null, topics: [], referencedWorkIds: [], relatedWorkIds: [],
});

class DuplicateTitleProvider implements ScholarlyProvider {
  public searchCount = 0;
  public getWorkCount = 0;
  public locationCount = 0;
  public workResult: PaperDetail | null = null;
  public workError: AppError | null = null;
  public locationResults: PaperDetail[] = [];
  public async searchWorks(_input: ProviderSearchInput): Promise<SearchResult> {
    this.searchCount += 1;
    return { papers: [paper("W1"), paper("W2")] };
  }
  public async getWork(): Promise<PaperDetail | null> {
    this.getWorkCount += 1;
    if (this.workError) throw this.workError;
    return this.workResult;
  }
  public async findWorksByLocationDoi(): Promise<PaperDetail[]> {
    this.locationCount += 1;
    return this.locationResults;
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

  it("불완전한 DOI를 제목 검색으로 넘기지 않고 거부한다", async () => {
    const provider = new DuplicateTitleProvider();
    await expect(new PaperService(provider).resolve("10.65215")).rejects.toMatchObject({
      code: "INVALID_INPUT",
      retryable: false,
      details: { expectedFormat: "10.xxxx/suffix" },
    });
    expect(provider.searchCount).toBe(0);
    expect(provider.getWorkCount).toBe(0);
    expect(provider.locationCount).toBe(0);
  });

  it("직접 DOI 조회가 성공하면 위치 fallback을 호출하지 않는다", async () => {
    const provider = new DuplicateTitleProvider();
    provider.workResult = paper("W10", "https://doi.org/10.65215/2q58a426");
    const result = await new PaperService(provider).resolve("10.65215/2Q58A426");
    expect(result).toMatchObject({
      status: "exact",
      resolution: {
        normalizedIdentifier: "https://doi.org/10.65215/2q58a426",
        matchedVia: "primary_doi",
      },
    });
    expect(provider.locationCount).toBe(0);
  });

  it("직접 조회 404 후 위치 DOI 한 건을 확정하고 충돌을 경고한다", async () => {
    const provider = new DuplicateTitleProvider();
    provider.locationResults = [paper("W2626778328", "https://doi.org/10.65215/2q58a426")];
    const result = await new PaperService(provider).resolve("10.48550/arXiv.1706.03762");
    expect(result).toMatchObject({
      status: "exact",
      paper: { id: "W2626778328" },
      resolution: {
        normalizedIdentifier: "https://doi.org/10.48550/arxiv.1706.03762",
        matchedVia: "location_doi",
        providerPrimaryDoi: "https://doi.org/10.65215/2q58a426",
      },
    });
    expect(result.warnings.map((warning) => warning.code)).toEqual(expect.arrayContaining([
      "DOI_RESOLVED_VIA_LOCATION",
      "IDENTIFIER_CONFLICT",
    ]));
    expect(provider.getWorkCount).toBe(1);
    expect(provider.locationCount).toBe(1);
  });

  it("위치 DOI가 여러 Work에 연결되면 첫 결과를 확정하지 않는다", async () => {
    const provider = new DuplicateTitleProvider();
    provider.locationResults = [paper("W1"), paper("W2")];
    const result = await new PaperService(provider).resolve("10.48550/arXiv.1706.03762");
    expect(result.status).toBe("ambiguous");
    expect(result.paper).toBeUndefined();
    expect(result.candidates).toHaveLength(2);
    expect(result.warnings).toContainEqual(expect.objectContaining({ code: "AMBIGUOUS_DOI_LOCATION" }));
  });

  it("위치 DOI 결과가 없으면 not_found를 반환한다", async () => {
    const provider = new DuplicateTitleProvider();
    const result = await new PaperService(provider).resolve("10.48550/arXiv.1706.03762");
    expect(result).toMatchObject({ status: "not_found", warnings: [{ code: "PAPER_NOT_FOUND" }] });
    expect(provider.getWorkCount).toBe(1);
    expect(provider.locationCount).toBe(1);
  });

  it.each<ErrorCode>([
    "PROVIDER_AUTH",
    "PROVIDER_RATE_LIMIT",
    "PROVIDER_TIMEOUT",
    "PROVIDER_RESPONSE_INVALID",
  ])("직접 DOI 조회가 %s이면 위치 fallback을 실행하지 않는다", async (code) => {
    const provider = new DuplicateTitleProvider();
    provider.workError = new AppError(code, "provider failure", { retryable: true });
    await expect(new PaperService(provider).resolve("10.1000/test")).rejects.toMatchObject({ code });
    expect(provider.locationCount).toBe(0);
  });

  it("OpenAlex 대표 DOI가 없으면 resolution에 null을 보존하고 충돌을 경고한다", async () => {
    const provider = new DuplicateTitleProvider();
    provider.workResult = paper("W10", null);
    const result = await new PaperService(provider).resolve("10.1000/test");
    expect(result).toMatchObject({
      status: "exact",
      resolution: { matchedVia: "primary_doi", providerPrimaryDoi: null },
    });
    expect(result.warnings).toContainEqual(expect.objectContaining({
      code: "IDENTIFIER_CONFLICT",
      details: { requestedDoi: "https://doi.org/10.1000/test", providerPrimaryDoi: null },
    }));
  });

  it("DOI URL과 불완전 DOI URL을 구분한다", () => {
    expect(paperServiceInternals.classifyIdentifier("https://doi.org/10.1000/ABC")).toEqual({
      kind: "doi",
      normalized: "https://doi.org/10.1000/abc",
    });
    expect(paperServiceInternals.classifyIdentifier("https://doi.org/10.65215")).toEqual({ kind: "incomplete_doi" });
    expect(paperServiceInternals.classifyIdentifier("doi: 10.1000/ABC")).toEqual({
      kind: "doi",
      normalized: "https://doi.org/10.1000/abc",
    });
    expect(paperServiceInternals.classifyIdentifier("http://dx.doi.org/10.1000/ABC")).toEqual({
      kind: "doi",
      normalized: "https://doi.org/10.1000/abc",
    });
    expect(paperServiceInternals.classifyIdentifier("Attention Is All You Need")).toEqual({ kind: "title" });
  });
});
