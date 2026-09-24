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

/** The key a number box is known by everywhere: what it is sent under, what its
 *  range arrives under, what a refusal names, and -- Python answering a default
 *  per setting under that same name -- what its placeholder and its seed are read
 *  off `AgentDefaults` by. Narrowed to the keys whose default really is a number,
 *  so the seed needs no cast and a box for a setting that is not one does not
 *  compile. */
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
  /** Whether this box belongs to the paying block rather than the settings grid.
   *  The three paying settings are drawn only once the box is ticked, and this one
   *  was the exception: disabled, four rows above that tick, under a hint naming a
   *  control the reader could not see -- and, on a phone, could not see at once. */
  paying: boolean;
  /** Whether a *cleared* box is worth remembering -- true for a bound, whose
   *  blank is the whole answer (ADR-0039). The one thing about a number box its
   *  key cannot say: `num_ctx` defaults to a number and still has to remember a
   *  blank, `temperature` defaults to 0 and must not. */
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
  /** What the server read out of the request: bounds it asks for in words. Offered
   *  and never applied -- the box is filled in and the shopper submits it or clears
   *  it. Not a `Rejection`: nothing here is wrong, so nothing here marks a box. */
  readonly noticed = input<BoundsCheck | null>(null);

  /** Named for what it asks rather than `search`, which is a DOM event: an
   *  `(search)` on this element would be answered by a native one bubbling up too. */
  readonly run = output<SearchOptions>();
  readonly stop = output<void>();
  /** Ask what another server is serving, when the provider or the address changes. */
  readonly refresh = output<ModelSource>();
  /** Ask whether the sources field names sources. */
  readonly check = output<string>();
  /** Ask what the request itself says about the bounds. */
  readonly read = output<string>();
  /** A refusal this form has moved past: the box it named holds something else now.
   *  The mark under that box goes when the value does (see `notes`), and the banner
   *  repeating the same sentence has to go with it -- a page showing "'english' is not
   *  a search region" over a Region box reading `us-en` is refusing something nobody
   *  can see, and the one place it was pointing has already stopped saying so. */
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
    field('max_price', 'Max price', this.maxPrice, {
      step: 0.01,
      // The scale this is read on, named rather than assumed: it is the shopper's
      // own choice above when they made one, and the vote when they did not.
      hint: () => `In ${this.scale()}; nothing is converted.`,
    }),
    field('min_rating', 'Min rating', this.minRating, {
      step: 0.1,
      hint: 'Out of 5. Unrated products are still shown.',
    }),
    field('min_reviews', 'Min reviews', this.minReviews, {
      hint: 'How many reviews a rating has to average.',
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
      // No second branch for paying being off: the box is not drawn then, and a
      // sentence explaining that had nowhere to be read from.
      hint: () =>
        `The most one payment may be, in ${this.scale()}. A price in another currency is refused, not passed.`,
      off: () => !this.pay(),
      paying: true,
    }),
  ];

  /** The boxes the settings grid draws, and the ones the paying block does -- one
   *  partition of the table above, so a box is still declared exactly once and only
   *  where it is drawn has moved. */
  protected readonly settingFields = this.numberFields.filter((row) => !row.paying);
  protected readonly payingFields = this.numberFields.filter((row) => row.paying);

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
    // Checked against what the server offers, for the reason `provider` and `rail`
    // are: a code remembered by a browser and since dropped from Python's table
    // would leave the picker matching nothing and every price off the scale.
    currency: setting(
      this.currency,
      (d) => d.currency,
      // The blank is a value here and not a missing one -- "whatever the pages
      // quote" -- so it is offered alongside the codes rather than refused.
      amongst((d) => ['', ...d.currency_options]),
    ),
    backend: setting(
      this.backend,
      (d) => d.backend,
      amongst((d) => d.backend_options.map((option) => option.name)),
    ),
    // Remembered like the rest: which sites a shopper trusts is a standing
    // answer, not something they retype for every search.
    sources: setting(this.sources, (d) => d.sources, asText),
    // Every number box, off the table above: being in it is what seeds,
    // remembers and restores one, the way it is already what draws and bounds
    // one. The three bounds a shopper sets once and shops under for weeks are
    // in there with the rest.
    ...numberSettings(this.numberFields),
    // The same check, and the row that most needed it: a cast is not one, and `SortBy` is a union
    // the server is free to add to and drop from.
    sortBy: setting(
      this.sortBy,
      (d) => d.sort_by,
      amongst<SortBy>((d) => d.sort_options),
    ),
    thinking: setting(this.thinking, (d) => toThinking(d.think), asThinking),
    // A standing answer about this machine -- whether its card is to be left alone --
    // so it is remembered like the rest.
    cpuOnly: setting(this.cpuOnly, (d) => d.cpu_only, asBoolean),
    fetchPages: setting(this.fetchPages, (d) => d.fetch, asBoolean),
    // A standing answer about this machine -- whether a shopping history is kept on
    // it -- so it is remembered like the rest.
    journal: setting(this.journal, (d) => d.journal, asBoolean),
    // Checked against the rails this server offers, for the reason `provider` is:
    // a name remembered by a browser and since dropped leaves the picker matching
    // nothing and the address field describing a rail nobody chose.
    rail: setting(
      this.rail,
      (d) => d.rail,
      amongst((d) => d.rail_options.map((option) => option.name)),
    ),
    merchantUrl: setting(this.merchantUrl, (d) => d.merchant_url, asText),
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

  /** What the two amounts on this form are read in, as the boxes name it. */
  protected readonly scale = computed(
    () => this.currency() || 'the currency most of the pages quote',
  );

  protected readonly currencyOptions = computed<string[]>(
    () => this.defaults()?.currency_options ?? [],
  );

  protected readonly backendOptions = computed<BackendOption[]>(
    () => this.defaults()?.backend_options ?? [],
  );

  /** The row for the backend currently chosen, which says whether this server can
   *  ask it at all. */
  protected readonly chosenBackend = computed<BackendOption | undefined>(() =>
    this.backendOptions().find((option) => option.name === this.backend()),
  );

  /** What the backend field says under it: where it is asked, or what it is missing.
   *  Python decided `configured`; the browser only reads it out. */
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

  /** The ranges the server declared, by the key each field is sent under -- and
   *  `undefined` for a field nothing bounds, which `Defaults.limits` documents and
   *  a `Record<string, Limit>` then denies to every template reading it. */
  protected readonly limits = computed<Record<string, Limit | undefined>>(
    () => this.defaults()?.limits ?? {},
  );

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

  /** The bounds the request asks for, while the answer is still about what the request
   *  box holds: text since typed over is a reading of a different question. */
  private readonly noticedNow = computed(() => {
    const check = this.noticed();
    return check && check.request === this.request().trim() ? check.noticed : [];
  });

  /** What to say under a box holding a number nobody typed, by the key it is sent
   *  under. Python's sentence, and a hint rather than a mark: nothing is wrong. */
  protected noticedNote(key: string): string {
    const offer = this.noticedNow().find((bound) => bound.bound === key);
    // Only while the box still holds that figure: under a number from an earlier
    // request, or one the shopper typed, the sentence attributes it to words that
    // asked for something else.
    const row = this.numberFields.find((field) => field.key === key);
    return offer && row?.value() === offer.value ? offer.note : '';
  }

  /** Which offers this form has already acted on, so a box the shopper then cleared
   *  is not filled in again on the next render. Keyed by the setting and the figure,
   *  so a re-worded request offering a different number is a new offer. */
  private readonly offered = new Set<string>();

  /** The boxes holding a figure this form filled in and nobody has touched since, by
   *  key: those are the request's and follow it, where a box somebody typed in is
   *  theirs. Left filled when the request moves on, a budget read off "under $900"
   *  was sent with "gaming laptop under $1500" under a note quoting the $1500 -- and
   *  then remembered, an invisible filter on every search after it. */
  private readonly filled = new Map<string, number>();

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

  /**
   * The id of the sentence marking this box, or null where nothing marks it.
   *
   * `aria-invalid` is the box saying something is wrong and never what, and the
   * sentence beside it is announced once as it appears and is a paragraph next
   * to a box from then on -- so a reader arriving at a box already marked, by a
   * remembered value or by tabbing back to it, is told there is a problem and
   * not which. ADR-0033 puts the refusal on the box it is about, and this is
   * the box pointing at it. One answer for both ends of that pointer: the
   * sentence is given this id and the box is described by it, an
   * `aria-describedby` naming an element that is not there being a mark that
   * reaches nobody -- which is the rule `a11y.ts` runs as
   * `aria-valid-attr-value`.
   */
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

  /** How many settings have something to say about them, for the summary to carry. */
  protected readonly flagged = computed(() => Object.keys(this.notes()).length);

  /** What a cleared number box falls back to, named in the box itself -- and nothing
   *  at all under a key the defaults answer with neither a number nor the blank the
   *  three bounds have, which is the miss the template's `?? ''` is for. */
  protected readonly placeholders = computed<Record<string, string | undefined>>(() => {
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

    // Fill in a bound the request asked for in words, once, and only where the box is
    // empty: offering is the whole of it, so a box the shopper has typed in or cleared
    // is theirs. The panel opens with it, since an offer nobody can see is not one.
    // A figure it filled in and nobody touched is the request's, so it follows the
    // request: replaced by what a reworded one asks for, and cleared once the answer
    // about the request now in the box offers nothing for it.
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
            // Cleared by the form and not the shopper, so a request asking for it
            // again is offered it again.
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
          // `offered` guards only a box somebody cleared; one the form still owns
          // takes whatever the request now asks for, back to a figure it once held too.
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

    // `stillSent` reads every field, so this re-runs on any of them changing -- which
    // is exactly when a refusal stops being about what is on screen.
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
    // A remembered value is one nobody is about to type, so nothing else would ever ask about it: a
    // browser holding a bad source would find out a run later, which is the whole complaint.
    this.sourcesChanged();
  }

  /** Follow the panel when the reader opens or shuts it, so a mark opening it is not undone. */
  protected toggled(event: Event): void {
    this.advanced.set((event.target as HTMLDetailsElement).open);
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
    this.run.emit(options);
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
      currency: this.currency(),
      backend: this.backend(),
      sources: this.sources().trim(),
      sort_by: this.sortBy(),
      think: fromThinking(this.thinking()),
      // Left out where the server chose its own device, the way an off number box is:
      // a switch that run cannot honour is not a setting it had.
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

/**
 * The number boxes as remembered settings, read off the one table that declares
 * them: the server answers each default under the very key the box is sent under,
 * so the seed is that key and not a second thing to write down. Stored under the
 * camel case of it -- what the signal is called, and what a browser holding a
 * saved blob already wrote.
 */
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
