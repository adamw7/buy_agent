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
  /** What the source pages said about it, in their words -- each one grounded. */
  opinions: string[];
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
  /** Sites to take the facts from, separated by spaces or commas. Empty is the whole web. */
  sources: string;
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
  sources?: string;
  results?: number;
  top?: number;
  sort_by?: SortBy;
  temperature?: number;
  num_ctx?: number | null;
  /** Two-valued: the tri-state's `null` cannot be sent -- see `Thinking`. */
  think?: boolean;
  fetch?: boolean;
}

/** What a streamed run emits: progress, then exactly one ending. */
export type SearchEvent =
  | { kind: 'log'; line: LogLine }
  | { kind: 'result'; result: SearchResult }
  | { kind: 'failure'; message: string; status: number };
