import type { PaperDetail, RequestUsage, SearchResult } from "../domain/models.js";

export type ProviderSearchInput = {
  query: string;
  fromYear?: number;
  toYear?: number;
  limit: number;
  semantic: boolean;
};

export interface ScholarlyProvider {
  searchWorks(input: ProviderSearchInput, signal?: AbortSignal): Promise<SearchResult>;
  getWork(identifier: string, signal?: AbortSignal): Promise<PaperDetail | null>;
  getWorksByIds(ids: string[], signal?: AbortSignal): Promise<PaperDetail[]>;
  getCitingWorks(id: string, limit: number, signal?: AbortSignal): Promise<PaperDetail[]>;
  getUsage(): RequestUsage;
  resetUsage(): void;
}
