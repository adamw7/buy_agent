import { Component, computed, effect, input, output, signal, untracked } from '@angular/core';
import type { WritableSignal } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { FormsModule } from '@angular/forms';

import type {
  AgentDefaults,
  BackendOption,
  BoundsCheck,
  Limit,
  ModelSource,
  ModelStatus,
  ProviderOption,
  RailOption,
  SearchOptions,
  SortBy,
  SourcesCheck,
} from '../agent.types';

/** The model's thinking mode as a `<select>` can hold it. */
type Thinking = 'on' | 'off';

/** One entry in the model dropdown, and what is wrong with picking it. */
interface ModelOption {
  name: string;
  note: string;
}

/** A value the server refused, and the box it came out of. */
export interface Rejection {
  field: string;
  message: string;
}

/** A number box's one key: sent, ranged, refused, seeded and placeholdered under it.
 *  Narrowed to keys whose default is a number, so a wrong box does not compile. */
type NumberKey = {
  [K in keyof AgentDefaults & keyof SearchOptions]: AgentDefaults[K] extends number | null
    ? K
    : never;
}[keyof AgentDefaults & keyof SearchOptions];

/** One number box, as the template draws it and the checks read it. */
interface NumberField {
  key: NumberKey;
  label: string;
  value: WritableSignal<number | null>;
  step: number;
  hint: () => string;
  off: () => boolean;
  /** Whether this box is drawn in the paying block, shown only once paying is ticked. */
  paying: boolean;
  /** Whether a cleared box is remembered (true for bounds, ADR-0039, and `num_ctx`). */
  remembersBlank: boolean;
}

/** One row of the table above, with the defaults most of them take. */
function field(
  key: NumberField['key'],
  label: string,
  value: WritableSignal<number | null>,
  extra: {
    step?: number;
    hint?: string | (() => string);
    off?: () => boolean;
    paying?: boolean;
    remembersBlank?: boolean;
  } = {},
): NumberField {
  const hint = extra.hint ?? '';
  return {
    key,
    label,
    value,
    step: extra.step ?? 1,
    hint: typeof hint === 'string' ? () => hint : hint,
    off: extra.off ?? (() => false),
    paying: extra.paying ?? false,
    remembersBlank: extra.remembersBlank ?? true,
  };
}

const SETTINGS_KEY = 'buy_agent.settings';

/** What a cleared bound box falls back to, said in the box. */
const NO_LIMIT = 'No limit';

const EXAMPLES = [
  'wireless noise cancelling headphones under $200',
  'gaming laptop under $1500',
  'espresso machine for a small kitchen',
  'running shoes for flat feet',
];

/** What to shop for, and the settings the CLI takes as flags. */
@Component({
  selector: 'app-search-form',
  imports: [FormsModule, NgTemplateOutlet],
  templateUrl: './search-form.html',
  styleUrl: './search-form.css',
})
export class SearchForm {
  readonly defaults = input<AgentDefaults | null>(null);
  readonly status = input<ModelStatus | null>(null);
  readonly running = input(false);
  /** Whether the model list currently on screen is being replaced. */
  readonly checking = input(false);
  /** What the server made of the sources field, last time it was asked. */
  readonly checked = input<SourcesCheck | null>(null);
  /** A value a run was refused for, to mark beside the field it came from. */
  readonly rejected = input<Rejection | null>(null);
  /** Bounds the request states in words: offered, never applied, never a mark (ADR-0059). */
  readonly noticed = input<BoundsCheck | null>(null);

  /** Not `search`, which a native DOM event would also answer. */
  readonly run = output<SearchOptions>();
  readonly stop = output<void>();
  /** Ask what another server is serving, when the provider or the address changes. */
  readonly refresh = output<ModelSource>();
  /** Ask whether the sources field names sources. */
  readonly check = output<string>();
  /** Ask what the request itself says about the bounds. */
  readonly read = output<string>();
  /** The refused box now holds something else, so the banner repeating the refusal
   *  should go with the mark (see `notes`). */
  readonly moved = output<void>();

  protected readonly examples = EXAMPLES;

