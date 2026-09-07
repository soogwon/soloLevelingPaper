type CacheEntry<T> = { value: T; expiresAt: number };

export class TtlLruCache<T> {
  readonly #entries = new Map<string, CacheEntry<T>>();

  public constructor(
    private readonly maxEntries: number,
    private readonly defaultTtlMs: number,
    private readonly now: () => number = Date.now,
  ) {}

  public get(key: string): T | undefined {
    const entry = this.#entries.get(key);
    if (!entry) return undefined;
    if (entry.expiresAt <= this.now()) {
      this.#entries.delete(key);
      return undefined;
    }
    this.#entries.delete(key);
    this.#entries.set(key, entry);
    return entry.value;
  }

  public set(key: string, value: T, ttlMs = this.defaultTtlMs): void {
    if (ttlMs <= 0 || this.maxEntries <= 0) return;
    this.#entries.delete(key);
    this.#entries.set(key, { value, expiresAt: this.now() + ttlMs });
    while (this.#entries.size > this.maxEntries) {
      const oldest = this.#entries.keys().next().value as string | undefined;
      if (!oldest) break;
      this.#entries.delete(oldest);
    }
  }

  public clear(): void {
    this.#entries.clear();
  }

  public get size(): number {
    return this.#entries.size;
  }
}
