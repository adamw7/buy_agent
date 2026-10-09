import {
  Component,
  ElementRef,
  Injector,
  afterNextRender,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  untracked,
} from '@angular/core';
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

type Thinking = 'on' | 'off';

/** A model dropdown entry, and what is wrong with picking it. */
interface ModelOption {
  name: string;
  note: string;
}

/** A value the server refused, and the box it came out of. */
export interface Rejection {
  field: string;
  message: string;
  /** A payment's refusal: what it sent for the field, and through which rail. It is held
   *  against those, never against the run's settings. */
  payment?: { sent: unknown; rail: string };
}

/** What a Pay button pays with: the paying settings as they stand now. */
export type PaySettings = Required<
  Pick<SearchOptions, 'pay' | 'rail' | 'merchant_url' | 'spend_limit'>
> & {
  /** The marked paying setting holding payment back, or null: an unreadable spend limit
   *  reads as null, which the server takes for no limit. */
  held: string | null;
};

/** The paying boxes, by request key: where a payment's refusal is marked. */
const PAYING_BOXES: Record<string, string> = {
  spend_limit: 'Spend limit',
  merchant_url: 'Payment endpoint',
};

const PAYING_KEYS = Object.keys(PAYING_BOXES);

/** Keys whose default is a number, so a wrong box does not compile. */
type NumberKey = {
  [K in keyof AgentDefaults & keyof SearchOptions]: AgentDefaults[K] extends number | null
    ? K
    : never;
}[keyof AgentDefaults & keyof SearchOptions];

interface NumberField {
  key: NumberKey;
  label: string;
  value: WritableSignal<number | null>;
  step: number;
  hint: () => string;
  off: () => boolean;
  /** Drawn in the paying block. */
  paying: boolean;
  /** Kept for the next visit; never a bound (ADR-0077). */
  remembered: boolean;
  /** Remembered cleared, rather than coming back as the default. */
  remembersBlank: boolean;
}

function field(
  key: NumberField['key'],
  label: string,
  value: WritableSignal<number | null>,
  extra: {
    step?: number;
    hint?: string | (() => string);
    off?: () => boolean;
    paying?: boolean;
    remembered?: boolean;
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
    remembered: extra.remembered ?? true,
    remembersBlank: extra.remembersBlank ?? true,
  };
}

