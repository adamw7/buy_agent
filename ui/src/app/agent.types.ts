/** The shapes the Python API answers with, mirrored for the browser. */

/** One log line the agent produced during a run, as it appears in the CLI too. */
export interface LogLine {
  /** When Python logged it, on Python's clock and in the CLI's own format. */
  time: string;
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

/** One model server the run can be pointed at, with the pair that goes with it.
 *  `takes_num_ctx` is false for vLLM, which fixes its context window when it
 *  starts (`--max-model-len`) rather than taking one per request. */
export interface ProviderOption {
  name: string;
  label: string;
  model: string;
  base_url: string;
  takes_num_ctx: boolean;
}

/** What one number field may hold, as `config.LIMITS` declares it.
 *  Shipped rather than written into the form: the browser applies the range and
 *  does not choose it, so there is no second copy to drift (ADR-0033). */
export interface Limit {
  min: number;
  max: number;
}

/** The form's starting values, served from the agent's own config defaults. */
export interface AgentDefaults {
  provider: string;
  provider_options: ProviderOption[];
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
  /** Keyed by the name the value is sent under -- `results`, `top`,
   *  `temperature`, `num_ctx`. A field with no entry here is one nothing bounds. */
  limits: Record<string, Limit>;
}

/** What the server made of a Trusted sources field, asked before a run rather
 *  than during one. `error` is empty when the field names sources; `sources` is
 *  the spec that was checked, so an answer about text the shopper has since
 *  typed over can be told from one about what is in the box now. */
export interface SourcesCheck {
  sources: string;
  error: string;
}

/** Which model server to ask about, and how to ask it. The provider travels with
 *  the address because the same URL is asked one way for Ollama and another for
 *  vLLM -- a vLLM asked Ollama's question answers 404. */
export interface ModelSource {
  provider: string;
  base_url: string;
}

/** One model a server is holding. `completion` is false for a model that cannot
 *  answer a prompt at all -- an embedding model, which Ollama holds alongside the
 *  chat ones and lists exactly the same way. Python decides it; the form marks it. */
export interface InstalledModel {
  name: string;
  completion: boolean;
}

/** Whether the model server answered, and what it is serving. `label` is how
 *  the pill above the form names it -- "Ollama", "vLLM" -- so a page pointed at
 *  one never reports the other being down. */
export interface ModelStatus {
  provider: string;
  label: string;
  base_url: string;
  reachable: boolean;
  models: InstalledModel[];
  /** The transport's own reason, when it could not be reached. */
  detail?: string;
  /** What to do about it, in the provider's own words -- the same sentence a
   *  run would have failed with. Present only when the server did not answer,
   *  and absent when there was no provider to ask. */
  hint?: string;
}

/** What the form sends. Everything but `request` is optional; blanks mean "default". */
export interface SearchOptions {
  request: string;
  provider?: string;
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
  | { kind: 'failure'; message: string; status: number; field: string | null };
