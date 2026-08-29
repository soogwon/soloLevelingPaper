import { describe, expect, it, vi } from "vitest";
import type { AppConfig } from "../../src/config.js";
import { normalizeOpenAlexWork, OpenAlexProvider } from "../../src/providers/openalex-provider.js";

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
});
