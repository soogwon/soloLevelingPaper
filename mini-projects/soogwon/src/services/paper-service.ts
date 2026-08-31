import type { PaperDetail, PaperResolution, ResolvePaperOutput, SearchPapersInput, SearchPapersOutput, Warning } from "../domain/models.js";
import { toPaperSummary } from "../domain/models.js";
import { AppError } from "../infrastructure/errors.js";
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

type IdentifierClassification =
  | { kind: "openalex"; normalized: string }
  | { kind: "doi"; normalized: string }
  | { kind: "incomplete_doi" }
  | { kind: "title" };

const DOI_PREFIX_PATTERN = /^10\.\d{4,9}(?:\/|\s|$)/i;
const DOI_URL_PREFIX_PATTERN = /^(?:doi:\s*|https?:\/\/(?:dx\.)?doi\.org\/)/i;
const OPENALEX_PATTERN = /^(?:https?:\/\/(?:api\.)?openalex\.org\/)?W\d+\/?$/i;

const classifyIdentifier = (value: string): IdentifierClassification => {
  const trimmed = value.trim();
  if (OPENALEX_PATTERN.test(trimmed)) {
    return { kind: "openalex", normalized: openAlexIdentifier.normalizeId(trimmed) };
  }
  const normalizedDoi = openAlexIdentifier.normalizeDoi(trimmed);
  if (normalizedDoi) return { kind: "doi", normalized: normalizedDoi };
  const withoutDoiPrefix = trimmed.replace(DOI_URL_PREFIX_PATTERN, "");
  if (DOI_URL_PREFIX_PATTERN.test(trimmed) || DOI_PREFIX_PATTERN.test(withoutDoiPrefix)) {
    return { kind: "incomplete_doi" };
  }
  return { kind: "title" };
};

const looksLikeStableIdentifier = (value: string): boolean => {
  const kind = classifyIdentifier(value).kind;
  return kind === "openalex" || kind === "doi";
};

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
    const requestedIdentifier = identifier.trim();
    const classification = classifyIdentifier(requestedIdentifier);
    if (classification.kind === "incomplete_doi") {
      throw new AppError("INVALID_INPUT", "DOI가 완전하지 않습니다. '10.xxxx/suffix' 형식으로 입력하세요.", {
        details: { expectedFormat: "10.xxxx/suffix" },
      });
    }
    if (classification.kind === "openalex") {
      const paper = await this.provider.getWork(classification.normalized, signal, deadlineAt);
      return paper
        ? this.#exactResult(paper, {
          requestedIdentifier,
          normalizedIdentifier: classification.normalized,
          matchedVia: "openalex_id",
          providerPrimaryDoi: paper.doi,
        })
        : { status: "not_found", warnings: [{ code: "PAPER_NOT_FOUND", message: "논문을 찾을 수 없습니다." }] };
    }
    if (classification.kind === "doi") {
      const paper = await this.provider.getWork(classification.normalized, signal, deadlineAt);
      if (paper) {
        return this.#exactResult(paper, this.#doiResolution(requestedIdentifier, classification.normalized, "primary_doi", paper));
      }
      const locationMatches = await this.provider.findWorksByLocationDoi(classification.normalized, signal, deadlineAt);
      if (locationMatches.length === 0) {
        return { status: "not_found", warnings: [{ code: "PAPER_NOT_FOUND", message: "논문을 찾을 수 없습니다." }] };
      }
      if (locationMatches.length > 1) {
        return {
          status: "ambiguous",
          candidates: locationMatches.slice(0, 2).map(toPaperSummary),
          warnings: [{
            code: "AMBIGUOUS_DOI_LOCATION",
            message: "동일한 위치 DOI가 여러 OpenAlex Work에 연결되어 논문을 확정할 수 없습니다.",
          }],
        };
      }
      const matched = locationMatches[0]!;
      const result = this.#exactResult(
        matched,
        this.#doiResolution(requestedIdentifier, classification.normalized, "location_doi", matched),
      );
      result.warnings.unshift({
        code: "DOI_RESOLVED_VIA_LOCATION",
        message: "입력 DOI가 OpenAlex의 보조 위치에서 확인됐습니다.",
      });
      return result;
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
      return this.#exactResult(first.paper, {
        requestedIdentifier,
        normalizedIdentifier: openAlexIdentifier.normalizeTitle(requestedIdentifier),
        matchedVia: "title",
        providerPrimaryDoi: first.paper.doi,
      });
    }
    return {
      status: "ambiguous",
      candidates: ranked.slice(0, 5).map(({ paper }) => toPaperSummary(paper)),
      warnings: [{ code: "AMBIGUOUS_PAPER", message: "제목만으로 논문을 확정할 수 없습니다. DOI 또는 OpenAlex ID를 사용하세요." }],
    };
  }

  #doiResolution(
    requestedIdentifier: string,
    normalizedIdentifier: string,
    matchedVia: "primary_doi" | "location_doi",
    paper: PaperDetail,
  ): PaperResolution {
    return { requestedIdentifier, normalizedIdentifier, matchedVia, providerPrimaryDoi: paper.doi };
  }

  #exactResult(paper: PaperDetail, resolution: PaperResolution): ResolvePaperOutput {
    const warnings = this.#paperWarnings(paper);
    if (
      (resolution.matchedVia === "primary_doi" || resolution.matchedVia === "location_doi")
      && resolution.normalizedIdentifier !== paper.doi
    ) {
      warnings.push({
        code: "IDENTIFIER_CONFLICT",
        message: "입력 DOI와 OpenAlex 대표 DOI가 다릅니다. 두 식별자를 원문에서 확인하세요.",
        details: {
          requestedDoi: resolution.normalizedIdentifier,
          providerPrimaryDoi: paper.doi,
        },
      });
    }
    return { status: "exact", paper, resolution, warnings };
  }

  #paperWarnings(paper: PaperDetail): Warning[] {
    const warnings: Warning[] = [];
    if (paper.isRetracted) warnings.push({ code: "RETRACTED", message: "이 논문은 철회된 것으로 표시되어 있습니다." });
    if (!paper.abstract) warnings.push({ code: "ABSTRACT_MISSING", message: "OpenAlex에 사용할 수 있는 초록이 없습니다." });
    if (paper.authorsTruncated) warnings.push({ code: "AUTHORS_TRUNCATED", message: "저자 목록은 처음 10명만 표시합니다." });
    return warnings;
  }
}

export const paperServiceInternals = { titleSimilarity, looksLikeStableIdentifier, classifyIdentifier };
