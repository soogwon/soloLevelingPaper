import { randomUUID } from "node:crypto";
import type { RequestUsage } from "../domain/models.js";

export type LogLevel = "debug" | "info" | "warning" | "error" | "silent";

export type ToolLogEvent = {
  requestId: string;
  toolName: string;
  durationMs: number;
  status: "success" | "error";
  usage: RequestUsage;
  errorCode?: string;
};

export interface AppLogger {
  createRequestId(): string;
  tool(event: ToolLogEvent): void;
}

const LEVEL_PRIORITY: Record<Exclude<LogLevel, "silent">, number> = {
  debug: 10,
  info: 20,
  warning: 30,
  error: 40,
};

export class StructuredLogger implements AppLogger {
  public constructor(
    private readonly level: LogLevel,
    private readonly write: (value: string) => void = (value) => process.stderr.write(value),
  ) {}

  public createRequestId(): string {
    return randomUUID();
  }

  public tool(event: ToolLogEvent): void {
    const eventLevel = event.status === "error" ? "error" : "info";
    if (!this.#enabled(eventLevel)) return;
    this.#emit(`${JSON.stringify({
      timestamp: new Date().toISOString(),
      level: eventLevel,
      event: "mcp_tool_completed",
      request_id: event.requestId,
      tool_name: event.toolName,
      duration_ms: event.durationMs,
      status: event.status,
      external_request_count: event.usage.requestCount,
      cache_hit_count: event.usage.cacheHitCount,
      credits_used: event.usage.creditsUsed,
      rate_limit_remaining: event.usage.rateLimitRemaining,
      credit_estimate_delta: event.usage.creditEstimateDelta,
      estimated_cost_usd: event.usage.estimatedCostUsd,
      ...(event.errorCode ? { error_code: event.errorCode } : {}),
    })}\n`);
  }

  public system(event: "mcp_stdio_started" | "mcp_stdio_error", level: "info" | "error"): void {
    if (!this.#enabled(level)) return;
    this.#emit(`${JSON.stringify({ timestamp: new Date().toISOString(), level, event })}\n`);
  }

  #emit(value: string): void {
    try {
      this.write(value);
    } catch {
      // 로깅 실패가 MCP 도구 실행 결과를 변경하지 않게 한다.
    }
  }

  #enabled(level: Exclude<LogLevel, "silent">): boolean {
    return this.level !== "silent" && LEVEL_PRIORITY[level] >= LEVEL_PRIORITY[this.level];
  }
}