const SETTINGS_KEY = 'buy_agent.settings';

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
  /** Whether the model list on screen is being replaced. */
  readonly checking = input(false);
  readonly checked = input<SourcesCheck | null>(null);
  readonly rejected = input<Rejection | null>(null);
  /** The results' currency, which a payment is made in (ADR-0056). */
  readonly countedIn = input<string | null>(null);
  /** Bounds the request states in words: offered, never applied (ADR-0059). */
  readonly noticed = input<BoundsCheck | null>(null);

  /** Not `search`, which a native DOM event would also answer. */
  readonly run = output<SearchOptions>();
  readonly stop = output<void>();
  readonly refresh = output<ModelSource>();
  readonly check = output<string>();
  readonly read = output<string>();
  /** The refused box holds something else now, so the banner goes with the mark. */
  readonly moved = output<void>();
  /** The paying settings on each change: paying runs no pipeline (ADR-0046). */
  readonly payWith = output<PaySettings>();

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
  protected readonly alertBelow = signal<number | null>(null);
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

  private readonly unreadable = signal<Record<string, boolean>>({});

  /** The settings a run was started with. */
  private readonly submitted = signal<SearchOptions | null>(null);

  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);

  /** The server is still reading the request, so a submit waits for it (ADR-0059). */
  private reading = false;

  /** Tells a reading landing from the request being typed into. */
  private lastReading: BoundsCheck | null = null;

  /** A submit waiting on that reading, by its request. */
  private readonly held = signal<string | null>(null);

  /** The box a held submit stopped at, which says so until something is sent. */
  protected readonly stoppedAt = signal<NumberKey | null>(null);

  /** Every number field, in the order the form draws them. */
  protected readonly numberFields: NumberField[] = [
    // A bound keeps a product with no figure (ADR-0039), and is never remembered (ADR-0077).
    field('max_price', 'Max price', this.maxPrice, {
      step: 0.01,
      hint: () => `In ${this.scale()}; nothing is converted. Unpriced products are still shown.`,
      remembered: false,
    }),
    field('min_rating', 'Min rating', this.minRating, {
      step: 0.1,
      hint: 'Out of 5. Unrated products are still shown.',
      remembered: false,
    }),
    field('min_reviews', 'Min reviews', this.minReviews, {
      hint: 'How many reviews a rating has to average. Products with no count are still shown.',
      remembered: false,
    }),
    // Told, never applied, and asked of one search as a bound is (ADR-0080).
    field('alert_below', 'Price alert', this.alertBelow, {
      step: 0.01,
      hint: () =>
        `In ${this.scale()}. Says whether anything in stock is at or under it; nothing is removed.`,
      remembered: false,
    }),
    field('results', 'Products to find', this.results, { remembersBlank: false }),
    field('top', 'Products to highlight', this.top, { remembersBlank: false }),
    field('temperature', 'Temperature', this.temperature, {
      step: 0.1,
      hint: 'Above 0 answers can vary from run to run and are never cached.',
      remembersBlank: false,
    }),
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
      hint: 'Seconds a page, and the answer about it, stay usable: 86400 is a day, 0 is off.',
    }),
    field('spend_limit', 'Spend limit', this.spendLimit, {
      step: 0.01,
      // In the results' currency once there are any: what a payment is checked in.
      hint: () =>
        `The most one payment may be, in ${this.countedIn() ?? this.scale()}. A price in another currency is refused, not passed.`,
      off: () => !this.pay(),
      paying: true,
    }),
  ];

  protected readonly settingFields = this.numberFields.filter((row) => !row.paying);
  protected readonly payingFields = this.numberFields.filter((row) => row.paying);

  /** Seeded from the server, and all but the bounds remembered (ADR-0077). */
  private readonly settings: Record<string, Setting> = {
    provider: setting(
      this.provider,
      (d) => d.provider,
      amongst((d) => d.provider_options.map((option) => option.name)),
    ),
    model: setting(this.model, (d) => d.model, asText),
    baseUrl: setting(this.baseUrl, (d) => d.base_url, asText),
    region: setting(this.region, (d) => d.region, asText),
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
    rail: setting(
      this.rail,
      (d) => d.rail,
      amongst((d) => d.rail_options.map((option) => option.name)),
    ),
    merchantUrl: setting(this.merchantUrl, (d) => d.merchant_url, asText),
  };

  /** Each criterion named by the order it gives ("cheapest first"). */
  protected readonly sortOptions = computed<{ name: SortBy; label: string }[]>(() => {
    const defaults = this.defaults();
    const names: SortBy[] = defaults?.sort_options ?? ['score', 'price', 'rating'];
    // A server older than the page sends no labels.
    return names.map((name) => ({ name, label: defaults?.sort_labels?.[name] ?? name }));
  });

  protected readonly providerOptions = computed<ProviderOption[]>(
    () => this.defaults()?.provider_options ?? [],
  );

  protected readonly railOptions = computed<RailOption[]>(
    () => this.defaults()?.rail_options ?? [],
  );

  protected readonly scale = computed(
    () => this.currency() || 'the currency most of the pages quote',
  );

  protected readonly currencyOptions = computed<string[]>(
    () => this.defaults()?.currency_options ?? [],
  );

  protected readonly backendOptions = computed<BackendOption[]>(
    () => this.defaults()?.backend_options ?? [],
  );

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

  protected readonly chosenRail = computed<RailOption | undefined>(() =>
    this.railOptions().find((option) => option.name === this.rail()),
  );

  /** Whether the server has the optional AP2 SDK. */
  protected readonly payAvailable = computed(() => this.defaults()?.pay_available ?? false);

  /** As a payment sends them: no spend limit while paying is off. */
  private readonly payingValues = computed(() => ({
    pay: this.pay(),
    rail: this.rail(),
    merchant_url: this.merchantUrl().trim(),
    spend_limit: this.pay() ? this.spendLimit() : null,
  }));

  /** Those, and what holds paying back; apart, since the marks read the values. */
  private readonly paying = computed<PaySettings>(() => {
    const notes = this.notes();
    const marked = PAYING_KEYS.find((key) => notes[key]);
    return {
      ...this.payingValues(),
      held: marked ? `Paying waits on ${PAYING_BOXES[marked]}: ${notes[marked]}` : null,
    };
  });

  protected readonly railSpends = computed(() => this.chosenRail()?.moves_money ?? false);

  protected readonly railNeedsEndpoint = computed(() => this.chosenRail()?.needs_endpoint ?? false);

  protected readonly chosenProvider = computed<ProviderOption | undefined>(() =>
    this.providerOptions().find((option) => option.name === this.provider()),
  );

  protected readonly providerLabel = computed(
    () => this.chosenProvider()?.label ?? this.provider(),
  );

  protected readonly takesNumCtx = computed(() => this.chosenProvider()?.takes_num_ctx ?? true);

  protected readonly takesCpuOnly = computed(() => this.chosenProvider()?.takes_cpu_only ?? true);

  protected readonly cpuOnlyHint = computed(() =>
    this.takesCpuOnly()
      ? 'Slower, but it leaves the card free and runs a model too large to fit on it.'
      : `With ${this.providerLabel()} the device is chosen where the model is served, so this is not a per-run setting there.`,
  );

  /** What a cleared context window falls back to, or what a switched-off box says:
   *  short, since it is read in the box at the grid's narrowest column. */
  protected readonly numCtxHint = computed(() => {
    if (!this.takesNumCtx()) {
      return 'Set by the server';
    }
    const fallback = this.defaults()?.num_ctx;
    return fallback ? `The default (${fallback})` : "Ollama's own (4096)";
  });

  /** What the server reported, plus the chosen name if missing, marked (ADR-0032). */
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

  protected readonly limits = computed<Record<string, Limit | undefined>>(
    () => this.defaults()?.limits ?? {},
  );

  /** What the page can tell is wrong with each field, by key (ADR-0033). */
  protected readonly problems = computed<Record<string, string>>(() => {
    const problems: Record<string, string> = {};
    const limits = this.limits();
    const unreadable = this.unreadable();
    for (const { key, value: held, off } of this.numberFields) {
      // Not sent, so not marked.
      if (off()) {
        continue;
      }
      const limit = limits[key];
      const value = held();
      // Text in a number box reads as `null`, which must not pass as cleared.
      if (unreadable[key]) {
        problems[key] = 'That is not a number. Clear the box to use the default.';
      } else if (limit && value !== null && (value < limit.min || value > limit.max)) {
        problems[key] = `Between ${limit.min} and ${limit.max}.`;
      }
    }
    const sources = this.sourcesProblem();
    if (sources) {
      problems['sources'] = sources;
    }
    return problems;
  });

  /** The noticed bounds, while about the request now in the box. */
  private readonly noticedNow = computed(() => {
    const check = this.noticed();
    return check && check.request === this.request().trim() ? check.noticed : [];
  });

  /** Python's note under a box the request filled in, while it holds that figure. */
  protected noticedNote(key: string): string {
    const offer = this.noticedNow().find((bound) => bound.bound === key);
    const row = this.numberFields.find((field) => field.key === key);
    return offer && row?.value() === offer.value ? offer.note : '';
  }

  /** Offers made, as `key=figure`, so a box the shopper cleared stays clear. */
  private readonly offered = new Set<string>();

  /** Boxes this form filled and nobody touched since: they follow the request. */
  private readonly filled = new Map<string, number>();

  private readonly sourcesProblem = computed(() => {
    const checked = this.checked();
    return checked && checked.sources === this.sources().trim() ? checked.error : '';
  });

  /** Under each field: the page's problems, else the server's refusal. */
  protected readonly notes = computed<Record<string, string>>(() => {
    const problems = this.problems();
    const rejected = this.rejected();
    if (!rejected || problems[rejected.field] || !this.stillSent(rejected.field)) {
      return problems;
    }
    return { ...problems, [rejected.field]: rejected.message };
  });

  /** Both the sentence's `id` and the box's `aria-describedby` (ADR-0033). */
  protected problemId(key: string): string | null {
    return this.notes()[key] ? `problem-${key}` : null;
  }

  /** Whether the field still holds the value it was refused for. */
  private stillSent(field: string): boolean {
    // A rail switched, or paying off, moves a payment's box on as much as typing does.
    const rejected = this.rejected();
    const payment = rejected?.field === field ? rejected.payment : undefined;
    if (payment) {
      return (
        this.pay() &&
        this.rail() === payment.rail &&
        this.options()[field as keyof SearchOptions] === payment.sent
      );
    }
    const sent = this.submitted();
    if (!sent || !(field in sent)) {
      return true;
    }
    return this.options()[field as keyof SearchOptions] === sent[field as keyof SearchOptions];
  }

  protected readonly flagged = computed(() => Object.keys(this.notes()).length);

  /** Each number box's placeholder: its default, or "No limit" for a bound. */
  protected readonly placeholders = computed<Record<string, string | undefined>>(() => {
    const named: Record<string, string> = {};
    const defaults = this.defaults();
    for (const { key } of this.numberFields) {
      const fallback = defaults?.[key];
      if (typeof fallback === 'number') {
        named[key] = `${fallback}`;
      } else if (fallback === null) {
        // A bound (ADR-0039).
        named[key] = NO_LIMIT;
      }
    }
    named['num_ctx'] = this.numCtxHint();
    return named;
  });

  protected readonly canSubmit = computed(
    () => this.request().trim().length > 0 && Object.keys(this.problems()).length === 0,
  );

  constructor() {
    effect(() => {
      const defaults = this.defaults();
      if (defaults) {
        untracked(() => this.seed(defaults));
      }
    });

    // Open the settings when there is something in them to read.
    effect(() => {
      if (this.flagged()) {
        this.advanced.set(true);
      }
    });

    // Fill a noticed bound into an empty box once, and open the panel (ADR-0059). A box
    // the form filled follows the request; one the shopper touched is theirs.
    effect(() => {
      const check = this.noticed();
      const answered = check !== null && check.request === this.request().trim();
      const offers = this.noticedNow();
      untracked(() => {
        // `App` drops a superseded reading, so one landing answers the last question.
        const landed = check !== this.lastReading;
        this.lastReading = check;
        if (landed) {
          this.reading = false;
        }
        // Boxes this reading changed.
        const shown: NumberKey[] = [];
        for (const [key, value] of this.filled) {
          const row = this.numberFields.find((field) => field.key === key);
          if (row?.value() !== value) {
            this.filled.delete(key);
          } else if (answered && !offers.some((bound) => bound.bound === key)) {
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
          const owned = this.filled.has(bound.bound);
          if (!owned && this.offered.has(mark)) {
            continue;
          }
          this.offered.add(mark);
          if (owned || row.value() === null) {
            if (row.value() !== bound.value) {
              shown.push(row.key);
            }
            row.value.set(bound.value);
            this.filled.set(bound.bound, bound.value);
            this.advanced.set(true);
          }
        }
        if (landed && this.held() !== null) {
          this.release(shown);
        }
      });
    });

    // Typing over a held submit's request takes it back.
    effect(() => {
      const request = this.request().trim();
      untracked(() => {
        if (this.held() !== request) {
          this.held.set(null);
        }
      });
    });

    effect(() => {
      const rejected = this.rejected();
      if (rejected && !this.stillSent(rejected.field)) {
        this.moved.emit();
      }
    });

    // Pay buttons on results already in follow the switch.
    effect(() => {
      const settings = this.paying();
      untracked(() => this.payWith.emit(settings));
    });
  }

  /** The server's defaults, then anything remembered. */
  private seed(defaults: AgentDefaults): void {
    for (const field of Object.values(this.settings)) {
      field.seed(defaults);
    }
    this.restore(defaults);
    // Nobody will type remembered sources to check them.
    this.sourcesChanged();
  }

  protected toggled(event: Event): void {
    this.advanced.set((event.target as HTMLDetailsElement).open);
  }

  protected submit(): void {
    if (!this.canSubmit() || this.running()) {
      return;
    }
    // Enter leaves the box and submits at once, so the reading is behind the run (ADR-0059).
    if (this.reading) {
      this.held.set(this.request().trim());
      return;
    }
    this.send();
  }

  private send(): void {
    this.stoppedAt.set(null);
    this.remember();
    const options = this.options();
    this.submitted.set(options);
    this.run.emit(options);
  }

  /** The reading a submit waited on has landed: send it, unless it put a figure in a
   *  box, where the shopper is taken instead, never applying an offer unseen. */
  private release(shown: readonly NumberKey[]): void {
    const request = this.held();
    this.held.set(null);
    if (request !== this.request().trim()) {
      return;
    }
    const first = shown[0];
    if (first === undefined) {
      // Again, so a box marked in the meantime still stops it.
      this.submit();
      return;
    }
    this.stoppedAt.set(first);
    // Once its panel is drawn; the whole field, so the sentences under the box show.
    afterNextRender(
      () => {
        const box = this.host.nativeElement.querySelector<HTMLInputElement>(
          `input[name="${first}"]`,
        );
        box?.focus({ preventScroll: true });
        box?.closest('.field')?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' });
      },
      { injector: this.injector },
    );
  }

  private options(): SearchOptions {
    return {
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
      cpu_only: this.takesCpuOnly() ? this.cpuOnly() : undefined,
      fetch: this.fetchPages(),
      journal: this.journal(),
      ...this.payingValues(),
    };
  }

  /** Nothing for a box this run does not take. */
  private numbers(): Pick<SearchOptions, NumberField['key']> {
    return Object.fromEntries(
      this.numberFields.map((row) => [row.key, row.off() ? null : row.value()]),
    );
  }

  protected numberTyped(key: string, event: Event): void {
    const input = event.target as HTMLInputElement;
    const bad = input.validity?.badInput ?? false;
    this.unreadable.update((held) => (held[key] === bad ? held : { ...held, [key]: bad }));
  }

  protected useExample(example: string): void {
    this.request.set(example);
    this.requestChanged();
  }

  /** A provider's model and address come with it. */
  protected providerChanged(): void {
    const option = this.chosenProvider();
    if (option) {
      this.model.set(option.model);
      this.baseUrl.set(option.base_url);
    }
    this.serverChanged();
  }

  protected railChanged(): void {
    const option = this.chosenRail();
    if (option) {
      this.merchantUrl.set(option.endpoint);
    }
  }

  /** Ask the server what the request asks for in words. */
  protected requestChanged(): void {
    this.stoppedAt.set(null);
    const request = this.request().trim();
    // An empty request is not asked about.
    this.reading = request !== '';
    this.read.emit(request);
  }

  protected sourcesChanged(): void {
    this.check.emit(this.sources().trim());
  }

  protected serverChanged(): void {
    const url = this.baseUrl().trim();
    if (url) {
      this.refresh.emit({ provider: this.provider(), base_url: url });
    }
  }

  /** Settings only: the request and its bounds are new every time (ADR-0077). */
  private remember(): void {
    const saved: Record<string, unknown> = {};
    for (const [key, field] of Object.entries(this.settings)) {
      if (field.remembered) {
        saved[key] = field.value();
      }
    }
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(saved));
    } catch {
      // A browser that refuses storage still gets a working form.
    }
  }

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
      // Not even a bound an older build stored (ADR-0077).
      if (field.remembered && key in saved) {
        field.restore((saved as Record<string, unknown>)[key], defaults);
      }
    }
  }
}

