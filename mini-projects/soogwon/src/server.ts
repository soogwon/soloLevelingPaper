import { randomUUID } from "node:crypto";
import { McpServer } from "@modelcontextprotocol/server";
import { z } from "zod/v4";
import type { AppConfig } from "./config.js";
import { loadConfig } from "./config.js";
import { toPublicError } from "./infrastructure/errors.js";
import { StructuredLogger, type AppLogger } from "./infrastructure/logger.js";
import { OpenAlexProvider } from "./providers/openalex-provider.js";
import type { ScholarlyProvider } from "./providers/scholarly-provider.js";
import { ConceptPathService } from "./services/concept-path-service.js";
import { PaperService } from "./services/paper-service.js";

const currentYear = new Date().getUTCFullYear();
const PAPER_TOOL_BUDGET_MS = 10_000;

const searchInputSchema = z.object({
  query: z.string().trim().min(3).max(300),
  query_en: z.string().trim().min(3).max(300).optional(),
  from_year: z.number().int().min(1800).max(currentYear).optional(),
  to_year: z.number().int().min(1800).max(currentYear).optional(),
  limit: z.number().int().min(1).max(10).default(5),
  semantic: z.boolean().default(false),
}).refine((value) => value.from_year === undefined || value.to_year === undefined || value.from_year <= value.to_year, {
  message: "from_year는 to_year보다 클 수 없습니다.",
});

const resolveInputSchema = z.object({
  identifier: z.string().trim().min(3).max(500),
});

const traceInputSchema = z.object({
  seed: z.string().trim().min(3).max(500),
  target_query: z.string().trim().min(3).max(300).optional(),
  direction: z.enum(["backward", "forward", "both"]).default("both"),
  max_depth: z.number().int().min(1).max(3).default(2),
  max_path_length: z.number().int().min(2).max(5).default(4),
  candidates_per_node: z.number().int().min(2).max(10).default(6),
});

const warningOutputSchema = z.object({
  code: z.string(),
  message: z.string(),
  details: z.record(z.string(), z.unknown()).optional(),
});
const weightedTermOutputSchema = z.object({ id: z.string().optional(), display_name: z.string(), score: z.number() });
const topicOutputSchema = weightedTermOutputSchema.extend({
  subfield: z.string().optional(), field: z.string().optional(), domain: z.string().optional(),
});
const paperSummaryOutputSchema = z.object({
  id: z.string(), title: z.string(), publication_year: z.number().nullable(), publication_date: z.string().nullable(),
  authors: z.array(z.string()), authors_truncated: z.boolean(), doi: z.string().nullable(), source_url: z.string(),
  cited_by_count: z.number(), primary_topic: topicOutputSchema.nullable(), keywords: z.array(weightedTermOutputSchema),
  is_retracted: z.boolean(), version_group_key: z.string(),
});
const paperDetailOutputSchema = paperSummaryOutputSchema.extend({
  abstract: z.string().nullable(), topics: z.array(topicOutputSchema),
  referenced_work_ids: z.array(z.string()), related_work_ids: z.array(z.string()),
});
const evidenceOutputSchema = z.object({
  kind: z.enum(["citation", "topic", "keyword", "chronology", "provider_relation"]),
  value: z.string(), source: z.enum(["openalex", "derived"]),
});
const edgeOutputSchema = z.object({
  from_id: z.string(), to_id: z.string(), relationship: z.enum(["cites", "cited_by", "related", "topic_similar"]),
  evidence: z.array(evidenceOutputSchema), score: z.number(), inferred: z.boolean(), rationale: z.string(),
});
const pathOutputSchema = z.object({
  nodes: z.array(z.object({ index: z.number(), paper: paperSummaryOutputSchema, matched_terms: z.array(z.string()) })),
  edges: z.array(edgeOutputSchema), score: z.number(), score_margin: z.number().nullable(),
});
const errorOutputSchema = z.object({
  error: z.object({
    code: z.string(), message: z.string(), retryable: z.boolean(),
    details: z.record(z.string(), z.unknown()).optional(),
  }),
});
const searchOutputSchema = z.union([z.object({
  query: z.string(), papers: z.array(paperSummaryOutputSchema), total_candidates: z.number().optional(), warnings: z.array(warningOutputSchema),
}), errorOutputSchema]);
const resolveOutputSchema = z.union([z.object({
  status: z.enum(["exact", "ambiguous", "not_found"]), paper: paperDetailOutputSchema.optional(),
  candidates: z.array(paperSummaryOutputSchema).optional(), warnings: z.array(warningOutputSchema),
}), errorOutputSchema]);
const traceOutputSchema = z.union([z.object({
  seed: paperSummaryOutputSchema, target_query: z.string().optional(), path: pathOutputSchema.nullable(),
  explored: z.object({
    node_count: z.number(), edge_count: z.number(), request_count: z.number(), credits_used: z.number(),
    estimated_cost_usd: z.number(), truncated: z.boolean(),
  }),
  warnings: z.array(warningOutputSchema),
  methodology: z.object({
    provider: z.literal("openalex"), retrieved_at: z.string(), scoring_version: z.string(), schema_version: z.string(),
    weights: z.record(z.string(), z.number()),
    limits: z.object({
      max_depth: z.number(), max_path_length: z.number(), candidates_per_node: z.number(), max_nodes: z.number(),
      max_requests: z.number(), max_credits: z.number(), max_estimated_cost_usd: z.number(),
    }),
    query_parameters: z.record(z.string(), z.union([z.string(), z.number(), z.boolean()])),
    limitations: z.array(z.string()),
  }),
}), errorOutputSchema]);

