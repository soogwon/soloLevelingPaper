import { describe, expect, it } from "vitest";
import { StructuredLogger } from "../../src/infrastructure/logger.js";

const usage = {
  requestCount: 2, cacheHitCount: 1, creditsUsed: 3, rateLimitRemaining: 97, creditEstimateDelta: 1, estimatedCostUsd: 0.0003,
};

describe("StructuredLogger", () => {
  it("검색어나 비밀값 없이 구조화된 도구 완료 로그를 기록한다", () => {
    const lines: string[] = [];
    const logger = new StructuredLogger("info", (value) => lines.push(value));
    logger.tool({ requestId: "request-1", toolName: "search_papers", durationMs: 12, status: "success", usage });
    const parsed = JSON.parse(lines[0]!) as Record<string, unknown>;
    expect(parsed).toMatchObject({
      event: "mcp_tool_completed", request_id: "request-1", tool_name: "search_papers",
      duration_ms: 12, external_request_count: 2, cache_hit_count: 1, rate_limit_remaining: 97, credit_estimate_delta: 1,
    });
    expect(lines[0]).not.toContain("query");
    expect(lines[0]).not.toContain("api_key");
  });

  it("silent 수준에서는 로그를 기록하지 않는다", () => {
    const lines: string[] = [];
    const logger = new StructuredLogger("silent", (value) => lines.push(value));
    logger.tool({ requestId: "request-1", toolName: "resolve_paper", durationMs: 1, status: "success", usage });
    logger.system("mcp_stdio_started", "info");
    expect(lines).toEqual([]);
  });

  it("stderr writer 실패를 호출자에게 전파하지 않는다", () => {
    const logger = new StructuredLogger("info", () => { throw new Error("stderr closed"); });
    expect(() => logger.tool({ requestId: "request-1", toolName: "search_papers", durationMs: 1, status: "success", usage })).not.toThrow();
    expect(() => logger.system("mcp_stdio_error", "error")).not.toThrow();
  });
});
