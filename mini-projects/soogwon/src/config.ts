import { z } from "zod/v4";

const booleanFromString = z
  .enum(["true", "false"])
  .transform((value) => value === "true");

const configSchema = z.object({
  OPENALEX_API_KEY: z.string().min(1).optional(),
  OPENALEX_BASE_URL: z.url().default("https://api.openalex.org"),
  REQUEST_TIMEOUT_MS: z.coerce.number().int().min(500).max(30_000).default(5_000),
  CACHE_TTL_SECONDS: z.coerce.number().int().min(0).max(86_400).default(3_600),
  CACHE_MAX_ENTRIES: z.coerce.number().int().min(10).max(10_000).default(500),
  LOG_LEVEL: z.enum(["debug", "info", "warning", "error", "silent"]).default("info"),
  MAX_OPENALEX_CREDITS_PER_TOOL: z.coerce.number().int().min(1).max(10_000).default(100),
  MAX_ESTIMATED_COST_USD: z.coerce.number().min(0.0001).max(10).default(0.01),
  SCORING_VERSION: z.string().regex(/^\d+\.\d+\.\d+$/).default("1.0.0"),
  ALLOW_INSECURE_OPENALEX_URL: booleanFromString.default(false),
});

export type AppConfig = {
  openAlexApiKey?: string;
  openAlexBaseUrl: string;
  requestTimeoutMs: number;
  cacheTtlSeconds: number;
  cacheMaxEntries: number;
  logLevel: "debug" | "info" | "warning" | "error" | "silent";
  maxCreditsPerTool: number;
  maxEstimatedCostUsd: number;
  scoringVersion: string;
};

export const loadConfig = (environment: NodeJS.ProcessEnv = process.env): AppConfig => {
  const parsed = configSchema.parse(environment);
  const url = new URL(parsed.OPENALEX_BASE_URL);
  if (url.protocol !== "https:" && !parsed.ALLOW_INSECURE_OPENALEX_URL) {
    throw new Error("OPENALEX_BASE_URL은 HTTPS여야 합니다.");
  }

  return {
    ...(parsed.OPENALEX_API_KEY ? { openAlexApiKey: parsed.OPENALEX_API_KEY } : {}),
    openAlexBaseUrl: url.toString().replace(/\/$/, ""),
    requestTimeoutMs: parsed.REQUEST_TIMEOUT_MS,
    cacheTtlSeconds: parsed.CACHE_TTL_SECONDS,
    cacheMaxEntries: parsed.CACHE_MAX_ENTRIES,
    logLevel: parsed.LOG_LEVEL,
    maxCreditsPerTool: parsed.MAX_OPENALEX_CREDITS_PER_TOOL,
    maxEstimatedCostUsd: parsed.MAX_ESTIMATED_COST_USD,
    scoringVersion: parsed.SCORING_VERSION,
  };
};