const toSnakeCase = (key: string): string => key.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`);

const toExternalDto = (value: unknown): unknown => {
  if (Array.isArray(value)) return value.map(toExternalDto);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [toSnakeCase(key), toExternalDto(item)]));
  }
  return value;
};

const toStructuredContent = (value: unknown): Record<string, unknown> => toExternalDto(value) as Record<string, unknown>;

const successResult = (value: unknown) => {
  const structured = toStructuredContent(value);
  const full = JSON.stringify(structured, null, 2);
  if (full.length <= 20_000) {
    return { content: [{ type: "text" as const, text: full }], structuredContent: structured };
  }
  const truncationWarning = {
    code: "CONTENT_TRUNCATED",
    message: "사람이 읽는 text 결과를 줄였습니다. 전체 결과는 structuredContent에서 확인하세요.",
    details: { omitted_characters: full.length - 20_000 },
  };
  const warnings = Array.isArray(structured.warnings) ? structured.warnings : [];
  const enriched = { ...structured, warnings: [...warnings, truncationWarning] };
  return {
    content: [{ type: "text" as const, text: JSON.stringify({
      warning: truncationWarning,
      summary: {
        status: structured.status,
        paper_count: Array.isArray(structured.papers) ? structured.papers.length : undefined,
        has_path: structured.path !== null && structured.path !== undefined,
      },
    }, null, 2) }],
    structuredContent: enriched,
  };
};

const failureResult = (error: unknown) => {
  const publicError = toPublicError(error);
  return {
    content: [{ type: "text" as const, text: `${publicError.code}: ${publicError.message}` }],
    structuredContent: toStructuredContent({ error: publicError }),
    isError: true,
  };
};

export type ServerDependencies = {
  config?: AppConfig;
  provider?: ScholarlyProvider;
  logger?: AppLogger;
};

export const createServer = (dependencies: ServerDependencies = {}): McpServer => {
  const config = dependencies.config ?? loadConfig();
  const provider = dependencies.provider ?? new OpenAlexProvider(config);
  const logger = dependencies.logger ?? new StructuredLogger(config.logLevel);
  const createRequestId = (): string => {
    try {
      return logger.createRequestId();
    } catch {
      return randomUUID();
    }
  };
  const logTool = (event: Parameters<AppLogger["tool"]>[0]): void => {
    try {
      logger.tool(event);
    } catch {
      // 외부 로거 구현의 실패도 도구 응답을 방해하지 않게 한다.
    }
  };
  const paperService = new PaperService(provider);
  const pathService = new ConceptPathService(provider, config);
  const server = new McpServer(
    { name: "paper-concept-path-mcp", version: "0.1.0" },
    {
      capabilities: { tools: {} },
      instructions: "OpenAlex 메타데이터를 이용해 논문을 검색하고 제한된 개념 경로를 생성합니다. 결과의 학술적 주장은 반드시 원문에서 확인하세요.",
    },
  );

  server.registerTool("search_papers", {
    title: "연구 논문 검색",
    description: "연구 주제를 OpenAlex에서 검색해 개념 경로의 시작 논문 후보를 반환합니다.",
    inputSchema: searchInputSchema,
    outputSchema: searchOutputSchema,
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, async (input) => {
    const requestId = createRequestId();
    const startedAt = Date.now();
    const deadlineAt = startedAt + PAPER_TOOL_BUDGET_MS;
    const deadlineSignal = AbortSignal.timeout(PAPER_TOOL_BUDGET_MS);
    try {
      provider.resetUsage();
      const output = await paperService.search({
        query: input.query,
        ...(input.query_en ? { queryEn: input.query_en } : {}),
        ...(input.from_year !== undefined ? { fromYear: input.from_year } : {}),
        ...(input.to_year !== undefined ? { toYear: input.to_year } : {}),
        limit: input.limit,
        semantic: input.semantic,
      }, deadlineSignal, deadlineAt);
      logTool({ requestId, toolName: "search_papers", durationMs: Date.now() - startedAt, status: "success", usage: provider.getUsage() });
      return successResult(output);
    } catch (error) {
      const publicError = toPublicError(error);
      logTool({ requestId, toolName: "search_papers", durationMs: Date.now() - startedAt, status: "error", usage: provider.getUsage(), errorCode: publicError.code });
      return failureResult(error);
    }
  });

  server.registerTool("resolve_paper", {
    title: "논문 확인",
    description: "DOI, OpenAlex ID·URL 또는 제목을 정규화된 논문 레코드로 확인합니다.",
    inputSchema: resolveInputSchema,
    outputSchema: resolveOutputSchema,
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, async ({ identifier }) => {
    const requestId = createRequestId();
    const startedAt = Date.now();
    const deadlineAt = startedAt + PAPER_TOOL_BUDGET_MS;
    const deadlineSignal = AbortSignal.timeout(PAPER_TOOL_BUDGET_MS);
    try {
      provider.resetUsage();
      const output = await paperService.resolve(identifier, deadlineSignal, deadlineAt);
      logTool({ requestId, toolName: "resolve_paper", durationMs: Date.now() - startedAt, status: "success", usage: provider.getUsage() });
      return successResult(output);
    } catch (error) {
      const publicError = toPublicError(error);
      logTool({ requestId, toolName: "resolve_paper", durationMs: Date.now() - startedAt, status: "error", usage: provider.getUsage(), errorCode: publicError.code });
      return failureResult(error);
    }
  });

  server.registerTool("trace_concept_path", {
    title: "논문 개념 경로 생성",
    description: "시작 논문에서 인용·주제·키워드 근거를 가진 제한된 논문 경로를 생성합니다. 결과는 전체 문헌의 전역 최적 경로가 아닙니다.",
    inputSchema: traceInputSchema,
    outputSchema: traceOutputSchema,
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, async (input) => {
    const requestId = createRequestId();
    const startedAt = Date.now();
    try {
      const output = await pathService.trace({
        seed: input.seed,
        ...(input.target_query ? { targetQuery: input.target_query } : {}),
        direction: input.direction,
        maxDepth: input.max_depth,
        maxPathLength: input.max_path_length,
        candidatesPerNode: input.candidates_per_node,
      });
      logTool({ requestId, toolName: "trace_concept_path", durationMs: Date.now() - startedAt, status: "success", usage: provider.getUsage() });
      return successResult(output);
    } catch (error) {
      const publicError = toPublicError(error);
      logTool({ requestId, toolName: "trace_concept_path", durationMs: Date.now() - startedAt, status: "error", usage: provider.getUsage(), errorCode: publicError.code });
      return failureResult(error);
    }
  });

  return server;
};

export const toolSchemas = {
  searchInputSchema, resolveInputSchema, traceInputSchema,
  searchOutputSchema, resolveOutputSchema, traceOutputSchema,
};

export const serverInternals = { successResult };
