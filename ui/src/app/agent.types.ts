/** The shapes the Python API answers with, mirrored for the browser. */

/** One log line the agent produced during a run, as it appears in the CLI too. */
export interface LogLine {
  /** When Python logged it, on Python's clock and in the CLI's own format. */
  time: string;
  level: string;
  logger: string;
  message: string;
}

/**
 * What a score is made of: one share per criterion, each in `[0, 1]`, and the blend they add up to.
 */
export interface ScoreParts {
  rating: number;
  popularity: number;
  price: number;
  total: number;
  neutral: string[];
}

/** How much each criterion counts towards the blend, as a fraction of one. */
export interface ScoreWeights {
  rating: number;
  popularity: number;
  price: number;
}

/** One thing a source page said about a product, beside the page that said it. */
export interface Opinion {
  text: string;
  url: string | null;
}

/** One ranked product. The `*_label` fields are written by Python's `Product`. */
export interface RankedProduct {
  /** Why this product cannot be bought, or `null` where it can. */
  cannot_pay: string | null;
  /** The currency a payment for this one would actually be made in, and what that
   *  amount says on a button -- Python's, out of the same check `cannot_pay` comes
   *  from, so all three are `null` for a product that cannot be bought. Not the
   *  product's own figures: a page that printed a bare "329.00" is priced in the run's
   *  currency (ADR-0043), so `currency` is `null` while the cart is in USD. */
  pay_currency: string | null;
  pay_label: string | null;
  /** Who the cart will name as the merchant: the seller a page printed, or the site
   *  the page is on where none did. Python's, for the reason the two above are --
   *  a confirmation that fell back to `seller` named nobody at all for the products
   *  no page printed a seller for, which is most of them. */
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
  /** What the source pages said about it, in their words -- each one grounded,
   *  and each carrying the page it was read off. */
  opinions: Opinion[];
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
  /** What the scores in `products` were blended by -- the same for every one of
   *  them, so it is sent once here rather than on each. */
  weights: ScoreWeights;
  products: RankedProduct[];
}

export type SortBy = 'score' | 'price' | 'rating';

/** One model server the run can be pointed at, with the pair that goes with it. */
export interface ProviderOption {
  name: string;
  label: string;
  model: string;
  base_url: string;
  takes_num_ctx: boolean;
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
  results: number;
  top: number;
  /** The shopper's own bounds, `null` for the bound nobody set -- which is the
   *  default, and what an empty box means (ADR-0039). */
  max_price: number | null;
  min_rating: number | null;
  min_reviews: number | null;
  /** How many seconds a fetched page stays usable on disk; 0 fetches every page fresh. */
  cache_ttl: number;
  region: string;
  /** Sites to take the facts from, separated by spaces or commas. Empty is the whole web. */
  sources: string;
  fetch: boolean;
  /** Whether the agent may pay for what it found. */
  pay: boolean;
  /** Whether the optional AP2 SDK is installed at all. */
  pay_available: boolean;
  rail: string;
  rail_options: RailOption[];
  /** The AP2-speaking endpoint a paying rail talks to. */
  merchant_url: string;
  /** The most one payment may be, `null` for no limit. */
  spend_limit: number | null;
  sort_by: SortBy;
  sort_options: SortBy[];
  /** Keyed by the name the value is sent under -- `results`, `top`,
   *  `max_price`, `cache_ttl` and the rest. A field with no entry here is one
   *  nothing bounds. */
  limits: Record<string, Limit>;
}

/** What the server made of a Trusted sources field, asked before a run rather than during one. */
export interface SourcesCheck {
  sources: string;
  error: string;
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
  /** Every number is nullable, because null is what a cleared box holds and
   *  what `toQuery` drops: "use the default" for most of them, and "no bound at
   *  all" for the three the shopper sets (ADR-0012, ADR-0039). */
  results?: number | null;
  top?: number | null;
  max_price?: number | null;
  min_rating?: number | null;
  min_reviews?: number | null;
  cache_ttl?: number | null;
  spend_limit?: number | null;
  pay?: boolean;
  rail?: string;
  merchant_url?: string;
  sort_by?: SortBy;
  temperature?: number | null;
  num_ctx?: number | null;
  model_timeout?: number | null;
  /** Two-valued: the tri-state's `null` cannot be sent -- see `Thinking`. */
  think?: boolean;
  fetch?: boolean;
}

/** What a re-sort sends: the products of a finished run, and the order to put them in. */
export interface RankOptions {
  request: string;
  products: RankedProduct[];
  sort_by: SortBy;
  top: number;
}

/**
 * What a payment sends: the finished run, which product of it to buy, and the approval the page was
 * given.
 */
export interface PayOptions {
  products: RankedProduct[];
  rank: number;
  approved?: { title: string; price: number; currency: string };
  rail?: string;
  merchant_url?: string;
  spend_limit?: number | null;
}

/** What a streamed run emits: progress, then exactly one ending. */
export type SearchEvent =
  | { kind: 'log'; line: LogLine }
  | { kind: 'result'; result: SearchResult }
  | { kind: 'failure'; message: string; status: number; field: string | null };
