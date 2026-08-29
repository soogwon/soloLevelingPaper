import { createHash } from "node:crypto";
import { AsyncLocalStorage } from "node:async_hooks";
import { z } from "zod/v4";
import type { AppConfig } from "../config.js";
import type { PaperDetail, RequestUsage, SearchResult, Topic, WeightedTerm } from "../domain/models.js";
import { AppError } from "../infrastructure/errors.js";
import { TtlLruCache } from "../infrastructure/cache.js";
import type { ProviderSearchInput, ScholarlyProvider } from "./scholarly-provider.js";

const weightedTermSchema = z.object({
  id: z.string().optional(),
  display_name: z.string(),
  score: z.number().nullish(),
}).passthrough();

const topicSchema = weightedTermSchema.extend({
  subfield: z.object({ display_name: z.string() }).nullish(),
  field: z.object({ display_name: z.string() }).nullish(),
  domain: z.object({ display_name: z.string() }).nullish(),
});

const workSchema = z.object({
  id: z.string(),
  title: z.string().nullish(),
  display_name: z.string().nullish(),
  doi: z.string().nullish(),
  publication_year: z.number().int().nullish(),
  publication_date: z.string().nullish(),
  cited_by_count: z.number().int().nullish(),
  is_retracted: z.boolean().nullish(),
  authorships: z.array(z.object({
    author: z.object({ display_name: z.string().nullish() }).nullish(),
  }).passthrough()).nullish(),
  primary_topic: topicSchema.nullish(),
  topics: z.array(topicSchema).nullish(),
  keywords: z.array(weightedTermSchema).nullish(),
  referenced_works: z.array(z.string()).nullish(),
  related_works: z.array(z.string()).nullish(),
  abstract_inverted_index: z.record(z.string(), z.array(z.number().int().nonnegative())).nullish(),
}).passthrough();

const listResponseSchema = z.object({
  meta: z.object({ count: z.number().int().nonnegative().optional() }).passthrough().optional(),
  results: z.array(workSchema),
}).passthrough();

const SCHEMA_VERSION = "1.0.0";
const SEARCH_CREDITS = 10;
const FILTER_CREDITS = 1;
const MAX_REQUESTS_PER_TOOL = 20;
const NEGATIVE_CACHE_TTL_MS = 5 * 60 * 1_000;
const NOT_FOUND_MARKER = { kind: "paper_not_found" } as const;

const normalizeOpenAlexId = (value: string): string => {
  const trimmed = value.trim();
  const match = trimmed.match(/(?:https?:\/\/(?:api\.)?openalex\.org\/)?(W\d+)/i);
  return match?.[1]?.toUpperCase() ?? trimmed;
};

const normalizeDoi = (value: string): string | null => {
  const normalized = value.trim().replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "").replace(/^doi:\s*/i, "");
  return /^10\.\d{4,9}\/[\S]+$/i.test(normalized) ? `https://doi.org/${normalized.toLowerCase()}` : null;
};

const normalizeTitle = (value: string): string => value
  .normalize("NFKC")
  .toLowerCase()
  .replace(/[^\p{L}\p{N}\s]/gu, " ")
  .replace(/\s+/g, " ")
  .trim();

const toVersionGroupKey = (work: z.infer<typeof workSchema>, authors: string[]): string => {
  const doi = work.doi ? normalizeDoi(work.doi) : null;
  if (doi) return doi;
  const material = `${normalizeTitle(work.title ?? work.display_name ?? "")}|${authors[0] ?? ""}|${work.publication_year ?? ""}`;
  return createHash("sha256").update(material).digest("hex").slice(0, 24);
};

const restoreAbstract = (index: Record<string, number[]> | null | undefined): string | null => {
  if (!index) return null;
  const positions: Array<[number, string]> = [];
  for (const [term, indexes] of Object.entries(index)) {
    for (const position of indexes) positions.push([position, term]);
  }
  if (positions.length === 0) return null;
  return positions.sort((a, b) => a[0] - b[0]).map((entry) => entry[1]).join(" ");
};

const toWeightedTerm = (term: z.infer<typeof weightedTermSchema>): WeightedTerm => ({
  ...(term.id ? { id: term.id } : {}),
  displayName: term.display_name,
  score: term.score ?? 0,
});

const toTopic = (topic: z.infer<typeof topicSchema>): Topic => ({
  ...toWeightedTerm(topic),
  ...(topic.subfield?.display_name ? { subfield: topic.subfield.display_name } : {}),
  ...(topic.field?.display_name ? { field: topic.field.display_name } : {}),
  ...(topic.domain?.display_name ? { domain: topic.domain.display_name } : {}),
});

