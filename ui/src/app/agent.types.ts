/** The shapes the Python API answers with, mirrored for the browser. */

/** One log line of a run, as the CLI prints it. */
export interface LogLine {
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
  /** Grounded with the price, and dropped with it (ADR-0079). */
  availability: Availability | null;
  condition: Condition | null;
  price_label: string;
}

/** Whether a listing can be bought now, as a page said (ADR-0079). */
export type Availability = 'in stock' | 'out of stock';

/** The state a listing comes in, as a page said (ADR-0079). */
export type Condition = 'new' | 'used' | 'refurbished';

/** One ranked product. The `*_label` fields are written by Python's `Product`. */
export interface RankedProduct {
  /** Why this product cannot be bought, or `null` where it can. */
  cannot_pay: string | null;
  /** The cart's, `null` together with `cannot_pay`'s check (ADR-0043). */
  pay_currency: string | null;
  pay_label: string | null;
  pay_merchant: string | null;
  rank: number;
  score: number;
  breakdown: ScoreParts;
  name: string;
  price: number | null;
  currency: string | null;
  /** The headline listing's, grounded as its price is (ADR-0079). */
  availability: Availability | null;
  condition: Condition | null;
  rating: number | null;
  review_count: number | null;
  seller: string | null;
  url: string | null;
  opinions: Opinion[];
  /** Every priced listing, the headline among them; ranking reads only the headline. */
  offers: Offer[];
  notes: string | null;
  price_label: string;
  rating_label: string;
  /** "3 listings, 129.00-149.00 USD", or `null` for fewer than two. */
  offers_label: string | null;
  /** "In stock, refurbished", or `null` where no page said either (ADR-0079). */
  listing_label: string | null;
}

/** Whether a run found the price the shopper is waiting for; never applied (ADR-0080). */
export interface PriceAlert {
  below: number;
  below_label: string;
  /** The products at or under it that can be bought, cheapest first. */
  met: string[];
  /** Python's sentence; the page writes none of its own. */
  detail: string;
}

/** One candidate that left the report, and what took it out. */
export interface Removal {
  name: string;
  /** `clean`, `ground`, `deduplicate`, `merge` or `limits`: for grouping only. */
  step: string;
  /** Python's sentence; the page writes none of its own. */
  reason: string;
}

/** What one product did between the last run of a search and this one. */
export interface Change {
  name: string;
  /** For grouping and colour only (ADR-0060). */
  movement: string;
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
  weights: ScoreWeights;
  products: RankedProduct[];
  /** Handed back by a re-sort and a payment, so the set does not vote again (ADR-0056). */
  scale: string | null;
  /** What the run removed (ADR-0055). Empty from a re-sort; the page keeps the run's. */
  dropped: Removal[];
  /** What moved since the last run (ADR-0060). Empty from a re-sort, as `dropped` is. */
  changes: Change[];
  compared_with: string | null;
  /** `null` unless the run was given one, and from a re-sort; the page keeps the run's. */
  alert: PriceAlert | null;
}

export type SortBy = 'score' | 'price' | 'rating';

/** One model server the run can be pointed at, with the pair that goes with it. */
export interface ProviderOption {
  name: string;
  label: string;
  model: string;
  base_url: string;
  takes_num_ctx: boolean;
  takes_cpu_only: boolean;
}

/** One search backend the run can be pointed at, and what it needs to be asked. */
export interface BackendOption {
  name: string;
  label: string;
  endpoint: string;
  needs_key: boolean;
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
  model_timeout: number;
  think: boolean | null;
  cpu_only: boolean;
  results: number;
  top: number;
  /** The shopper's bounds; `null`, the default, is no bound (ADR-0039). */
  max_price: number | null;
  min_rating: number | null;
  min_reviews: number | null;
  /** Said whether anything is at or under it; never applied (ADR-0080). */
  alert_below: number | null;
  cache_ttl: number;
  journal: boolean;
  region: string;
  /** The run's currency; empty, the default, lets the set vote (ADR-0043, ADR-0056). */
  currency: string;
  currency_options: string[];
  backend: string;
  backend_options: BackendOption[];
  /** Separated by spaces or commas; empty is the whole web. */
  sources: string;
  fetch: boolean;
  pay: boolean;
  pay_available: boolean;
  /** Whether this server has a camera; not a setting (ADR-0065). */
  screenshots: boolean;
  rail: string;
  rail_options: RailOption[];
  merchant_url: string;
  spend_limit: number | null;
  sort_by: SortBy;
  sort_options: SortBy[];
  /** Each criterion as the order it gives ("Cheapest first"), in Python's words. */
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
  /** Null is a cleared box, dropped by `toQuery` (ADR-0012, ADR-0039). */
  results?: number | null;
  top?: number | null;
  max_price?: number | null;
  min_rating?: number | null;
  min_reviews?: number | null;
  alert_below?: number | null;
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
  /** The currency the run was counted in where the set voted, for the same reason. */
  scale?: string;
}

/** What a payment sends: the run's products, which to buy, and the approval shown. */
export interface PayOptions {
  products: RankedProduct[];
  rank: number;
  approved?: { title: string; price: number; currency: string };
  rail?: string;
  merchant_url?: string;
  spend_limit?: number | null;
  currency?: string;
  scale?: string;
}

/** What a streamed run emits: progress, then exactly one ending. */
export type SearchEvent =
  | { kind: 'log'; line: LogLine }
  | { kind: 'result'; result: SearchResult }
  | { kind: 'failure'; message: string; status: number; field: string | null };
