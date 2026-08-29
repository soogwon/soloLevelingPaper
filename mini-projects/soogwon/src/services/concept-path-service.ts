import type { AppConfig } from "../config.js";
import type { PaperDetail, TraceConceptPathInput, TraceConceptPathOutput, Warning } from "../domain/models.js";
import { toPaperSummary } from "../domain/models.js";
import { findBestPath, type Graph } from "../domain/path-finder.js";
import { SCORING_WEIGHTS, scoreEdge } from "../domain/scoring.js";
import { AppError } from "../infrastructure/errors.js";
import type { ScholarlyProvider } from "../providers/scholarly-provider.js";
import { PaperService } from "./paper-service.js";

const MAX_NODES = 50;
const MAX_REQUESTS = 20;
const WORK_BUDGET_MS = 8_000;
const SCHEMA_VERSION = "1.0.0";

export class ConceptPathService {
  readonly #paperService: PaperService;

  public constructor(
    private readonly provider: ScholarlyProvider,
    private readonly config: AppConfig,
  ) {
    this.#paperService = new PaperService(provider);
  }

  public async trace(input: TraceConceptPathInput, signal?: AbortSignal): Promise<TraceConceptPathOutput> {
    this.provider.resetUsage();
    const deadlineSignal = signal
      ? AbortSignal.any([signal, AbortSignal.timeout(WORK_BUDGET_MS)])
      : AbortSignal.timeout(WORK_BUDGET_MS);
    const startedAt = Date.now();
    const warnings: Warning[] = [];
    let truncated = false;
    const resolved = await this.#paperService.resolve(input.seed, deadlineSignal);
    if (resolved.status === "ambiguous") {
      throw new AppError("AMBIGUOUS_PAPER", "시작 논문이 모호합니다. DOI 또는 OpenAlex ID를 사용하세요.", {
        details: { candidates: resolved.candidates },
      });
    }
    if (resolved.status === "not_found" || !resolved.paper) {
      throw new AppError("PAPER_NOT_FOUND", "시작 논문을 찾을 수 없습니다.");
    }
    const seed = resolved.paper;
    warnings.push(...resolved.warnings);

    const graph: Graph = { papers: new Map([[seed.id, seed]]), edges: new Map() };
    let frontier: PaperDetail[] = [seed];

    try {
      for (let depth = 0; depth < input.maxDepth; depth += 1) {
        if (this.#limitReached(startedAt, graph)) {
          truncated = true;
          break;
        }
        const next: PaperDetail[] = [];
        for (const current of frontier) {
          if (this.#limitReached(startedAt, graph)) {
            truncated = true;
            break;
          }
          const candidates = await this.#neighbors(current, input, depth === 0, deadlineSignal);
          const scored = candidates
            .filter((paper) => paper.id !== current.id && !paper.isRetracted)
            .map((paper) => ({ paper, edge: scoreEdge(current, paper, input.targetQuery) }))
            .filter(({ edge }) => edge.score >= 0.25)
            .sort((a, b) => b.edge.score - a.edge.score || a.paper.id.localeCompare(b.paper.id))
            .slice(0, input.candidatesPerNode);
          graph.edges.set(current.id, scored.map(({ edge }) => edge));
          for (const { paper } of scored) {
            if (graph.papers.size >= MAX_NODES) {
              truncated = true;
              break;
            }
            if (!graph.papers.has(paper.id)) {
              graph.papers.set(paper.id, paper);
              next.push(paper);
            }
          }
        }
        frontier = next;
        if (frontier.length === 0 || truncated) break;
      }
    } catch (error) {
      if (error instanceof AppError && ["BUDGET_EXCEEDED", "PROVIDER_TIMEOUT", "PROVIDER_RATE_LIMIT"].includes(error.code)) {
        truncated = true;
        warnings.push({ code: error.code, message: error.message });
      } else {
        throw error;
      }
    }

    const path = findBestPath(graph, seed.id, input.maxPathLength, input.targetQuery);
    if (!path) warnings.push({ code: "PATH_NOT_FOUND", message: "현재 제한과 근거 기준에서 유효한 논문 경로를 찾지 못했습니다." });
    if (path?.nodes.length === 2) {
      warnings.push({ code: "SHORT_PARTIAL_PATH", message: "근거 기준을 충족한 3편 이상의 경로를 찾지 못해 2편의 부분 경로를 반환합니다." });
    }
    if (path && path.scoreMargin !== null && path.scoreMargin < 0.05) {
      warnings.push({
        code: "SIMILAR_ALTERNATIVE_PATH",
        message: "점수가 비슷한 대안 경로가 있어 이 결과를 유일한 경로로 해석하면 안 됩니다.",
        details: { scoreMargin: path.scoreMargin },
      });
    }
    if (truncated) warnings.push({ code: "SEARCH_TRUNCATED", message: "시간·노드·요청 또는 비용 제한으로 탐색이 조기에 종료됐습니다." });
    warnings.push({
      code: "BOUNDED_SEARCH",
      message: "이 경로는 제한된 후보 탐색의 결과이며 전체 학술 문헌의 전역 최적 경로를 의미하지 않습니다.",
    });

    const usage = this.provider.getUsage();
    const edgeCount = [...graph.edges.values()].reduce((total, edges) => total + edges.length, 0);
    return {
      seed: toPaperSummary(seed),
      ...(input.targetQuery ? { targetQuery: input.targetQuery } : {}),
      path,
      explored: {
        nodeCount: graph.papers.size,
        edgeCount,
        requestCount: usage.requestCount,
        creditsUsed: usage.creditsUsed,
        estimatedCostUsd: usage.estimatedCostUsd,
        truncated,
      },
      warnings: warnings.slice(0, 20),
      methodology: {
        provider: "openalex",
        retrievedAt: new Date().toISOString(),
        scoringVersion: this.config.scoringVersion,
        schemaVersion: SCHEMA_VERSION,
        weights: { ...SCORING_WEIGHTS },
        limits: {
          maxDepth: input.maxDepth,
          maxPathLength: input.maxPathLength,
          candidatesPerNode: input.candidatesPerNode,
          maxNodes: MAX_NODES,
          maxRequests: MAX_REQUESTS,
          maxCredits: this.config.maxCreditsPerTool,
          maxEstimatedCostUsd: this.config.maxEstimatedCostUsd,
        },
        queryParameters: {
          direction: input.direction,
          maxDepth: input.maxDepth,
          maxPathLength: input.maxPathLength,
          candidatesPerNode: input.candidatesPerNode,
          hasTargetQuery: Boolean(input.targetQuery),
        },
        limitations: [
          "단계별 가지치기를 사용하므로 전체 그래프의 전역 최적 경로를 보장하지 않습니다.",
          "인용 관계는 논문의 동의·확장·반박을 자동으로 증명하지 않습니다.",
          "OpenAlex 데이터와 관련 논문 순위는 시간이 지나며 변경될 수 있습니다.",
        ],
      },
    };
  }