  protected readonly request = signal('');
  protected readonly provider = signal('ollama');
  protected readonly model = signal('');
  protected readonly baseUrl = signal('');
  protected readonly region = signal('us-en');
  protected readonly currency = signal('');
  protected readonly backend = signal('ddg');
  protected readonly sources = signal('');
  // Null is a cleared box: the default (ADR-0012), or no bound (ADR-0039).
  protected readonly results = signal<number | null>(10);
  protected readonly top = signal<number | null>(3);
  protected readonly maxPrice = signal<number | null>(null);
  protected readonly minRating = signal<number | null>(null);
  protected readonly minReviews = signal<number | null>(null);
  protected readonly cacheTtl = signal<number | null>(null);
  protected readonly sortBy = signal<SortBy>('score');
  protected readonly temperature = signal<number | null>(0);
  protected readonly numCtx = signal<number | null>(null);
  protected readonly modelTimeout = signal<number | null>(null);
  protected readonly thinking = signal<Thinking>('off');
  protected readonly cpuOnly = signal(false);
  protected readonly fetchPages = signal(true);
  protected readonly journal = signal(true);
  // Paying, and who through.
  protected readonly pay = signal(false);
  protected readonly rail = signal('dry-run');
  protected readonly merchantUrl = signal('');
  protected readonly spendLimit = signal<number | null>(null);
  protected readonly advanced = signal(false);

  /** The number boxes holding something that is not a number, by their key. */
  private readonly unreadable = signal<Record<string, boolean>>({});

  /** The settings a run was actually started with, for as long as they stand. */
  private readonly submitted = signal<SearchOptions | null>(null);

  /** Every number field, in the order the form draws them. */
  protected readonly numberFields: NumberField[] = [
    // Each bound says what it does with a figure no page printed: it keeps the product
    // (ADR-0039), and a "price unknown" in a run capped at 10 reads as a broken cap.
    field('max_price', 'Max price', this.maxPrice, {
      step: 0.01,
      // Name the scale it is read in.
      hint: () => `In ${this.scale()}; nothing is converted. Unpriced products are still shown.`,
    }),
    field('min_rating', 'Min rating', this.minRating, {
      step: 0.1,
      hint: 'Out of 5. Unrated products are still shown.',
    }),
    field('min_reviews', 'Min reviews', this.minReviews, {
      hint: 'How many reviews a rating has to average. Products with no count are still shown.',
    }),
    field('results', 'Products to find', this.results, { remembersBlank: false }),
    field('top', 'Products to highlight', this.top, { remembersBlank: false }),
    field('temperature', 'Temperature', this.temperature, { step: 0.1, remembersBlank: false }),
    field('num_ctx', 'Context window', this.numCtx, {
      hint: () =>
        this.takesNumCtx()
          ? 'Thinking models need the room to answer; the default leaves it.'
          : `${this.providerLabel()} is started with the window it serves, so this is not a per-run setting there.`,
      off: () => !this.takesNumCtx(),
    }),
    field('model_timeout', 'Wait for the model', this.modelTimeout, {
      hint: 'Seconds to wait for one answer. Asked once, so this is the whole wait.',
    }),
    field('cache_ttl', 'Cache pages for', this.cacheTtl, {
      hint: 'Seconds a page, and the answer about it, stay usable. 0 is off.',
    }),
    field('spend_limit', 'Spend limit', this.spendLimit, {
      step: 0.01,
      // Only drawn while paying, so no "off" wording is needed.
      hint: () =>
        `The most one payment may be, in ${this.scale()}. A price in another currency is refused, not passed.`,
      off: () => !this.pay(),
      paying: true,
    }),
  ];

  /** The table above, split by where each box is drawn. */
  protected readonly settingFields = this.numberFields.filter((row) => !row.paying);
  protected readonly payingFields = this.numberFields.filter((row) => row.paying);

  /** The settings that are seeded from the server, remembered, and restored. */
  private readonly settings: Record<string, Setting> = {
    // Saved in one blob with its model and address, so they restore as a pair.
    provider: setting(
      this.provider,
      (d) => d.provider,
      amongst((d) => d.provider_options.map((option) => option.name)),
    ),
    model: setting(this.model, (d) => d.model, asText),
    baseUrl: setting(this.baseUrl, (d) => d.base_url, asText),
    region: setting(this.region, (d) => d.region, asText),
    // Checked against what the server offers, like `provider` and `rail`.
    currency: setting(
      this.currency,
      (d) => d.currency,
      // Blank is a value: the pages' vote.
      amongst((d) => ['', ...d.currency_options]),
    ),
    backend: setting(
      this.backend,
      (d) => d.backend,
      amongst((d) => d.backend_options.map((option) => option.name)),
    ),
    sources: setting(this.sources, (d) => d.sources, asText),
    // Every number box, off the table above.
    ...numberSettings(this.numberFields),
    // Checked, not cast: the server may change `SortBy`.
    sortBy: setting(
      this.sortBy,
      (d) => d.sort_by,
      amongst<SortBy>((d) => d.sort_options),
    ),
    thinking: setting(this.thinking, (d) => toThinking(d.think), asThinking),
    cpuOnly: setting(this.cpuOnly, (d) => d.cpu_only, asBoolean),
    fetchPages: setting(this.fetchPages, (d) => d.fetch, asBoolean),
    journal: setting(this.journal, (d) => d.journal, asBoolean),
    // Checked against what the server offers, like `provider`.
    rail: setting(
      this.rail,
      (d) => d.rail,
      amongst((d) => d.rail_options.map((option) => option.name)),
    ),
    merchantUrl: setting(this.merchantUrl, (d) => d.merchant_url, asText),
  };

