/** The shapes the Python API answers with, mirrored for the browser. */

/** One log line of a run, as the CLI prints it. */
export interface LogLine {
  /** When Python logged it, in the CLI's format. */
  time: string;
  level: string;
  logger: string;
  message: string;
}

/** A score's parts: one share per criterion in `[0, 1]`, and the blend. */
export interface ScoreParts {
  rating: number;
  popularity: number;
  price: number;
  total: number;
  neutral: string[];
}

/** Each criterion's weight, as a fraction of one. */
export interface ScoreWeights {
  rating: number;
  popularity: number;
  price: number;
}

/** A quote about a product, and the page that printed it. */
export interface Opinion {
  text: string;
  url: string | null;
}

/** One listing's price, currency, shop and page, which travel together (ADR-0058). */
export interface Offer {
  price: number;
  currency: string | null;
  seller: string | null;
  url: string | null;
  /** Python's wording; the card never formats money. */
  price_label: string;
}

/** One ranked product. The `*_label` fields are written by Python's `Product`. */
export interface RankedProduct {
  /** Why this product cannot be bought, or `null` where it can. */
  cannot_pay: string | null;
  /** The cart's currency and amount, from the check behind `cannot_pay` (so `null`
   *  together with it). Not the product's own figures (ADR-0043). */
  pay_currency: string | null;
  pay_label: string | null;
  /** The cart's merchant: the printed seller, else the page's site. */
  pay_merchant: string | null;
  rank: number;
  score: number;
  breakdown: ScoreParts;
  name: string;
  price: number | null;
  currency: string | null;
  rating: number | null;
  review_count: number | null;
  seller: string | null;
  url: string | null;
  /** Grounded quotes, each with its page. */
  opinions: Opinion[];
  /** Every priced listing, the headline among them; ranking reads only the headline. */
  offers: Offer[];
  notes: string | null;
  price_label: string;
  rating_label: string;
  /** "3 listings, 129.00-149.00 USD", or `null` for fewer than two. */
  offers_label: string | null;
}

/** One candidate that left the report, and what took it out. */
export interface Removal {
  /** Its name when removed. */
  name: string;
  /** `clean`, `ground`, `deduplicate`, `merge` or `limits`: for grouping only. */
  step: string;
  /** Python's sentence; the page writes none of its own. */
  reason: string;
}

/** What one product did between the last run of a search and this one. */
export interface Change {
  name: string;
  /** `new`, `gone`, `cheaper`, `dearer`, `steady` or `unplaced` (ADR-0043): for
   *  grouping and colour only. */
  movement: string;
  /** Now and then, in Python's wording. */
  price_label: string | null;
  was_label: string | null;
  /** Negative is cheaper; `null` where the two are not comparable. */
  delta: number | null;
  /** Python's sentence; the page writes none of its own. */
  detail: string;
}

/** Everything one finished run produced. */
export interface SearchResult {
  request: string;
  count: number;
  top_n: number;
  sort_by: SortBy;
  /** The weights every score was blended by. */
  weights: ScoreWeights;
  products: RankedProduct[];
  /** What the run removed (ADR-0055). Empty from a re-sort; the page keeps the run's. */
  dropped: Removal[];
  /** What moved since the last run (ADR-0060). Empty from a re-sort, as `dropped` is. */
  changes: Change[];
  /** The day compared against, or `null` if no earlier run was kept. */
  compared_with: string | null;
}

export type SortBy = 'score' | 'price' | 'rating';

/** One model server the run can be pointed at, with the pair that goes with it. */
export interface ProviderOption {
  name: string;
  label: string;
  model: string;
  base_url: string;
  takes_num_ctx: boolean;
  /** Whether a run can ask for CPU only, or the server fixed its device at startup. */
  takes_cpu_only: boolean;
}

/** One search backend the run can be pointed at, and what it needs to be asked. */
export interface BackendOption {
  name: string;
  label: string;
  /** Where it listens; empty for a library-backed backend. */
  endpoint: string;
  needs_key: boolean;
  /** Whether this server has the key it needs (Python's answer). */
  configured: boolean;
}

/** What one number field may hold, as `config.LIMITS` declares it. */
export interface Limit {
  min: number;
  max: number;
}

/** One rail a payment can go through, with what goes with it. */
export interface RailOption {
  name: string;
  label: string;
  endpoint: string;
  needs_endpoint: boolean;
  moves_money: boolean;
}

/** What came of a payment. */
export interface Receipt {
  paid: boolean;
  rail: string;
  merchant: string;
  title: string;
  price: number;
  currency: string;
  amount: number;
  price_label: string;
  transaction_id: string;
  reference: string;
  autonomous: boolean;
  enrolled_key: boolean;
  detail: string;
}

