/** The payloads the server sends, as the specs need them to exist. */
import type {
  AgentDefaults,
  BackendOption,
  ModelStatus,
  ProviderOption,
  RailOption,
  RankedProduct,
  Receipt,
  ScoreWeights,
} from './agent.types';

/** The provider the form opens on, and the one that fixes its own window. */
export const OLLAMA: ProviderOption = {
  name: 'ollama',
  label: 'Ollama',
  model: 'llama3.2',
  base_url: 'http://localhost:11434',
  takes_num_ctx: true,
  takes_cpu_only: true,
};

export const VLLM: ProviderOption = {
  name: 'vllm',
  label: 'vLLM',
  model: 'Qwen/Qwen3-8B',
  base_url: 'http://localhost:8000/v1',
  takes_num_ctx: false,
  takes_cpu_only: false,
};

/** The rail that charges nobody, and the one that would. */
export const DRY_RUN: RailOption = {
  name: 'dry-run',
  label: 'Dry run',
  endpoint: '',
  needs_endpoint: false,
  moves_money: false,
};

export const CHARGES: RailOption = {
  ...DRY_RUN,
  name: 'http',
  label: 'HTTP endpoint',
  needs_endpoint: true,
  moves_money: true,
};

/** The backend that needs nothing, one that is a server of your own, and one that
 *  needs a key this server has not got. */
export const DDG: BackendOption = {
  name: 'ddg',
  label: 'DuckDuckGo',
  endpoint: '',
  needs_key: false,
  configured: true,
};

export const SEARXNG: BackendOption = {
  name: 'searxng',
  label: 'SearXNG',
  endpoint: 'http://localhost:8080',
  needs_key: false,
  configured: true,
};

export const BRAVE: BackendOption = {
  name: 'brave',
  label: 'Brave Search',
  endpoint: 'https://api.search.brave.com/res/v1/web/search',
  needs_key: true,
  configured: false,
};

/** What a run says its scores were blended by: the defaults, normalised. */
export const WEIGHTS: ScoreWeights = { rating: 0.5, popularity: 0.2, price: 0.3 };

/** The ranges `limits_payload` ships, which is what the form holds a box to. */
const LIMITS: AgentDefaults['limits'] = {
  results: { min: 1, max: 50 },
  top: { min: 1, max: 50 },
  temperature: { min: 0, max: 2 },
  num_ctx: { min: 1, max: 1_000_000 },
  model_timeout: { min: 1, max: 3_600 },
  max_price: { min: 1, max: 10_000_000 },
  min_rating: { min: 0, max: 5 },
  min_reviews: { min: 0, max: 10_000_000 },
  cache_ttl: { min: 0, max: 2_592_000 },
  spend_limit: { min: 1, max: 10_000_000 },
};

/** What `GET /api/config` answers with, with whatever this spec is about on top. */
export function defaults(overrides: Partial<AgentDefaults> = {}): AgentDefaults {
  return {
    provider: OLLAMA.name,
    provider_options: [OLLAMA, VLLM],
    model: OLLAMA.model,
    base_url: OLLAMA.base_url,
    temperature: 0,
    num_ctx: null,
    model_timeout: 600,
    think: null,
    cpu_only: false,
    results: 10,
    top: 3,
    max_price: null,
    min_rating: null,
    min_reviews: null,
    cache_ttl: 86400,
    region: 'us-en',
    currency: '',
    currency_options: ['EUR', 'GBP', 'PLN', 'USD'],
    backend: DDG.name,
    backend_options: [DDG, SEARXNG, BRAVE],
    sources: '',
    fetch: true,
    pay: false,
    pay_available: true,
    rail: DRY_RUN.name,
    rail_options: [DRY_RUN, CHARGES],
    merchant_url: '',
    spend_limit: null,
    sort_by: 'score',
    sort_options: ['score', 'price', 'rating'],
    limits: LIMITS,
    ...overrides,
  };
}

/** What `GET /api/models` answers with for a server that was reachable. */
export function status(overrides: Partial<ModelStatus> = {}): ModelStatus {
  return {
    provider: OLLAMA.name,
    label: OLLAMA.label,
    base_url: OLLAMA.base_url,
    reachable: true,
    models: [{ name: OLLAMA.model, completion: true }],
    ...overrides,
  };
}

/** What came of a payment -- the dry run's, which charged nobody. */
export function receipt(overrides: Partial<Receipt> = {}): Receipt {
  return {
    paid: false,
    rail: DRY_RUN.name,
    merchant: 'Amazon',
    title: 'Sony WH-1000XM5',
    price: 328,
    currency: 'USD',
    amount: 32800,
    price_label: '328.00 USD',
    transaction_id: 'tx',
    reference: 'ref-abc',
    autonomous: false,
    enrolled_key: false,
    detail: 'Nothing was charged.',
    ...overrides,
  };
}

/** One ranked product, whole, with whatever the spec is about on top. Here
 *  rather than in either spec that needs one: `RankedProduct` mirrors
 *  `product_payload` field for field, so a field added there is added once. */
export function product(overrides: Partial<RankedProduct> = {}): RankedProduct {
  return {
    rank: 1,
    score: 0.912,
    breakdown: {
      rating: 0.94,
      popularity: 1,
      price: 0.32,
      total: 0.912,
      neutral: [],
    },
    name: 'Sony WH-1000XM5',
    price: 328,
    currency: 'USD',
    rating: 4.7,
    review_count: 12000,
    seller: 'Amazon',
    url: 'https://www.example.com/sony',
    notes: 'Best noise cancelling.',
    opinions: [
      { text: 'the noise cancelling is uncanny', url: 'https://www.example.com/sony' },
      { text: 'the case is bulky', url: 'https://audiosite.example/xm5' },
    ],
    price_label: '328.00 USD',
    rating_label: '4.7/5 (12,000 reviews)',
    cannot_pay: null,
    pay_currency: 'USD',
    pay_label: '328.00 USD',
    pay_merchant: 'Amazon',
    ...overrides,
  };
}