  /** Each criterion named by the order it puts a run in: "price" alone cannot say
   *  cheapest from dearest. By name only until the defaults have said. */
  protected readonly sortOptions = computed(() => {
    const defaults = this.defaults();
    const names: SortBy[] = defaults?.sort_options ?? ['score', 'price', 'rating'];
    // A server older than the page -- a build under one still running -- sends none.
    return names.map((name) => ({ name, label: defaults?.sort_labels?.[name] ?? name }));
  });

  protected readonly providerOptions = computed<ProviderOption[]>(
    () => this.defaults()?.provider_options ?? [],
  );

  protected readonly railOptions = computed<RailOption[]>(
    () => this.defaults()?.rail_options ?? [],
  );

  /** The currency the form's amounts are read in. */
  protected readonly scale = computed(
    () => this.currency() || 'the currency most of the pages quote',
  );

  protected readonly currencyOptions = computed<string[]>(
    () => this.defaults()?.currency_options ?? [],
  );

  protected readonly backendOptions = computed<BackendOption[]>(
    () => this.defaults()?.backend_options ?? [],
  );

  /** The chosen backend's row. */
  protected readonly chosenBackend = computed<BackendOption | undefined>(() =>
    this.backendOptions().find((option) => option.name === this.backend()),
  );

  /** The backend field's hint; `configured` is Python's verdict. */
  protected readonly backendHint = computed(() => {
    const option = this.chosenBackend();
    if (!option) {
      return '';
    }
    if (!option.configured) {
      return `${option.label} needs a key this server does not have, so a run through it will fail.`;
    }
    return option.endpoint
      ? `Asked at ${option.endpoint}.`
      : `${option.label} needs no server of your own and rate-limits heavy use.`;
  });

  /** The chosen rail's row. */
  protected readonly chosenRail = computed<RailOption | undefined>(() =>
    this.railOptions().find((option) => option.name === this.rail()),
  );

  /** Whether the optional AP2 SDK is installed on the server at all. */
  protected readonly payAvailable = computed(() => this.defaults()?.pay_available ?? false);

  /** Whether this rail can charge anybody. */
  protected readonly railSpends = computed(() => this.chosenRail()?.moves_money ?? false);

  /** Whether the address field is a setting on this rail at all. */
  protected readonly railNeedsEndpoint = computed(() => this.chosenRail()?.needs_endpoint ?? false);

  /** The chosen provider's row; absent until the defaults land. */
  protected readonly chosenProvider = computed<ProviderOption | undefined>(() =>
    this.providerOptions().find((option) => option.name === this.provider()),
  );

  /** What to call this server on screen -- "Ollama", "vLLM", "LiteLLM". */
  protected readonly providerLabel = computed(
    () => this.chosenProvider()?.label ?? this.provider(),
  );

  /** Whether the context window is a per-run setting at all. */
  protected readonly takesNumCtx = computed(() => this.chosenProvider()?.takes_num_ctx ?? true);

  /** Whether keeping the model off the GPU is a per-run setting at all. */
  protected readonly takesCpuOnly = computed(() => this.chosenProvider()?.takes_cpu_only ?? true);

  /** The CPU-only box's hint. */
  protected readonly cpuOnlyHint = computed(() =>
    this.takesCpuOnly()
      ? 'Slower, but it leaves the card free and runs a model too large to fit on it.'
      : `With ${this.providerLabel()} the device is chosen where the model is served, so this is not a per-run setting there.`,
  );

  /** What a cleared context window falls back to. */
  protected readonly numCtxHint = computed(() => {
    if (!this.takesNumCtx()) {
      return `Fixed where ${this.providerLabel()}'s model is served`;
    }
    const fallback = this.defaults()?.num_ctx;
    return fallback ? `The default (${fallback})` : "Ollama's own (4096)";
  });