export const normalizeOpenAlexWork = (input: unknown): PaperDetail => {
  const work = workSchema.parse(input);
  const title = (work.title ?? work.display_name ?? "").trim();
  if (!title) throw new AppError("PROVIDER_RESPONSE_INVALID", "OpenAlex 응답에 논문 제목이 없습니다.");
  const allAuthors = (work.authorships ?? [])
    .map((item) => item.author?.display_name?.trim())
    .filter((name): name is string => Boolean(name));
  const authors = allAuthors.slice(0, 10);
  const topics = (work.topics ?? []).slice(0, 3).map(toTopic);
  const keywords = (work.keywords ?? []).slice(0, 10).map(toWeightedTerm);
  const id = normalizeOpenAlexId(work.id);

  return {
    id,
    title,
    publicationYear: work.publication_year ?? null,
    publicationDate: work.publication_date ?? null,
    authors,
    authorsTruncated: allAuthors.length > authors.length,
    doi: work.doi ? normalizeDoi(work.doi) : null,
    sourceUrl: `https://openalex.org/${id}`,
    citedByCount: work.cited_by_count ?? 0,
    primaryTopic: work.primary_topic ? toTopic(work.primary_topic) : null,
    keywords,
    isRetracted: work.is_retracted ?? false,
    versionGroupKey: toVersionGroupKey(work, authors),
    abstract: restoreAbstract(work.abstract_inverted_index),
    topics,
    referencedWorkIds: (work.referenced_works ?? []).map(normalizeOpenAlexId),
    relatedWorkIds: (work.related_works ?? []).map(normalizeOpenAlexId),
  };
};

type FetchKind = "singleton" | "filter" | "search";

export class OpenAlexProvider implements ScholarlyProvider {
  readonly #cache: TtlLruCache<unknown>;
  readonly #usageStorage = new AsyncLocalStorage<RequestUsage>();
  #fallbackUsage: RequestUsage = { requestCount: 0, creditsUsed: 0, estimatedCostUsd: 0 };

  public constructor(
    private readonly config: AppConfig,
    private readonly fetchImpl: typeof fetch = fetch,
  ) {
    this.#cache = new TtlLruCache(config.cacheMaxEntries, config.cacheTtlSeconds * 1_000);
  }

  public resetUsage(): void {
    const usage = { requestCount: 0, creditsUsed: 0, estimatedCostUsd: 0 };
    this.#fallbackUsage = usage;
    this.#usageStorage.enterWith(usage);
  }

