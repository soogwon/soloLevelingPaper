import { describe, expect, it } from "vitest";
import { TtlLruCache } from "../../src/infrastructure/cache.js";

describe("TtlLruCache", () => {
  it("만료된 값을 반환하지 않는다", () => {
    let now = 0;
    const cache = new TtlLruCache<string>(2, 100, () => now);
    cache.set("a", "value");
    now = 101;
    expect(cache.get("a")).toBeUndefined();
  });

  it("최근 사용되지 않은 항목을 제거한다", () => {
    const cache = new TtlLruCache<string>(2, 100);
    cache.set("a", "A");
    cache.set("b", "B");
    expect(cache.get("a")).toBe("A");
    cache.set("c", "C");
    expect(cache.get("b")).toBeUndefined();
    expect(cache.get("a")).toBe("A");
  });
});