  /** The model dropdown: what the server reported, plus the chosen name if missing,
   *  each marked with what is wrong with it (ADR-0032). */
  protected readonly modelOptions = computed<ModelOption[]>(() => {
    const installed = this.status()?.models ?? [];
    if (!installed.length) {
      return [];
    }
    const options = installed.map((model) => ({
      name: model.name,
      note: model.completion ? '' : ' — embedding only',
    }));
    const chosen = this.model().trim();
    if (chosen && !installed.some((model) => model.name === chosen)) {
      options.unshift({ name: chosen, note: ' — not served' });
    }
    return options;
  });

  /** The server's ranges by key; typed with `undefined` for unbounded fields. */
  protected readonly limits = computed<Record<string, Limit | undefined>>(
    () => this.defaults()?.limits ?? {},
  );

  /** What the page can tell is wrong with each field, by key (ADR-0033). */
  protected readonly problems = computed<Record<string, string>>(() => {
    const problems: Record<string, string> = {};
    const limits = this.limits();
    const unreadable = this.unreadable();
    for (const { key, value: held, off } of this.numberFields) {
      // A disabled box is not sent, so it is not marked.
      if (off()) {
        continue;
      }
      const limit = limits[key];
      const value = held();
      // Text in a number box reads as `null`, which must not pass as cleared.
      if (unreadable[key]) {
        problems[key] = 'That is not a number. Clear the box to use the default.';
      } else if (limit && value !== null && (value < limit.min || value > limit.max)) {
        // A cleared box is the default, not a number to range-check (ADR-0012).
        problems[key] = `Between ${limit.min} and ${limit.max}.`;
      }
    }
    const sources = this.sourcesProblem();
    if (sources) {
      problems['sources'] = sources;
    }
    return problems;
  });

  /** The noticed bounds, while they are about the request now in the box. */
  private readonly noticedNow = computed(() => {
    const check = this.noticed();
    return check && check.request === this.request().trim() ? check.noticed : [];
  });

  /** Python's note under a box the request filled in; a hint, not a mark. */
  protected noticedNote(key: string): string {
    const offer = this.noticedNow().find((bound) => bound.bound === key);
    // Only while the box still holds that figure.
    const row = this.numberFields.find((field) => field.key === key);
    return offer && row?.value() === offer.value ? offer.note : '';
  }

  /** Offers already made, as `key=figure`, so a box the shopper cleared stays clear. */
  private readonly offered = new Set<string>();

  /** Boxes this form filled and nobody touched since, by key: they follow the request. */
  private readonly filled = new Map<string, number>();

  /** The server's verdict on the sources field, while it is about what the field holds. */
  private readonly sourcesProblem = computed(() => {
    const checked = this.checked();
    return checked && checked.sources === this.sources().trim() ? checked.error : '';
  });

  /** What to show under each field: the page's problems, else the server's refusal. */
  protected readonly notes = computed<Record<string, string>>(() => {
    const problems = this.problems();
    const rejected = this.rejected();
    if (!rejected || problems[rejected.field] || !this.stillSent(rejected.field)) {
      return problems;
    }
    return { ...problems, [rejected.field]: rejected.message };
  });

  /** The id of the sentence marking this box, or null: both the sentence's `id` and
   *  the box's `aria-describedby`, so they never disagree (ADR-0033). */
  protected problemId(key: string): string | null {
    return this.notes()[key] ? `problem-${key}` : null;
  }

  /** Whether the field named still holds the value the run was refused for. */
  private stillSent(field: string): boolean {
    const sent = this.submitted();
    if (!sent || !(field in sent)) {
      return true;
    }
    return this.options()[field as keyof SearchOptions] === sent[field as keyof SearchOptions];
  }

  /** How many settings are marked, for the summary. */
  protected readonly flagged = computed(() => Object.keys(this.notes()).length);

  /** Each number box's placeholder: its default, or "No limit" for a bound. */
  protected readonly placeholders = computed<Record<string, string | undefined>>(() => {
    const named: Record<string, string> = {};
    const defaults = this.defaults();
    for (const { key } of this.numberFields) {
      // Read off the defaults under the box's own key.
      const fallback = defaults?.[key];
      if (typeof fallback === 'number') {
        named[key] = `${fallback}`;
      } else if (fallback === null) {
        // A bound, whose default is none (ADR-0039).
        named[key] = NO_LIMIT;
      }
    }
    // Per provider, so a sentence rather than a number.
    named['num_ctx'] = this.numCtxHint();
    return named;
  });

