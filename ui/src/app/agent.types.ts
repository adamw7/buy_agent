/** The shapes the Python API answers with, mirrored for the browser. */

/** One log line the agent produced during a run, as it appears in the CLI too. */
export interface LogLine {
  /** When Python logged it, on Python's clock and in the CLI's own format. */
  time: string;
  level: string;
  logger: string;
  message: string;
}

/** What a score is made of: one share per criterion, each in `[0, 1]`, and the
 *  blend they add up to. `neutral` names the criteria this product published
 *  nothing for, which scored the neutral 0.5 rather than being read off a page --
 *  the one thing the numbers alone cannot say, since a criterion assumed and a
 *  criterion that scored middling are the same 0.5 (ADR-0041). Python decides
 *  every value here; the page only draws them. */
export interface ScoreParts {
  rating: number;
  popularity: number;
  price: number;
  total: number;
  neutral: string[];
}

/** How much each criterion counts towards the blend, as a fraction of one.
 *
 *  A run-level fact and not a per-product one, which is why it travels on the
 *  result rather than inside `ScoreParts`: the shares there are each scored out
 *  of 1 on their own, so three of them drawn beside a total they do not add up
 *  to say nothing about which criterion a placing actually turned on. Python
 *  normalises them (`RankingWeights.fractions`); the card only draws them. */
export interface ScoreWeights {
  rating: number;
  popularity: number;
  price: number;
}

/**
 * One thing a source page said about a product, beside the page that said it.
 *
 * Both halves are Python's: `verification.verify_opinions()` keeps a quote only
 * where a page that mentions this product printed it, and `url` is that page --
 * the first of them, never a link the model wrote. `null` where the result the
 * quote came off carried no URL, which is a quote to show and nothing to link.
 */
export interface Opinion {
  text: string;
  url: string | null;
}

/** One ranked product. The `*_label` fields are written by Python's `Product`. */
export interface RankedProduct {
  /** Why this product cannot be bought, or `null` where it can. Python's
   *  judgement, made by the same check the payment itself goes through, so the
   *  page never offers a Pay button the server would refuse (ADR-0012). It is a
   *  sentence and not a flag because every reason is worth saying: each names
   *  something the sources did not establish -- no price survived grounding, no
   *  currency was printed, the price is in one this run cannot place, or no page
   *  was linked. */
  cannot_pay: string | null;
  /** The currency a payment for this one would actually be made in, and what
   *  that amount says on a button -- Python's, out of the same check
   *  `cannot_pay` comes from, so both are `null` for a product that cannot be
   *  bought. Not the product's own figures: a page that printed a bare "329.00"
   *  is priced in the run's currency (ADR-0043), so `currency` is `null` while
   *  the cart is in USD. The card shows `pay_label` on its Pay button and its
   *  confirmation, and echoes `pay_currency` back as part of the approval. */
  pay_currency: string | null;
  pay_label: string | null;
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

/** One rail a payment can go through, with what goes with it. `moves_money` is
 *  false for the dry run, which signs a real AP2 authorisation and charges
 *  nobody -- said by Python rather than worked out from the name here, for the
 *  reason `takes_num_ctx` is. `needs_endpoint` is what disables the address
 *  field when there is nowhere to point it. */
export interface RailOption {
  name: string;
  label: string;
  endpoint: string;
  needs_endpoint: boolean;
  moves_money: boolean;
}

/** What came of a payment. Never the mandate chain: that authorises the purchase
 *  to whoever holds it, and this reaches a browser. `reference` is the hash that
 *  points back at it, which is what AP2 says a receipt binds by.
 *
 *  `paid` is whether money actually moved -- false for a dry run, whose whole
 *  point is that it did not -- and `autonomous` whether a pre-signed open
 *  mandate authorised it rather than a person approving the cart. `enrolled_key`
 *  is false when the signature was made with a key the process invented, which
 *  demonstrates the shape of an authorisation without being one. */
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
  think: boolean | null;
  results: number;
  top: number;
  /** The shopper's own bounds, `null` for the bound nobody set -- which is the
   *  default, and what an empty box means (ADR-0039). */
  max_price: number | null;
  min_rating: number | null;
  min_reviews: number | null;
  /** How many seconds a fetched page stays usable on disk; 0 fetches every page
   *  fresh. Where they are kept is the server's own business (ADR-0040). */
  cache_ttl: number;
  region: string;
  /** Sites to take the facts from, separated by spaces or commas. Empty is the whole web. */
  sources: string;
  fetch: boolean;
  /** Whether the agent may pay for what it found. Off by default: paying happens
   *  after a run, to one product, on a separate decision. */
  pay: boolean;
  /** Whether the optional AP2 SDK is installed at all. False, and the page says
   *  so rather than offering a button whose only outcome is a sentence about pip. */
  pay_available: boolean;
  rail: string;
  rail_options: RailOption[];
  /** The AP2-speaking endpoint a paying rail talks to. Empty for the rail's own,
   *  which is empty for the dry run -- it has nowhere to be. */
  merchant_url: string;
  /** The most one payment may be, `null` for no limit. Unlike the bounds above,
   *  a price the run cannot place fails it rather than passing. */
  spend_limit: number | null;
  sort_by: SortBy;
  sort_options: SortBy[];
  /** Keyed by the name the value is sent under -- `results`, `top`,
   *  `max_price`, `cache_ttl` and the rest. A field with no entry here is one
   *  nothing bounds. */
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
  /** Two-valued: the tri-state's `null` cannot be sent -- see `Thinking`. */
  think?: boolean;
  fetch?: boolean;
}

/** What a re-sort sends: the products of a finished run, and the order to put
 *  them in. They go back to Python because the ordering is Python's -- what is
 *  skipped is the searching, not the ranking, so a criterion changed after the
 *  run costs a request rather than another minute (ADR-0035). `top` travels with
 *  them so the answer keeps highlighting as many as the run did. */
export interface RankOptions {
  request: string;
  products: RankedProduct[];
  sort_by: SortBy;
  top: number;
}

/** What a payment sends: the finished run, which product of it to buy, and the
 *  approval the page was given.
 *
 *  `approved` is an echo and not a cart -- the cart is built on the server from
 *  `products`, and this has to match it. So a page showing a stale price cannot
 *  buy at that price, and the approval is witnessed rather than asserted
 *  (ADR-0012). It is omitted where a pre-signed open mandate authorises the run,
 *  which is the whole meaning of the autonomous mode. */
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
