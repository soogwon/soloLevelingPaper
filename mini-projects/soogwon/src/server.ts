import { McpServer } from "@modelcontextprotocol/server";
import { z } from "zod/v4";
import type { AppConfig } from "./config.js";
import { loadConfig } from "./config.js";
import { toPublicError } from "./infrastructure/errors.js";
import { OpenAlexProvider } from "./providers/openalex-provider.js";
import type { ScholarlyProvider } from "./providers/scholarly-provider.js";
import { ConceptPathService } from "./services/concept-path-service.js";
import { PaperService } from "./services/paper-service.js";

const currentYear = new Date().getUTCFullYear();

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

const toSnakeCase = (key: string): string => key.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`);

const toExternalDto = (value: unknown): unknown => {
  if (Array.isArray(value)) return value.map(toExternalDto);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [toSnakeCase(key), toExternalDto(item)]));
  }
  return value;
};

const toStructuredContent = (value: unknown): Record<string, unknown> => toExternalDto(value) as Record<string, unknown>;

const successResult = (value: unknown) => ({
  content: [{ type: "text" as const, text: boundedText(toStructuredContent(value)) }],
  structuredContent: toStructuredContent(value),
});

const boundedText = (value: Record<string, unknown>): string => {
  const full = JSON.stringify(value, null, 2);
  if (full.length <= 20_000) return full;
  return JSON.stringify({
    warning: { code: "CONTENT_TRUNCATED", message: "전체 결과는 structuredContent에서 확인하세요." },
    summary: {
      status: value.status,
      paper_count: Array.isArray(value.papers) ? value.papers.length : undefined,
      has_path: value.path !== null && value.path !== undefined,
    },
  }, null, 2);
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
};

export const createServer = (dependencies: ServerDependencies = {}): McpServer => {
  const config = dependencies.config ?? loadConfig();
  const provider = dependencies.provider ?? new OpenAlexProvider(config);
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
    outputSchema: z.record(z.string(), z.unknown()),
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, async (input) => {
    try {
      provider.resetUsage();
      const output = await paperService.search({
        query: input.query,
        ...(input.query_en ? { queryEn: input.query_en } : {}),
        ...(input.from_year !== undefined ? { fromYear: input.from_year } : {}),
        ...(input.to_year !== undefined ? { toYear: input.to_year } : {}),
        limit: input.limit,
        semantic: input.semantic,
      });
      return successResult(output);
    } catch (error) {
      return failureResult(error);
    }
  });

  server.registerTool("resolve_paper", {
    title: "논문 확인",
    description: "DOI, OpenAlex ID·URL 또는 제목을 정규화된 논문 레코드로 확인합니다.",
    inputSchema: resolveInputSchema,
    outputSchema: z.record(z.string(), z.unknown()),
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, async ({ identifier }) => {
    try {
      provider.resetUsage();
      return successResult(await paperService.resolve(identifier));
    } catch (error) {
      return failureResult(error);
    }
  });

  server.registerTool("trace_concept_path", {
    title: "논문 개념 경로 생성",
    description: "시작 논문에서 인용·주제·키워드 근거를 가진 제한된 논문 경로를 생성합니다. 결과는 전체 문헌의 전역 최적 경로가 아닙니다.",
    inputSchema: traceInputSchema,
    outputSchema: z.record(z.string(), z.unknown()),
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, async (input) => {
    try {
      const output = await pathService.trace({
        seed: input.seed,
        ...(input.target_query ? { targetQuery: input.target_query } : {}),
        direction: input.direction,
        maxDepth: input.max_depth,
        maxPathLength: input.max_path_length,
        candidatesPerNode: input.candidates_per_node,
      });
      return successResult(output);
    } catch (error) {
      return failureResult(error);
    }
  });

  return server;
};

export const toolSchemas = { searchInputSchema, resolveInputSchema, traceInputSchema };