  /** A request, and no field the server would refuse. */
  protected readonly canSubmit = computed(
    () => this.request().trim().length > 0 && Object.keys(this.problems()).length === 0,
  );

  constructor() {
    // Seed the fields when the server's defaults land.
    effect(() => {
      const defaults = this.defaults();
      if (defaults) {
        untracked(() => this.seed(defaults));
      }
    });

    // Open the settings the first time there is something in them to read.
    effect(() => {
      if (this.flagged()) {
        this.advanced.set(true);
      }
    });

    // Fill a noticed bound into an empty box, once, and open the panel (ADR-0059). A
    // box the form filled follows the request; one the shopper touched is theirs.
    effect(() => {
      const check = this.noticed();
      const answered = check !== null && check.request === this.request().trim();
      const offers = this.noticedNow();
      untracked(() => {
        for (const [key, value] of this.filled) {
          const row = this.numberFields.find((field) => field.key === key);
          if (row?.value() !== value) {
            this.filled.delete(key);
          } else if (answered && !offers.some((bound) => bound.bound === key)) {
            // Cleared by the form, so it may be offered again.
            row.value.set(null);
            this.filled.delete(key);
            this.offered.delete(`${key}=${value}`);
          }
        }
        for (const bound of offers) {
          const row = this.numberFields.find((field) => field.key === bound.bound);
          const mark = `${bound.bound}=${bound.value}`;
          if (!row) {
            continue;
          }
          // A box the form still owns takes whatever the request now asks for.
          const owned = this.filled.has(bound.bound);
          if (!owned && this.offered.has(mark)) {
            continue;
          }
          this.offered.add(mark);
          if (owned || row.value() === null) {
            row.value.set(bound.value);
            this.filled.set(bound.bound, bound.value);
            this.advanced.set(true);
          }
        }
      });
    });

    // Re-runs on any field change, via `stillSent`.
    effect(() => {
      const rejected = this.rejected();
      if (rejected && !this.stillSent(rejected.field)) {
        this.moved.emit();
      }
    });
  }

  /** Fill the form from the server's defaults, then let anything remembered win. */
  private seed(defaults: AgentDefaults): void {
    for (const field of Object.values(this.settings)) {
      field.seed(defaults);
    }
    this.restore(defaults);
    // Check remembered sources now; nobody will type them to trigger it.
    this.sourcesChanged();
  }

  /** Track the reader opening or shutting the panel. */
  protected toggled(event: Event): void {
    this.advanced.set((event.target as HTMLDetailsElement).open);
  }

  protected submit(): void {
    if (!this.canSubmit() || this.running()) {
      return;
    }
    this.remember();
    const options = this.options();
    // So a refusal can be dropped once its field changes.
    this.submitted.set(options);
    this.run.emit(options);
  }

  /** Every setting as a run would be asked for it. */
  private options(): SearchOptions {
    return {
      // Every number box, off the one table.
      ...this.numbers(),
      request: this.request().trim(),
      provider: this.provider(),
      model: this.model().trim(),
      base_url: this.baseUrl().trim(),
      region: this.region().trim(),
      currency: this.currency(),
      backend: this.backend(),
      sources: this.sources().trim(),
      sort_by: this.sortBy(),
      think: fromThinking(this.thinking()),
      // Left out where the server fixes its own device.
      cpu_only: this.takesCpuOnly() ? this.cpuOnly() : undefined,
      fetch: this.fetchPages(),
      journal: this.journal(),
      pay: this.pay(),
      rail: this.rail(),
      merchant_url: this.merchantUrl().trim(),
    };
  }

  /** What the number boxes are sent as: nothing, for one this run does not take. */
  private numbers(): Pick<SearchOptions, NumberField['key']> {
    return Object.fromEntries(
      this.numberFields.map((row) => [row.key, row.off() ? null : row.value()]),
    );
  }

  /** A number box was typed into: ask the element whether it can read it. */
  protected numberTyped(key: string, event: Event): void {
    const input = event.target as HTMLInputElement;
    const bad = input.validity?.badInput ?? false;
    this.unreadable.update((held) => (held[key] === bad ? held : { ...held, [key]: bad }));
  }

  protected useExample(example: string): void {
    this.request.set(example);
    // The same reading a typed request gets: an example is the request now.
    this.requestChanged();
  }

