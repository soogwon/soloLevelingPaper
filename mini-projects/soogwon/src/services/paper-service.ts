import type { PaperDetail, ResolvePaperOutput, SearchPapersInput, SearchPapersOutput, Warning } from "../domain/models.js";
import { toPaperSummary } from "../domain/models.js";
import { openAlexIdentifier } from "../providers/openalex-provider.js";
import type { ScholarlyProvider } from "../providers/scholarly-provider.js";

const titleTokens = (value: string): Set<string> => new Set(
  openAlexIdentifier.normalizeTitle(value).split(" ").filter(Boolean),
);

const titleSimilarity = (left: string, right: string): number => {
  const a = titleTokens(left);
  const b = titleTokens(right);
  const union = new Set([...a, ...b]);
  if (union.size === 0) return 0;
  const intersection = [...a].filter((token) => b.has(token)).length;
  return intersection / union.size;
};

const looksLikeStableIdentifier = (value: string): boolean => (
  /^(?:https?:\/\/(?:api\.)?openalex\.org\/)?W\d+\/?$/i.test(value.trim())
  || /^10\.\d{4,9}\/[\S]+$/i.test(value.trim().replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, ""))
);

export class PaperService {
  public constructor(private readonly provider: ScholarlyProvider) {}

  public async search(input: SearchPapersInput, signal?: AbortSignal, deadlineAt?: number): Promise<SearchPapersOutput> {
    const providerQuery = input.queryEn?.trim() || input.query.trim();
    const result = await this.provider.searchWorks({
      query: providerQuery,
      ...(input.fromYear !== undefined ? { fromYear: input.fromYear } : {}),
      ...(input.toYear !== undefined ? { toYear: input.toYear } : {}),
      limit: input.limit,
      semantic: input.semantic,
    }, signal, deadlineAt);
    const warnings: Warning[] = [];
    if (input.queryEn) {
      warnings.push({ code: "QUERY_EN_USED", message: "OpenAlex 검색에는 사용자가 제공한 영어 검색어를 사용했습니다." });
    }
    return {
      query: input.query,
      papers: result.papers.filter((paper) => !paper.isRetracted).map(toPaperSummary),
      ...(result.totalCandidates !== undefined ? { totalCandidates: result.totalCandidates } : {}),
      warnings,
    };
  }

  public async resolve(identifier: string, signal?: AbortSignal, deadlineAt?: number): Promise<ResolvePaperOutput> {
    if (looksLikeStableIdentifier(identifier)) {
      const paper = await this.provider.getWork(identifier, signal, deadlineAt);
      return paper
        ? { status: "exact", paper, warnings: this.#paperWarnings(paper) }
        : { status: "not_found", warnings: [{ code: "PAPER_NOT_FOUND", message: "논문을 찾을 수 없습니다." }] };
    }

    const result = await this.provider.searchWorks({ query: identifier, limit: 5, semantic: false }, signal, deadlineAt);
    const ranked = result.papers
      .map((paper) => ({ paper, similarity: titleSimilarity(identifier, paper.title) }))
      .sort((a, b) => b.similarity - a.similarity || a.paper.id.localeCompare(b.paper.id));
    const first = ranked[0];
    if (!first) return { status: "not_found", warnings: [{ code: "PAPER_NOT_FOUND", message: "논문을 찾을 수 없습니다." }] };
    const second = ranked[1];
    const normalizedIdentifier = openAlexIdentifier.normalizeTitle(identifier);
    const exactMatches = ranked.filter(({ paper }) => openAlexIdentifier.normalizeTitle(paper.title) === normalizedIdentifier);
    const exactNormalized = exactMatches.length === 1;
    const sufficientlySeparated = first.similarity >= 0.95 && (!second || first.similarity - second.similarity >= 0.1);
    if (exactNormalized || sufficientlySeparated) {
      return { status: "exact", paper: first.paper, warnings: this.#paperWarnings(first.paper) };
    }
    return {
      status: "ambiguous",
      candidates: ranked.slice(0, 5).map(({ paper }) => toPaperSummary(paper)),
      warnings: [{ code: "AMBIGUOUS_PAPER", message: "제목만으로 논문을 확정할 수 없습니다. DOI 또는 OpenAlex ID를 사용하세요." }],
    };
  }

  #paperWarnings(paper: PaperDetail): Warning[] {
    const warnings: Warning[] = [];
    if (paper.isRetracted) warnings.push({ code: "RETRACTED", message: "이 논문은 철회된 것으로 표시되어 있습니다." });
    if (!paper.abstract) warnings.push({ code: "ABSTRACT_MISSING", message: "OpenAlex에 사용할 수 있는 초록이 없습니다." });
    if (paper.authorsTruncated) warnings.push({ code: "AUTHORS_TRUNCATED", message: "저자 목록은 처음 10명만 표시합니다." });
    return warnings;
  }
}

export const paperServiceInternals = { titleSimilarity, looksLikeStableIdentifier };
