import { Component, computed, effect, input, output, signal, untracked } from '@angular/core';
import type { WritableSignal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import type {
  AgentDefaults,
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

/** One number box, as the template draws it and the checks read it. */
interface NumberField {
  /** The key the value is sent under, the range arrives under and a refusal names --
   *  and, Python answering a default per setting under that same name, the key its
   *  placeholder is read off `AgentDefaults` by. Typed as both, so a box that is drawn
   *  is one the server has a range and a default for. */
  key: keyof AgentDefaults & keyof SearchOptions;
  label: string;
  value: WritableSignal<number | null>;
  step: number;
  hint: () => string;
  off: () => boolean;
}

/** One row of the table above, with the defaults most of them take. */
function field(
  key: NumberField['key'],
  label: string,
  value: WritableSignal<number | null>,
  extra: { step?: number; hint?: string | (() => string); off?: () => boolean } = {},
): NumberField {
  const hint = extra.hint ?? '';
  return {
    key,
    label,
    value,
    step: extra.step ?? 1,
    hint: typeof hint === 'string' ? () => hint : hint,
    off: extra.off ?? (() => false),
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
  imports: [FormsModule],
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

  readonly search = output<SearchOptions>();
  readonly stop = output<void>();
  /** Ask what another server is serving, when the provider or the address changes. */
  readonly refresh = output<ModelSource>();
  /** Ask whether the sources field names sources. */
  readonly check = output<string>();

  protected readonly examples = EXAMPLES;

  protected readonly request = signal('');
  protected readonly provider = signal('ollama');
  protected readonly model = signal('');
  protected readonly baseUrl = signal('');
  protected readonly region = signal('us-en');
  protected readonly sources = signal('');
  // Every number box is `number | null`, because null is what one holds when it is cleared -- "use
  // the default" for most (ADR-0012) and "no bound at all" for the three the shopper sets
  // (ADR-0039).
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

  /** The settings that are seeded from the server, remembered, and restored. */
  private readonly settings: Record<string, Setting> = {
    // Remembered like the rest, and remembered *with* the two fields it decides: a browser that
    // switched to vLLM saved that provider's model and address in the same blob, so restoring them
    // together can never pair one with the other.
    provider: setting(
      this.provider,
      (d) => d.provider,
      amongst((d) => d.provider_options.map((option) => option.name)),
    ),
    model: setting(this.model, (d) => d.model, asText),
    baseUrl: setting(this.baseUrl, (d) => d.base_url, asText),
    region: setting(this.region, (d) => d.region, asText),
    // Remembered like the rest: which sites a shopper trusts is a standing
    // answer, not something they retype for every search.
    sources: setting(this.sources, (d) => d.sources, asText),
    results: setting(this.results, (d) => d.results, asNumber),
    top: setting(this.top, (d) => d.top, asNumber),
    // The three the shopper sets once and shops under for weeks, so they are remembered like the
    // rest.
    maxPrice: setting(this.maxPrice, (d) => d.max_price, asNumberOrNull),
    minRating: setting(this.minRating, (d) => d.min_rating, asNumberOrNull),
    minReviews: setting(this.minReviews, (d) => d.min_reviews, asNumberOrNull),
    cacheTtl: setting(this.cacheTtl, (d) => d.cache_ttl, asNumberOrNull),
    // The same check, and the row that most needed it: a cast is not one, and `SortBy` is a union
    // the server is free to add to and drop from.
    sortBy: setting(
      this.sortBy,
      (d) => d.sort_by,
      amongst<SortBy>((d) => d.sort_options),
    ),
    temperature: setting(this.temperature, (d) => d.temperature, asNumber),
    // The one field a remembered `null` has to win on.
    numCtx: setting(this.numCtx, (d) => d.num_ctx, asNumberOrNull),
    modelTimeout: setting(this.modelTimeout, (d) => d.model_timeout, asNumberOrNull),
    thinking: setting(this.thinking, (d) => toThinking(d.think), asThinking),
    // A standing answer about this machine -- whether its card is to be left alone --
    // so it is remembered like the rest.
    cpuOnly: setting(this.cpuOnly, (d) => d.cpu_only, asBoolean),
    fetchPages: setting(this.fetchPages, (d) => d.fetch, asBoolean),
    // Checked against the rails this server offers, for the reason `provider` is:
    // a name remembered by a browser and since dropped leaves the picker matching
    // nothing and the address field describing a rail nobody chose.
    rail: setting(
      this.rail,
      (d) => d.rail,
      amongst((d) => d.rail_options.map((option) => option.name)),
    ),
    merchantUrl: setting(this.merchantUrl, (d) => d.merchant_url, asText),
    spendLimit: setting(this.spendLimit, (d) => d.spend_limit, asNumberOrNull),
  };

  protected readonly sortOptions = computed<SortBy[]>(
    () => this.defaults()?.sort_options ?? ['score', 'price', 'rating'],
  );

  protected readonly providerOptions = computed<ProviderOption[]>(
    () => this.defaults()?.provider_options ?? [],
  );

  protected readonly railOptions = computed<RailOption[]>(
    () => this.defaults()?.rail_options ?? [],
  );

  /** The row for the rail currently chosen, which carries its address and whether it needs one. */
  protected readonly chosenRail = computed<RailOption | undefined>(() =>
    this.railOptions().find((option) => option.name === this.rail()),
  );

  /** Whether the optional AP2 SDK is installed on the server at all. */
  protected readonly payAvailable = computed(() => this.defaults()?.pay_available ?? false);

  /** Whether this rail can charge anybody. */
  protected readonly railSpends = computed(() => this.chosenRail()?.moves_money ?? false);

  /** Whether the address field is a setting on this rail at all. */
  protected readonly railNeedsEndpoint = computed(() => this.chosenRail()?.needs_endpoint ?? false);

  /** The row for the provider currently chosen, which carries its defaults and
   *  what it can be told per request. Absent before the server's defaults land. */
  protected readonly chosenProvider = computed<ProviderOption | undefined>(() =>
    this.providerOptions().find((option) => option.name === this.provider()),
  );

  /** What to call this server on screen -- "Ollama", "vLLM". */
  protected readonly providerLabel = computed(
    () => this.chosenProvider()?.label ?? this.provider(),
  );

  /** Whether the context window is a per-run setting at all. */
  protected readonly takesNumCtx = computed(() => this.chosenProvider()?.takes_num_ctx ?? true);

  /** Whether keeping the model off the GPU is a per-run setting at all. */
  protected readonly takesCpuOnly = computed(() => this.chosenProvider()?.takes_cpu_only ?? true);

  /** What the box says under it: the trade, or why this server is not asked. */
  protected readonly cpuOnlyHint = computed(() =>
    this.takesCpuOnly()
      ? 'Slower, but it leaves the card free and runs a model too large to fit on it.'
      : `${this.providerLabel()} is started on the device it serves from, so this is not a per-run setting there.`,
  );

  /** Cleared, the field means "whatever the server defaults to" -- so name it. */
  protected readonly numCtxHint = computed(() => {
    if (!this.takesNumCtx()) {
      return `Fixed when ${this.providerLabel()} starts`;
    }
    const fallback = this.defaults()?.num_ctx;
    return fallback ? `The default (${fallback})` : "Ollama's own (4096)";
  });

  /**
   * What the model dropdown offers: everything the server reported, plus the name currently chosen
   * if that is not among them, each marked with whatever is wrong with it.
   */
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

  /** Every number field, in the order the form draws them. */
  protected readonly numberFields: NumberField[] = [
    field('max_price', 'Max price', this.maxPrice, {
      step: 0.01,
      hint: 'In the currency most of the pages quote; nothing is converted.',
    }),
    field('min_rating', 'Min rating', this.minRating, {
      step: 0.1,
      hint: 'Out of 5. Unrated products are still shown.',
    }),
    field('min_reviews', 'Min reviews', this.minReviews, {
      hint: 'How many reviews a rating has to average.',
    }),
    field('results', 'Products to find', this.results),
    field('top', 'Products to highlight', this.top),
    field('temperature', 'Temperature', this.temperature, { step: 0.1 }),
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
      hint: () =>
        this.pay()
          ? 'The most one payment may be, in the currency most pages quote. A price in another currency is refused, not passed.'
          : 'Only applies when Pay for the top product is on.',
      off: () => !this.pay(),
    }),
  ];

  /** The ranges the server declared, by the key each field is sent under. */
  protected readonly limits = computed<Record<string, Limit>>(() => this.defaults()?.limits ?? {});

  /** What the page itself can say is wrong with a field, by the key it is sent under. */
  protected readonly problems = computed<Record<string, string>>(() => {
    const problems: Record<string, string> = {};
    const limits = this.limits();
    const unreadable = this.unreadable();
    for (const { key, value: held, off } of this.numberFields) {
      // A box this run does not take is not a setting to be held to anything, and it is disabled --
      // so a mark on it is one nobody can act on: the button stays off, the summary counts a
      // setting to look at, and the box it points at cannot be typed into.
      if (off()) {
        continue;
      }
      const limit = limits[key];
      const value = held();
      // Before the range, and before reading the value at all: what the box holds
      // is not a number, so there is nothing to hold to a range -- and the signal
      // says `null`, which is the one answer this must not be confused with.
      if (unreadable[key]) {
        problems[key] = 'That is not a number. Clear the box to use the default.';
      } else if (limit && value !== null && (value < limit.min || value > limit.max)) {
        // A cleared box means "use the default" (ADR-0012) rather than a number to
        // hold to a range -- and it is the only way to ask for the server's own.
        problems[key] = `Between ${limit.min} and ${limit.max}.`;
      }
    }
    const sources = this.sourcesProblem();
    if (sources) {
      problems['sources'] = sources;
    }
    return problems;
  });

  /** What the server said about the sources field, while it is still about what the field holds. */
  private readonly sourcesProblem = computed(() => {
    const checked = this.checked();
    return checked && checked.sources === this.sources().trim() ? checked.error : '';
  });

  /**
   * What to show under each field: what the page worked out, and -- for a field it has no rule of
   * its own for -- what the server said when it refused the run.
   */
  protected readonly notes = computed<Record<string, string>>(() => {
    const problems = this.problems();
    const rejected = this.rejected();
    if (!rejected || problems[rejected.field] || !this.stillSent(rejected.field)) {
      return problems;
    }
    return { ...problems, [rejected.field]: rejected.message };
  });

  /** Whether the field named still holds the value the run was refused for. */
  private stillSent(field: string): boolean {
    const sent = this.submitted();
    if (!sent || !(field in sent)) {
      return true;
    }
    return this.options()[field as keyof SearchOptions] === sent[field as keyof SearchOptions];
  }

  /** How many settings have something to say about them, for the summary to carry. */
  protected readonly flagged = computed(() => Object.keys(this.notes()).length);

  /** What a cleared number box falls back to, named in the box itself. */
  protected readonly placeholders = computed<Record<string, string>>(() => {
    const named: Record<string, string> = {};
    const defaults = this.defaults();
    for (const { key } of this.numberFields) {
      // Each box is sent under the name Python answers its default under, so the fallback
      // is read off the defaults rather than listed here a second time: a ninth box used
      // to be drawn with an empty placeholder until somebody remembered this list too.
      const fallback = defaults?.[key];
      if (typeof fallback === 'number') {
        named[key] = `${fallback}`;
      } else if (fallback === null) {
        // The three bounds, whose default really is nothing: an empty box here
        // is the whole answer rather than a stand-in for a number (ADR-0039).
        named[key] = NO_LIMIT;
      }
    }
    // Its own sentence rather than a bare number: cleared, this one falls back to
    // whatever the server defaults to, which is a different answer per provider.
    named['num_ctx'] = this.numCtxHint();
    return named;
  });

  /** Nothing to shop for, or a field the page already knows the server would refuse. */
  protected readonly canSubmit = computed(
    () => this.request().trim().length > 0 && Object.keys(this.problems()).length === 0,
  );

  constructor() {
    // The server's defaults arrive after the form has already rendered, so seed the fields when
    // they land.
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
  }

  /** Fill the form from the server's defaults, then let anything remembered win. */
  private seed(defaults: AgentDefaults): void {
    for (const field of Object.values(this.settings)) {
      field.seed(defaults);
    }
    this.restore(defaults);
    // A remembered value is one nobody is about to type, so nothing else would ever ask about it: a
    // browser holding a bad source would find out a run later, which is the whole complaint.
    this.sourcesChanged();
  }

  protected submit(): void {
    if (!this.canSubmit() || this.running()) {
      return;
    }
    this.remember();
    const options = this.options();
    // Kept beside the request, so a refusal naming a field can be dropped as soon
    // as that field stops holding what was refused.
    this.submitted.set(options);
    this.search.emit(options);
  }

  /** Every setting as a run would be asked for it. */
  private options(): SearchOptions {
    return {
      // Every number box, off the one table that declares them -- so a new box is a row there and
      // nothing here, the way it is already a row there and nothing in the template.
      ...this.numbers(),
      request: this.request().trim(),
      provider: this.provider(),
      model: this.model().trim(),
      base_url: this.baseUrl().trim(),
      region: this.region().trim(),
      sources: this.sources().trim(),
      sort_by: this.sortBy(),
      think: fromThinking(this.thinking()),
      // Left out where the server chose its own device, the way an off number box is:
      // a switch that run cannot honour is not a setting it had.
      cpu_only: this.takesCpuOnly() ? this.cpuOnly() : undefined,
      fetch: this.fetchPages(),
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

/** Validated rather than taken as text, so a `'default'` remembered by a browser
 *  from when the form offered three states is ignored and the seed stands. */
const asThinking: Parser<Thinking> = (raw) => (raw === 'on' || raw === 'off' ? raw : undefined);

/** A server default of `null` seeds `off`, which is what the server does with an
 *  unset `think` anyway -- so the form shows the state the run will actually use. */
function toThinking(value: boolean | null): Thinking {
  return value ? 'on' : 'off';
}

function fromThinking(value: Thinking): boolean {
  return value === 'on';
}
