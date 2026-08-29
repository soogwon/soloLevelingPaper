export type WeightedTerm = {
  id?: string;
  displayName: string;
  score: number;
};

export type Topic = WeightedTerm & {
  subfield?: string;
  field?: string;
  domain?: string;
};

export type PaperSummary = {
  id: string;
  title: string;
  publicationYear: number | null;
  publicationDate: string | null;
  authors: string[];
  authorsTruncated: boolean;
  doi: string | null;
  sourceUrl: string;
  citedByCount: number;
  primaryTopic: Topic | null;
  keywords: WeightedTerm[];
  isRetracted: boolean;
  versionGroupKey: string;
};

export type PaperDetail = PaperSummary & {
  abstract: string | null;
  topics: Topic[];
  referencedWorkIds: string[];
  relatedWorkIds: string[];
};

export type Warning = {
  code: string;
  message: string;
  details?: Record<string, unknown>;
};

export type RelationshipType = "cites" | "cited_by" | "related" | "topic_similar";

export type Evidence = {
  kind: "citation" | "topic" | "keyword" | "chronology" | "provider_relation";
  value: string;
  source: "openalex" | "derived";
};

export type PathNode = {
  index: number;
  paper: PaperSummary;
  matchedTerms: string[];
};

export type PathEdge = {
  fromId: string;
  toId: string;
  relationship: RelationshipType;
  evidence: Evidence[];
  score: number;
  inferred: boolean;
  rationale: string;
};

export type ConceptPath = {
  nodes: PathNode[];
  edges: PathEdge[];
  score: number;
  scoreMargin: number | null;
};

export type SearchPapersInput = {
  query: string;
  queryEn?: string;
  fromYear?: number;
  toYear?: number;
  limit: number;
  semantic: boolean;
};

export type SearchPapersOutput = {
  query: string;
  papers: PaperSummary[];
  totalCandidates?: number;
  warnings: Warning[];
};

export type ResolvePaperOutput = {
  status: "exact" | "ambiguous" | "not_found";
  paper?: PaperDetail;
  candidates?: PaperSummary[];
  warnings: Warning[];
};

export type TraceConceptPathInput = {
  seed: string;
  targetQuery?: string;
  direction: "backward" | "forward" | "both";
  maxDepth: number;
  maxPathLength: number;
  candidatesPerNode: number;
};

export type RequestUsage = {
  requestCount: number;
  creditsUsed: number;
  estimatedCostUsd: number;
};

export type MethodologySummary = {
  provider: "openalex";
  retrievedAt: string;
  scoringVersion: string;
  schemaVersion: string;
  weights: Record<string, number>;
  limits: {
    maxDepth: number;
    maxPathLength: number;
    candidatesPerNode: number;
    maxNodes: number;
    maxRequests: number;
    maxCredits: number;
    maxEstimatedCostUsd: number;
  };
  queryParameters: Record<string, string | number | boolean>;
  limitations: string[];
};

export type TraceConceptPathOutput = {
  seed: PaperSummary;
  targetQuery?: string;
  path: ConceptPath | null;
  explored: {
    nodeCount: number;
    edgeCount: number;
    requestCount: number;
    creditsUsed: number;
    estimatedCostUsd: number;
    truncated: boolean;
  };
  warnings: Warning[];
  methodology: MethodologySummary;
};

export type SearchResult = {
  papers: PaperDetail[];
  totalCandidates?: number;
};

export const toPaperSummary = (paper: PaperDetail): PaperSummary => ({
  id: paper.id,
  title: paper.title,
  publicationYear: paper.publicationYear,
  publicationDate: paper.publicationDate,
  authors: paper.authors,
  authorsTruncated: paper.authorsTruncated,
  doi: paper.doi,
  sourceUrl: paper.sourceUrl,
  citedByCount: paper.citedByCount,
  primaryTopic: paper.primaryTopic,
  keywords: paper.keywords,
  isRetracted: paper.isRetracted,
  versionGroupKey: paper.versionGroupKey,
});