  public getUsage(): RequestUsage {
    return { ...(this.#usageStorage.getStore() ?? this.#fallbackUsage) };
  }

  public async searchWorks(input: ProviderSearchInput, signal?: AbortSignal): Promise<SearchResult> {
    const url = new URL(`${this.config.openAlexBaseUrl}/works`);
    url.searchParams.set(input.semantic ? "search.semantic" : "search", input.query);
    url.searchParams.set("per_page", String(input.limit));
    url.searchParams.set("sort", "relevance_score:desc");
    const filters: string[] = ["is_retracted:false"];
    if (input.fromYear !== undefined) filters.push(`from_publication_date:${input.fromYear}-01-01`);
    if (input.toYear !== undefined) filters.push(`to_publication_date:${input.toYear}-12-31`);
    url.searchParams.set("filter", filters.join(","));
    const data = listResponseSchema.parse(await this.#request(url, "search", signal));
    return {
      papers: data.results.map(normalizeOpenAlexWork),
      ...(data.meta?.count !== undefined ? { totalCandidates: data.meta.count } : {}),
    };
  }

  public async getWork(identifier: string, signal?: AbortSignal): Promise<PaperDetail | null> {
    const normalized = normalizeDoi(identifier) ?? normalizeOpenAlexId(identifier);
    const url = new URL(`${this.config.openAlexBaseUrl}/works/${encodeURIComponent(normalized)}`);
    try {
      return normalizeOpenAlexWork(await this.#request(url, "singleton", signal));
    } catch (error) {
      if (error instanceof AppError && error.code === "PAPER_NOT_FOUND") return null;
      throw error;
    }
  }

  public async getWorksByIds(ids: string[], signal?: AbortSignal): Promise<PaperDetail[]> {
    const uniqueIds = [...new Set(ids.map(normalizeOpenAlexId).filter((id) => /^W\d+$/.test(id)))].slice(0, 100);
    if (uniqueIds.length === 0) return [];
    const url = new URL(`${this.config.openAlexBaseUrl}/works`);
    url.searchParams.set("filter", `openalex:${uniqueIds.join("|")}`);
    url.searchParams.set("per_page", String(uniqueIds.length));
    const data = listResponseSchema.parse(await this.#request(url, "filter", signal));
    return data.results.map(normalizeOpenAlexWork);
  }

  public async getCitingWorks(id: string, limit: number, signal?: AbortSignal): Promise<PaperDetail[]> {
    const url = new URL(`${this.config.openAlexBaseUrl}/works`);
    url.searchParams.set("filter", `cites:${normalizeOpenAlexId(id)},is_retracted:false`);
    url.searchParams.set("sort", "publication_date:desc");
    url.searchParams.set("per_page", String(Math.min(100, Math.max(1, limit))));
    const data = listResponseSchema.parse(await this.#request(url, "filter", signal));
    return data.results.map(normalizeOpenAlexWork);
  }

  async #request(url: URL, kind: FetchKind, parentSignal?: AbortSignal): Promise<unknown> {
    const cacheKey = `${SCHEMA_VERSION}:${this.config.scoringVersion}:${url.toString()}`;
    const cached = this.#cache.get(cacheKey);
    if (cached !== undefined) {
      if (cached === NOT_FOUND_MARKER) throw new AppError("PAPER_NOT_FOUND", "논문을 찾을 수 없습니다.");
      return cached;
    }

    const credits = kind === "singleton" ? 0 : kind === "search" ? SEARCH_CREDITS : FILTER_CREDITS;
    const estimatedCost = kind === "singleton" ? 0 : kind === "search" ? 0.001 : 0.0001;
    const usage = this.#usageStorage.getStore() ?? this.#fallbackUsage;

    let lastError: unknown;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      if (
        usage.requestCount + 1 > MAX_REQUESTS_PER_TOOL
        || usage.creditsUsed + credits > this.config.maxCreditsPerTool
        || usage.estimatedCostUsd + estimatedCost > this.config.maxEstimatedCostUsd
      ) {
        throw new AppError("BUDGET_EXCEEDED", "OpenAlex 도구 호출 예산을 초과해 탐색을 중단했습니다.");
      }
      const timeout = AbortSignal.timeout(this.config.requestTimeoutMs);
      const signal = parentSignal ? AbortSignal.any([parentSignal, timeout]) : timeout;
      usage.requestCount += 1;
      usage.creditsUsed += credits;
      usage.estimatedCostUsd = Number((usage.estimatedCostUsd + estimatedCost).toFixed(6));
      try {
        const response = await this.fetchImpl(url, {
          signal,
          headers: {
            Accept: "application/json",
            "User-Agent": "paper-concept-path-mcp/0.1.0",
            ...(this.config.openAlexApiKey ? { Authorization: `Bearer ${this.config.openAlexApiKey}` } : {}),
          },
        });
        const actualCredits = Number(response.headers.get("X-RateLimit-Credits-Used"));
        if (Number.isFinite(actualCredits) && actualCredits >= 0) {
          usage.creditsUsed += actualCredits - credits;
        }
        if (response.status === 404) {
          this.#cache.set(cacheKey, NOT_FOUND_MARKER, NEGATIVE_CACHE_TTL_MS);
          throw new AppError("PAPER_NOT_FOUND", "논문을 찾을 수 없습니다.");
        }
        if (response.status === 401 || response.status === 403) {
          throw new AppError("PROVIDER_AUTH", "OpenAlex 인증에 실패했습니다.");
        }
        if (response.status === 429) {
          const retryAfter = Number(response.headers.get("Retry-After"));
          if (attempt < 2) {
            await this.#delay(Number.isFinite(retryAfter) ? retryAfter * 1_000 : 250 * 2 ** attempt, parentSignal);
            continue;
          }
          throw new AppError("PROVIDER_RATE_LIMIT", "OpenAlex 요청 한도를 초과했습니다.", { retryable: true });
        }
        if (response.status >= 500 && attempt < 2) {
          await this.#delay(250 * 2 ** attempt, parentSignal);
          continue;
        }
        if (!response.ok) {
          throw new AppError("PROVIDER_RESPONSE_INVALID", `OpenAlex가 HTTP ${response.status}를 반환했습니다.`);
        }
        const value: unknown = await response.json();
        this.#cache.set(cacheKey, value);
        return value;
      } catch (error) {
        lastError = error;
        if (error instanceof AppError) throw error;
        if (signal.aborted) {
          throw new AppError("PROVIDER_TIMEOUT", "OpenAlex 요청 시간이 초과되거나 취소됐습니다.", {
            cause: error,
            retryable: true,
          });
        }
        if (attempt < 2) {
          await this.#delay(250 * 2 ** attempt, parentSignal);
          continue;
        }
      }
    }
    throw new AppError("PROVIDER_RESPONSE_INVALID", "OpenAlex 응답을 처리할 수 없습니다.", {
      cause: lastError,
      retryable: true,
    });
  }

  async #delay(milliseconds: number, signal?: AbortSignal): Promise<void> {
    await new Promise<void>((resolve, reject) => {
      const onAbort = () => {
        clearTimeout(timer);
        reject(signal?.reason);
      };
      const timer = setTimeout(() => {
        signal?.removeEventListener("abort", onAbort);
        resolve();
      }, Math.min(milliseconds, 5_000));
      signal?.addEventListener("abort", onAbort, { once: true });
    });
  }
}

export const openAlexIdentifier = {
  normalizeId: normalizeOpenAlexId,
  normalizeDoi,
  normalizeTitle,
};