  #limitReached(startedAt: number, graph: Graph): boolean {
    return Date.now() - startedAt >= WORK_BUDGET_MS
      || graph.papers.size >= MAX_NODES
      || this.provider.getUsage().requestCount >= MAX_REQUESTS;
  }

  async #neighbors(
    paper: PaperDetail,
    input: TraceConceptPathInput,
    includeTargetSearch: boolean,
    signal?: AbortSignal,
  ): Promise<PaperDetail[]> {
    const candidates: PaperDetail[] = [];
    if (input.direction === "backward" || input.direction === "both") {
      candidates.push(...await this.provider.getWorksByIds(
        [...paper.referencedWorkIds, ...paper.relatedWorkIds].slice(0, 100),
        signal,
      ));
    } else if (paper.relatedWorkIds.length > 0) {
      candidates.push(...await this.provider.getWorksByIds(paper.relatedWorkIds.slice(0, 100), signal));
    }
    if (input.direction === "forward" || input.direction === "both") {
      candidates.push(...await this.provider.getCitingWorks(paper.id, input.candidatesPerNode * 2, signal));
    }
    if (includeTargetSearch && input.targetQuery) {
      const target = await this.provider.searchWorks({
        query: input.targetQuery,
        limit: input.candidatesPerNode,
        semantic: false,
      }, signal);
      candidates.push(...target.papers);
    }
    const unique = new Map<string, PaperDetail>();
    for (const candidate of candidates) unique.set(candidate.id, candidate);
    return [...unique.values()];
  }
}