/** One remembered value, or undefined for anything it will not take. */
type Parser<T> = (raw: unknown, defaults: AgentDefaults) => T | undefined;

/** One seeded setting, its signal's type closed over. */
interface Setting {
  seed(defaults: AgentDefaults): void;
  value(): unknown;
  restore(raw: unknown, defaults: AgentDefaults): void;
  readonly remembered: boolean;
}

function setting<T>(
  target: WritableSignal<T>,
  fromDefaults: (defaults: AgentDefaults) => T,
  parse: Parser<T>,
  remembered = true,
): Setting {
  return {
    remembered,
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

/** The number boxes as settings, stored under their camel case. */
function numberSettings(fields: readonly NumberField[]): Record<string, Setting> {
  return Object.fromEntries(
    fields.map((row) => [
      camelCase(row.key),
      setting<number | null>(
        row.value,
        (defaults) => defaults[row.key],
        row.remembersBlank ? asNumberOrNull : asNumber,
        row.remembered,
      ),
    ]),
  );
}

function camelCase(key: string): string {
  return key.replace(/_(\w)/g, (_, letter: string) => letter.toUpperCase());
}

const asText: Parser<string> = (raw) => (typeof raw === 'string' ? raw : undefined);
const asNumber: Parser<number> = (raw) => (typeof raw === 'number' ? raw : undefined);
const asBoolean: Parser<boolean> = (raw) => (typeof raw === 'boolean' ? raw : undefined);
const asNumberOrNull: Parser<number | null> = (raw) =>
  raw === null || typeof raw === 'number' ? raw : undefined;

/** A remembered name the server still offers. */
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
