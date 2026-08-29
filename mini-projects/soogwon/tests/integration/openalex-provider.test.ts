import { describe, expect, it, vi } from "vitest";
import type { AppConfig } from "../../src/config.js";
import { normalizeOpenAlexWork, OpenAlexProvider, openAlexProviderInternals } from "../../src/providers/openalex-provider.js";

const config: AppConfig = {
  openAlexApiKey: "secret-key",
  openAlexBaseUrl: "https://api.openalex.org",
  requestTimeoutMs: 1_000,
  cacheTtlSeconds: 60,
  cacheMaxEntries: 10,
  logLevel: "silent",
  maxCreditsPerTool: 100,
  maxEstimatedCostUsd: 0.01,
  scoringVersion: "1.0.0",
};

const rawWork = {
  id: "https://openalex.org/W123",
  title: "A Test Paper",
  doi: "https://doi.org/10.1000/TEST",
  publication_year: 2021,
  publication_date: "2021-02-03",
  cited_by_count: 7,
  is_retracted: false,
  authorships: [{ author: { display_name: "Alice" } }],
  primary_topic: { id: "T1", display_name: "AI", score: 0.9 },
  topics: [{ id: "T1", display_name: "AI", score: 0.9 }],
  keywords: [{ display_name: "machine learning", score: 0.8 }],
  referenced_works: ["https://openalex.org/W100"],
  related_works: ["https://openalex.org/W200"],
  abstract_inverted_index: { Hello: [0], world: [1] },
};

