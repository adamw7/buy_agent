/** The shapes the Python API answers with, mirrored for the browser. */

/** One log line the agent produced during a run, as it appears in the CLI too. */
export interface LogLine {
  level: string;
  logger: string;
  message: string;
}

/** One ranked product. The `*_label` fields are written by Python's `Product`. */
export interface RankedProduct {
  rank: number;
  score: number;
  name: string;
  price: number | null;
  currency: string | null;
  rating: number | null;
  review_count: number | null;
  seller: string | null;
  url: string | null;
  notes: string | null;
  price_label: string;
  rating_label: string;
}

/** Everything one finished run produced. */
export interface SearchResult {
  request: string;
  count: number;
  top_n: number;
  sort_by: SortBy;
  products: RankedProduct[];
}

export type SortBy = 'score' | 'price' | 'rating';

/** The form's starting values, served from the agent's own config defaults. */
export interface AgentDefaults {
  model: string;
  base_url: string;
  temperature: number;
  num_ctx: number | null;
  think: boolean | null;
  results: number;
  top: number;
  region: string;
  fetch: boolean;
  sort_by: SortBy;
  sort_options: SortBy[];
}

/** Whether Ollama answered, and which models it has pulled. */
export interface ModelStatus {
  base_url: string;
  reachable: boolean;
  models: string[];
  detail?: string;
}

/** What the form sends. Everything but `request` is optional; blanks mean "default". */
export interface SearchOptions {
  request: string;
  model?: string;
  base_url?: string;
  region?: string;
  results?: number;
  top?: number;
  sort_by?: SortBy;
  temperature?: number;
  num_ctx?: number | null;
  think?: boolean | null;
  fetch?: boolean;
}

/** What a streamed run emits: progress, then exactly one ending. */
export type SearchEvent =
  | { kind: 'log'; line: LogLine }
  | { kind: 'result'; result: SearchResult }
  | { kind: 'failure'; message: string; status: number };