  /** Another provider was picked: its model and its address come with it. */
  protected providerChanged(): void {
    const option = this.chosenProvider();
    if (option) {
      this.model.set(option.model);
      this.baseUrl.set(option.base_url);
    }
    this.serverChanged();
  }

  /** Another rail was picked: its address comes with it. */
  protected railChanged(): void {
    const option = this.chosenRail();
    if (option) {
      this.merchantUrl.set(option.endpoint);
    }
  }

  /** The request was typed and left: ask the server what it asks for in words. */
  protected requestChanged(): void {
    this.read.emit(this.request().trim());
  }

  /** The sources field was left: ask the server what it makes of what it holds. */
  protected sourcesChanged(): void {
    this.check.emit(this.sources().trim());
  }

  /** The server field was left: whatever that one is serving is a new list. */
  protected serverChanged(): void {
    const url = this.baseUrl().trim();
    if (url) {
      this.refresh.emit({ provider: this.provider(), base_url: url });
    }
  }

  /** Advanced settings only: what to shop for is a new question every time. */
  private remember(): void {
    const saved: Record<string, unknown> = {};
    for (const [key, field] of Object.entries(this.settings)) {
      saved[key] = field.value();
    }
    // A figure read off this request is part of the question, not a standing answer.
    for (const [key, value] of this.filled) {
      const name = camelCase(key);
      if (saved[name] === value) {
        saved[name] = null;
      }
    }
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(saved));
    } catch {
      // A browser that refuses storage still gets a working form.
    }
  }

  /** Let anything this browser remembered win over the seeded defaults. */
  private restore(defaults: AgentDefaults): void {
    let saved: unknown;
    try {
      saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? 'null') ?? {};
    } catch {
      return;
    }
    if (typeof saved !== 'object' || saved === null) {
      return;
    }
    for (const [key, field] of Object.entries(this.settings)) {
      if (key in saved) {
        field.restore((saved as Record<string, unknown>)[key], defaults);
      }
    }
  }
}

/** Reads one remembered value, or undefined for anything it will not take. */
type Parser<T> = (raw: unknown, defaults: AgentDefaults) => T | undefined;

/** One remembered setting, with the signal's own type closed over. */
interface Setting {
  seed(defaults: AgentDefaults): void;
  value(): unknown;
  restore(raw: unknown, defaults: AgentDefaults): void;
}

function setting<T>(
  target: WritableSignal<T>,
  fromDefaults: (defaults: AgentDefaults) => T,
  parse: Parser<T>,
): Setting {
  return {
    seed: (defaults) => target.set(fromDefaults(defaults)),
    value: () => target(),
    restore: (raw, defaults) => {
      const parsed = parse(raw, defaults);
      if (parsed !== undefined) {
        target.set(parsed);
      }
    },
  };
}

/** The number boxes as remembered settings, seeded by their key and stored under its
 *  camel case. */
function numberSettings(fields: readonly NumberField[]): Record<string, Setting> {
  return Object.fromEntries(
    fields.map((row) => [
      camelCase(row.key),
      setting<number | null>(
        row.value,
        (defaults) => defaults[row.key],
        row.remembersBlank ? asNumberOrNull : asNumber,
      ),
    ]),
  );
}

/** `max_price` -> `maxPrice`: a request key as this file names the signal for it. */
function camelCase(key: string): string {
  return key.replace(/_(\w)/g, (_, letter: string) => letter.toUpperCase());
}

const asText: Parser<string> = (raw) => (typeof raw === 'string' ? raw : undefined);
const asNumber: Parser<number> = (raw) => (typeof raw === 'number' ? raw : undefined);
const asBoolean: Parser<boolean> = (raw) => (typeof raw === 'boolean' ? raw : undefined);
const asNumberOrNull: Parser<number | null> = (raw) =>
  raw === null || typeof raw === 'number' ? raw : undefined;

/** A remembered name the server still offers, and nothing else. */
function amongst<T extends string>(
  offered: (defaults: AgentDefaults) => readonly string[],
): Parser<T> {
  return (raw, defaults) =>
    typeof raw === 'string' && offered(defaults).includes(raw) ? (raw as T) : undefined;
}

/** Validated, so an old remembered `'default'` is ignored. */
const asThinking: Parser<Thinking> = (raw) => (raw === 'on' || raw === 'off' ? raw : undefined);

/** `null` seeds `off`, which is what the server does with it anyway. */

function toThinking(value: boolean | null): Thinking {
  return value ? 'on' : 'off';
}

function fromThinking(value: Thinking): boolean {
  return value === 'on';
}