describe("OpenAlexProvider", () => {
  it("OpenAlex Work를 내부 모델로 정규화한다", () => {
    const result = normalizeOpenAlexWork(rawWork);
    expect(result.id).toBe("W123");
    expect(result.doi).toBe("https://doi.org/10.1000/test");
    expect(result.abstract).toBe("Hello world");
    expect(result.referencedWorkIds).toEqual(["W100"]);
  });

  it("API 키를 URL이 아닌 Authorization 헤더로 전송한다", async () => {
    const fetchMock = vi.fn(async (_input: URL | RequestInfo, _init?: RequestInit) => new Response(JSON.stringify(rawWork), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    const provider = new OpenAlexProvider(config, fetchMock as typeof fetch);
    await provider.getWork("W123");
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).not.toContain("secret-key");
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer secret-key");
    expect(new URL(String(url)).searchParams.get("select")).toContain("referenced_works");
  });

  it("같은 요청은 캐시에서 반환한다", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(rawWork), { status: 200 }));
    const provider = new OpenAlexProvider(config, fetchMock as typeof fetch);
    await provider.getWork("W123");
    await provider.getWork("W123");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("동시에 실행된 도구 호출의 사용량을 서로 분리한다", async () => {
    const fetchMock = vi.fn(async (input: URL | RequestInfo) => {
      const id = String(input).match(/W\d+/)?.[0] ?? "W123";
      return new Response(JSON.stringify({ ...rawWork, id: `https://openalex.org/${id}` }), { status: 200 });
    });
    const provider = new OpenAlexProvider(config, fetchMock as typeof fetch);
    const call = async (id: string) => {
      provider.resetUsage();
      await provider.getWork(id);
      await Promise.resolve();
      return provider.getUsage();
    };
    const [left, right] = await Promise.all([call("W201"), call("W202")]);
    expect(left.requestCount).toBe(1);
    expect(right.requestCount).toBe(1);
  });

  it("404 응답을 음성 캐시해 같은 논문을 다시 요청하지 않는다", async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 404 }));
    const provider = new OpenAlexProvider(config, fetchMock as typeof fetch);
    await expect(provider.getWork("W404")).resolves.toBeNull();
    await expect(provider.getWork("W404")).resolves.toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("재시도를 포함해 요청 수 20회를 넘지 않는다", async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 500 }));
    const provider = new OpenAlexProvider(config, fetchMock as typeof fetch);
    provider.resetUsage();
    for (let index = 0; index < 6; index += 1) {
      await expect(provider.getWork(`W50${index}`)).rejects.toMatchObject({ code: "PROVIDER_RESPONSE_INVALID" });
    }
    await expect(provider.getWork("W599")).rejects.toMatchObject({ code: "BUDGET_EXCEEDED" });
    expect(provider.getUsage().requestCount).toBe(20);
    expect(fetchMock).toHaveBeenCalledTimes(20);
  }, 15_000);

  it("5xx Retry-After 이후 요청을 재시도한다", async () => {
    vi.useFakeTimers();
    try {
      const fetchMock = vi.fn()
        .mockResolvedValueOnce(new Response(null, { status: 503, headers: { "Retry-After": "1" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify(rawWork), { status: 200 }));
      const provider = new OpenAlexProvider(config, fetchMock as typeof fetch);
      const pending = provider.getWork("W123");
      await vi.advanceTimersByTimeAsync(999);
      expect(fetchMock).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(1);
      await expect(pending).resolves.toMatchObject({ id: "W123" });
      expect(fetchMock).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("OpenAlex 크레딧 실제값과 잔여량을 사용 통계에 반영한다", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(rawWork), {
      status: 200,
      headers: { "X-RateLimit-Credits-Used": "2", "X-RateLimit-Remaining": "98" },
    }));
    const provider = new OpenAlexProvider(config, fetchMock as typeof fetch);
    provider.resetUsage();
    await provider.getWork("W123");
    expect(provider.getUsage()).toMatchObject({ creditsUsed: 2, rateLimitRemaining: 98, creditEstimateDelta: 2 });
  });

  it("크레딧 헤더가 없으면 예상 크레딧을 유지한다", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ meta: { count: 1 }, results: [rawWork] }), { status: 200 }));
    const provider = new OpenAlexProvider(config, fetchMock as typeof fetch);
    provider.resetUsage();
    await provider.searchWorks({ query: "test", limit: 1, semantic: false });
    expect(provider.getUsage()).toMatchObject({ creditsUsed: 10, rateLimitRemaining: null, creditEstimateDelta: 0 });
  });

  it("Retry-After가 없으면 기본 지수 백오프를 적용한다", async () => {
    vi.useFakeTimers();
    try {
      const fetchMock = vi.fn()
        .mockResolvedValueOnce(new Response(null, { status: 503 }))
        .mockResolvedValueOnce(new Response(JSON.stringify(rawWork), { status: 200 }));
      const provider = new OpenAlexProvider(config, fetchMock as typeof fetch);
      const pending = provider.getWork("W123");
      await vi.advanceTimersByTimeAsync(249);
      expect(fetchMock).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(1);
      await expect(pending).resolves.toMatchObject({ id: "W123" });
      expect(fetchMock).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("HTTP 날짜 Retry-After를 밀리초 대기로 변환한다", () => {
    const now = Date.UTC(2026, 7, 29, 0, 0, 0);
    const headers = new Headers({ "Retry-After": new Date(now + 2_000).toUTCString() });
    expect(openAlexProviderInternals.retryAfterMilliseconds(headers, 250, now)).toBe(2_000);
    expect(openAlexProviderInternals.retryAfterMilliseconds(new Headers(), 250, now)).toBe(250);
  });

  it("5초보다 긴 Retry-After도 deadline 안이면 완전히 기다린다", async () => {
    vi.useFakeTimers();
    try {
      const fetchMock = vi.fn()
        .mockResolvedValueOnce(new Response(null, { status: 503, headers: { "Retry-After": "6" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify(rawWork), { status: 200 }));
      const provider = new OpenAlexProvider(config, fetchMock as typeof fetch);
      const pending = provider.getWork("W123", undefined, Date.now() + 8_000);
      await vi.advanceTimersByTimeAsync(5_999);
      expect(fetchMock).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(1);
      await expect(pending).resolves.toMatchObject({ id: "W123" });
      expect(fetchMock).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("대기와 다음 요청이 deadline 안에 불가능하면 조기 재시도하지 않는다", async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 503, headers: { "Retry-After": "6" } }));
    const provider = new OpenAlexProvider(config, fetchMock as typeof fetch);
    await expect(provider.getWork("W123", undefined, Date.now() + 6_500)).rejects.toMatchObject({
      code: "PROVIDER_TIMEOUT",
      retryable: true,
      details: { retryAfterMs: 6_000 },
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("일반 네트워크 오류의 재시도 대기 중 취소를 PROVIDER_TIMEOUT으로 변환한다", async () => {
    vi.useFakeTimers();
    try {
      const fetchMock = vi.fn()
        .mockRejectedValueOnce(new Error("network unavailable"))
        .mockResolvedValueOnce(new Response(JSON.stringify(rawWork), { status: 200 }));
      const provider = new OpenAlexProvider(config, fetchMock as typeof fetch);
      const controller = new AbortController();
      const pending = provider.getWork("W123", controller.signal, Date.now() + 10_000);
      await vi.advanceTimersByTimeAsync(0);
      expect(fetchMock).toHaveBeenCalledTimes(1);

      controller.abort(new Error("cancelled during backoff"));

      await expect(pending).rejects.toMatchObject({
        code: "PROVIDER_TIMEOUT",
        retryable: true,
        details: { retryAfterMs: 250 },
      });
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("일반 네트워크 오류 후 다음 요청 시간이 부족하면 재시도하지 않는다", async () => {
    const fetchMock = vi.fn(async () => {
      throw new Error("network unavailable");
    });
    const provider = new OpenAlexProvider(config, fetchMock as typeof fetch);

    await expect(provider.getWork("W123", undefined, Date.now() + 1_000)).rejects.toMatchObject({
      code: "PROVIDER_TIMEOUT",
      retryable: true,
      details: { retryAfterMs: 250 },
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("일일 예산 소진 429는 호출 내부에서 재시도하지 않는다", async () => {
    const fetchMock = vi.fn(async () => new Response(null, {
      status: 429,
      headers: { "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "3600" },
    }));
    const provider = new OpenAlexProvider(config, fetchMock as typeof fetch);
    await expect(provider.getWork("W123", undefined, Date.now() + 10_000)).rejects.toMatchObject({
      code: "PROVIDER_RATE_LIMIT",
      retryable: true,
      details: { retryAfterMs: 3_600_000 },
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("재시도 대기 전에 이미 취소된 signal을 즉시 전파한다", async () => {
    vi.useFakeTimers();
    try {
      const fetchMock = vi.fn(async () => new Response(null, { status: 503, headers: { "Retry-After": "60" } }));
      const provider = new OpenAlexProvider(config, fetchMock as typeof fetch);
      const controller = new AbortController();
      controller.abort(new Error("cancelled by caller"));
      await expect(provider.getWork("W123", controller.signal)).rejects.toMatchObject({
        code: "PROVIDER_TIMEOUT",
        retryable: true,
      });
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });
});