/** The form's starting values, served from the agent's own config defaults. */
export interface AgentDefaults {
  provider: string;
  provider_options: ProviderOption[];
  model: string;
  base_url: string;
  temperature: number;
  num_ctx: number | null;
  /** The longest one answer may take, in seconds. */
  model_timeout: number;
  think: boolean | null;
  /** Whether to keep the model off the GPU entirely. */
  cpu_only: boolean;
  results: number;
  top: number;
  /** The shopper's bounds; `null`, the default, is no bound (ADR-0039). */
  max_price: number | null;
  min_rating: number | null;
  min_reviews: number | null;
  /** How many seconds a fetched page stays usable on disk; 0 fetches every page fresh. */
  cache_ttl: number;
  /** Whether runs are recorded, so the next can say what moved. */
  journal: boolean;
  region: string;
  /** The run's currency; empty, the default, lets the set vote (ADR-0043, ADR-0056). */
  currency: string;
  /** Every currency a run may count in. */
  currency_options: string[];
  backend: string;
  backend_options: BackendOption[];
  /** Sites to take the facts from, separated by spaces or commas. Empty is the whole web. */
  sources: string;
  fetch: boolean;
  /** Whether the agent may pay for what it found. */
  pay: boolean;
  /** Whether the optional AP2 SDK is installed at all. */
  pay_available: boolean;
  /** Whether this server has a camera; not a setting (ADR-0065). */
  screenshots: boolean;
  rail: string;
  rail_options: RailOption[];
  /** The AP2-speaking endpoint a paying rail talks to. */
  merchant_url: string;
  /** The most one payment may be, `null` for no limit. */
  spend_limit: number | null;
  sort_by: SortBy;
  sort_options: SortBy[];
  /** Each criterion as the order it produces -- "Cheapest first" -- which is what the
   *  two ordering controls list it by; Python's words, as the report's heading is. */
  sort_labels: Record<SortBy, string>;
  /** Each number's range, keyed as sent (`results`, `top`, ...); absent is unbounded. */
  limits: Record<string, Limit>;
}

/** The server's verdict on a Trusted sources field, asked before a run. */
export interface SourcesCheck {
  sources: string;
  error: string;
}

/** A bound the request states in words; offered to its box, never applied (ADR-0059). */
export interface NoticedBound {
  /** The setting that would enforce it: `max_price`, `min_rating`, `min_reviews`. */
  bound: string;
  value: number;
  /** Why the box holds a number nobody typed, in Python's words. */
  note: string;
}

/** Bounds read out of a request; carries the request so stale answers can be dropped. */
export interface BoundsCheck {
  request: string;
  noticed: NoticedBound[];
}

/** Which model server to ask about, and how to ask it. */
export interface ModelSource {
  provider: string;
  base_url: string;
}

/** One model a server is holding. */
export interface InstalledModel {
  name: string;
  completion: boolean;
}

/** Whether the model server answered, and what it is serving. */
export interface ModelStatus {
  provider: string;
  label: string;
  base_url: string;
  reachable: boolean;
  models: InstalledModel[];
  /** The transport's own reason, when it could not be reached. */
  detail?: string;
  /** The provider's remedy, as a run would fail with; only when unreachable. */
  hint?: string;
}

/** What the form sends. Everything but `request` is optional; blanks mean "default". */
export interface SearchOptions {
  request: string;
  provider?: string;
  model?: string;
  base_url?: string;
  region?: string;
  /** Blank lets the set vote on its own scale, which is the default (ADR-0056). */
  currency?: string;
  backend?: string;
  sources?: string;
  /** Null is a cleared box, dropped by `toQuery`: the default, or no bound
   *  (ADR-0012, ADR-0039). */
  results?: number | null;
  top?: number | null;
  max_price?: number | null;
  min_rating?: number | null;
  min_reviews?: number | null;
  cache_ttl?: number | null;
  spend_limit?: number | null;
  journal?: boolean;
  pay?: boolean;
  rail?: string;
  merchant_url?: string;
  sort_by?: SortBy;
  temperature?: number | null;
  num_ctx?: number | null;
  model_timeout?: number | null;
  /** Two-valued: the tri-state's `null` cannot be sent -- see `Thinking`. */
  think?: boolean;
  /** Left out where the server fixes its own device. */
  cpu_only?: boolean;
  fetch?: boolean;
}

/** What a re-sort sends: the products of a finished run, and the order to put them in. */
export interface RankOptions {
  request: string;
  products: RankedProduct[];
  sort_by: SortBy;
  top: number;
  /** The run's currency, so the set does not vote again (ADR-0056). */
  currency?: string;
}

/** What a payment sends: the run's products, which to buy, and the approval shown. */
export interface PayOptions {
  products: RankedProduct[];
  rank: number;
  approved?: { title: string; price: number; currency: string };
  rail?: string;
  merchant_url?: string;
  spend_limit?: number | null;
  /** The run's currency, as a re-sort sends it. */
  currency?: string;
}

/** What a streamed run emits: progress, then exactly one ending. */
export type SearchEvent =
  | { kind: 'log'; line: LogLine }
  | { kind: 'result'; result: SearchResult }
  | { kind: 'failure'; message: string; status: number; field: string | null };
