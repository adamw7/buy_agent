import { Component, computed, effect, input, output, signal, untracked } from '@angular/core';
import type { WritableSignal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import type {
  AgentDefaults,
  Limit,
  ModelSource,
  ModelStatus,
  ProviderOption,
  SearchOptions,
  SortBy,
  SourcesCheck,
} from '../agent.types';

/**
 * The model's thinking mode as a `<select>` can hold it.
 *
 * Two states, not the three `AgentConfig.reasoning` carries. The third --
 * `null`, "send nothing and leave the model's own behaviour alone" -- is a
 * library-level escape hatch that no front end can spell: a blank field means
 * "use the default" (ADR-0012), so there is no way to ask for it over the wire,
 * and the server would answer with the default `false` regardless. Offering it
 * here made a third option that silently did what `off` does (ADR-0019).
 */
type Thinking = 'on' | 'off';

/**
 * One entry in the model dropdown, and what is wrong with picking it.
 *
 * Two things can be, and neither is a reason to leave the entry out. A name the
 * server is not serving is kept so a remembered setting is never silently
 * swapped for someone else's model; a model that cannot answer a prompt -- an
 * embedding model, which Ollama lists exactly like a chat one -- is kept so a
 * pull made by mistake is visible rather than absent (ADR-0032). `note` is the
 * empty string for an entry that is simply a choice.
 */
interface ModelOption {
  name: string;
  note: string;
}

/**
 * A value the server refused, and the box it came out of.
 *
 * The second line rather than the first: it is what a run that started anyway
 * comes back with -- a region of the wrong shape, a source typed and submitted
 * before the check for it answered. `field` is the key Python named in its
 * `ApiError`, which is the key the form sends that setting under (ADR-0033).
 */
export interface Rejection {
  field: string;
  message: string;
}

/**
 * One number box, as the template draws it and the checks read it.
 *
 * `hint` and `off` are functions rather than values because two of them depend
 * on the provider the picker is on: the context window's sentence names the
 * server, and the box itself is switched off for a vLLM, which fixes its window
 * when it starts. Everything else answers the same for every run.
 */
interface NumberField {
  key: string;
  label: string;
  value: WritableSignal<number | null>;
  step: number;
  hint: () => string;
  off: () => boolean;
}

/** One row of the table above, with the defaults most of them take. */
function field(
  key: string,
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

/** What a cleared bound box falls back to, said in the box. The other numbers
 *  fall back to a value the server named; these fall back to no bound at all,
 *  and "10" in grey where the answer is "everything" would be a lie. */
const NO_LIMIT = 'No limit';

const EXAMPLES = [
  'wireless noise cancelling headphones under $200',
  'gaming laptop under $1500',
  'espresso machine for a small kitchen',
  'running shoes for flat feet',
];

/**
 * What to shop for, and the settings the CLI takes as flags.
 *
 * The advanced settings are remembered between visits, because they are the ones
 * a given machine sets once -- which model is pulled, which region to search --
 * and then never changes.
 */
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
  /** What the server made of the sources field, last time it was asked. */
  readonly checked = input<SourcesCheck | null>(null);
  /** A value a run was refused for, to mark beside the field it came from. */
  readonly rejected = input<Rejection | null>(null);

  readonly search = output<SearchOptions>();
  readonly stop = output<void>();
  /** Ask what another server is serving, when the provider or the address changes. */
  readonly refresh = output<ModelSource>();
  /** Ask whether the sources field names sources. The parse is Python's, so the
   *  page asks rather than keeping a second copy of it (ADR-0033). */
  readonly check = output<string>();

  protected readonly examples = EXAMPLES;

  protected readonly request = signal('');
  protected readonly provider = signal('ollama');
  protected readonly model = signal('');
  protected readonly baseUrl = signal('');
  protected readonly region = signal('us-en');
  protected readonly sources = signal('');
  // Every number box is `number | null`, because null is what one holds when it
  // is cleared -- "use the default" for most of them (ADR-0012) and "no bound at
  // all" for the three the shopper sets (ADR-0039). Typed as plain numbers, three
  // of these already went null at runtime the moment somebody emptied the box.
  protected readonly results = signal<number | null>(10);
  protected readonly top = signal<number | null>(3);
  protected readonly maxPrice = signal<number | null>(null);
  protected readonly minRating = signal<number | null>(null);
  protected readonly minReviews = signal<number | null>(null);
  protected readonly cacheTtl = signal<number | null>(null);
  protected readonly sortBy = signal<SortBy>('score');
  protected readonly temperature = signal<number | null>(0);
  protected readonly numCtx = signal<number | null>(null);
  protected readonly thinking = signal<Thinking>('off');
  protected readonly fetchPages = signal(true);
  protected readonly advanced = signal(false);

  /**
   * The settings that are seeded from the server, remembered, and restored.
   *
   * One row each, rather than the same ten names written out in `seed`, in
   * `remember` and again in `restore` -- three lists that only ever drift apart.
   * The key is the name the setting is stored under; `request` and `advanced`
   * are absent because neither is remembered.
   */
  private readonly settings: Record<string, Setting> = {
    // Remembered like the rest, and remembered *with* the two fields it decides:
    // a browser that switched to vLLM saved that provider's model and address in
    // the same blob, so restoring them together can never pair one with the other.
    // Checked against what this server offers rather than taken as text, for the
    // reason `asThinking` is: a name remembered by a browser and since dropped --
    // a build without vLLM, a provider renamed -- leaves the picker matching
    // nothing, `chosenProvider` undefined, and the model and context fields
    // describing a server nobody chose.
    provider: setting(
      this.provider,
      (d) => d.provider,
      amongst((d) => providerNames(d)),
    ),
    model: setting(this.model, (d) => d.model, asText),
    baseUrl: setting(this.baseUrl, (d) => d.base_url, asText),
    region: setting(this.region, (d) => d.region, asText),
    // Remembered like the rest: which sites a shopper trusts is a standing
    // answer, not something they retype for every search.
    sources: setting(this.sources, (d) => d.sources, asText),
    results: setting(this.results, (d) => d.results, asNumber),
    top: setting(this.top, (d) => d.top, asNumber),
    // The three the shopper sets once and shops under for weeks -- a budget, a
    // rating floor -- so they are remembered like the rest. `asNumberOrNull`
    // for all four, because a cleared box is an answer here: "no bound", and
    // for the cache "fetch everything fresh".
    maxPrice: setting(this.maxPrice, (d) => d.max_price, asNumberOrNull),
    minRating: setting(this.minRating, (d) => d.min_rating, asNumberOrNull),
    minReviews: setting(this.minReviews, (d) => d.min_reviews, asNumberOrNull),
    cacheTtl: setting(this.cacheTtl, (d) => d.cache_ttl, asNumberOrNull),
    // The same check, and the row that most needed it: a cast is not one, and
    // `SortBy` is a union the server is free to add to and drop from. Restored
    // unchecked, a criterion no longer offered left the Rank by select showing
    // nothing and the run refused by Python for a value nobody could see.
    sortBy: setting(
      this.sortBy,
      (d) => d.sort_by,
      amongst<SortBy>((d) => d.sort_options),
    ),
    temperature: setting(this.temperature, (d) => d.temperature, asNumber),
    // The one field a remembered `null` has to win on. Cleared, this box means
    // "whatever the server defaults to" -- what `numCtxHint` names -- which is a
    // choice and not an absence, so `null` is a value its parser accepts rather
    // than rejects. Settings saved before the field existed carry no key at all,
    // and `restore` leaves those to the seeded default.
    numCtx: setting(this.numCtx, (d) => d.num_ctx, asNumberOrNull),
    thinking: setting(this.thinking, (d) => toThinking(d.think), asThinking),
    fetchPages: setting(this.fetchPages, (d) => d.fetch, asBoolean),
  };

  protected readonly sortOptions = computed<SortBy[]>(
    () => this.defaults()?.sort_options ?? ['score', 'price', 'rating'],
  );

  protected readonly providerOptions = computed<ProviderOption[]>(
    () => this.defaults()?.provider_options ?? [],
  );

  /** The row for the provider currently chosen, which carries its defaults and
   *  what it can be told per request. Absent before the server's defaults land. */
  protected readonly chosenProvider = computed<ProviderOption | undefined>(() =>
    this.providerOptions().find((option) => option.name === this.provider()),
  );

  /** What to call this server on screen -- "Ollama", "vLLM". Falls back to the
   *  bare name, which is what a provider the server knows and this build does not
   *  would be. */
  protected readonly providerLabel = computed(
    () => this.chosenProvider()?.label ?? this.provider(),
  );

  /** Whether the context window is a per-run setting at all. It is not for vLLM,
   *  which fixes it with `--max-model-len` when it starts, so the field is
   *  disabled rather than left there to be filled in and ignored. */
  protected readonly takesNumCtx = computed(() => this.chosenProvider()?.takes_num_ctx ?? true);

  /** Cleared, the field means "whatever the server defaults to" -- so name it. */
  protected readonly numCtxHint = computed(() => {
    if (!this.takesNumCtx()) {
      return `Fixed when ${this.providerLabel()} starts`;
    }
    const fallback = this.defaults()?.num_ctx;
    return fallback ? `The default (${fallback})` : "Ollama's own (4096)";
  });

  /**
   * What the model dropdown offers: everything the server reported, plus the
   * name currently chosen if that is not among them, each marked with whatever
   * is wrong with it.
   *
   * Empty means there is nothing to pick from -- the server was unreachable, or
   * has nothing loaded -- and the field falls back to a text box, because a
   * dropdown with one unusable entry would be worse than typing.
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

  /**
   * Every number field, in the order the form draws them.
   *
   * The key is the one the server ships the range under, the one its refusals
   * name and the one the value is sent as, so this one table answers "what may
   * this box hold", "which box was the run refused for" and "what does it say
   * on it". The template loops over it: written out one at a time these were
   * eight blocks of identical markup, and the ninth setting would have been
   * twenty more lines of it.
   */
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
    field('cache_ttl', 'Cache pages for', this.cacheTtl, {
      hint: 'Seconds a page, and the answer about it, stay usable. 0 is off.',
    }),
  ];

  /** The ranges the server declared, by the key each field is sent under. Empty
   *  until the defaults land, which is a form that holds nothing to anything yet. */
  protected readonly limits = computed<Record<string, Limit>>(() => this.defaults()?.limits ?? {});

  /**
   * What the page itself can say is wrong with a field, by the key it is sent
   * under. Empty is a form that can be submitted.
   *
   * Every one of these is a rule the server declared and this only applies: the
   * numbers against the ranges `config.LIMITS` shipped, the sources against the
   * sentence `GET /api/sources` answered with. Nothing here decides anything the
   * server would not, and the server still checks all of it -- this is the
   * earlier line, not the only one (ADR-0033).
   */
  protected readonly problems = computed<Record<string, string>>(() => {
    const problems: Record<string, string> = {};
    const limits = this.limits();
    for (const { key, value: held } of this.numberFields) {
      const limit = limits[key];
      const value = held();
      // A cleared box means "use the default" (ADR-0012) rather than a number to
      // hold to a range -- and it is the only way to ask for the server's own.
      if (limit && value !== null && (value < limit.min || value > limit.max)) {
        problems[key] = `Between ${limit.min} and ${limit.max}.`;
      }
    }
    const sources = this.sourcesProblem();
    if (sources) {
      problems['sources'] = sources;
    }
    return problems;
  });

  /**
   * What the server said about the sources field, while it is still about what
   * the field holds.
   *
   * An answer names the spec it was about, so one that arrived for text since
   * typed over is dropped rather than shown against the new value -- which would
   * be a field marked for a mistake it no longer has.
   */
  private readonly sourcesProblem = computed(() => {
    const checked = this.checked();
    return checked && checked.sources === this.sources().trim() ? checked.error : '';
  });

  /**
   * What to show under each field: what the page worked out, and -- for a field
   * it has no rule of its own for -- what the server said when it refused the run.
   *
   * The page's own wins where both have something to say, because it is about
   * what the box holds now and the server's is about what was sent.
   */
  protected readonly notes = computed<Record<string, string>>(() => {
    const problems = this.problems();
    const rejected = this.rejected();
    if (!rejected || problems[rejected.field]) {
      return problems;
    }
    return { ...problems, [rejected.field]: rejected.message };
  });

  /**
   * How many settings have something to say about them, for the summary to carry.
   *
   * The count is what the summary shows once the Settings panel is shut again,
   * and what the effect in the constructor opens it on in the first place --
   * which is reachable without touching a thing: a remembered source or a
   * remembered number the server no longer takes is restored, checked and marked
   * before the form is first drawn.
   */
  protected readonly flagged = computed(() => Object.keys(this.notes()).length);

  /**
   * What a cleared number box falls back to, named in the box itself.
   *
   * An empty field means "use the default" (ADR-0012) -- a real answer, and the
   * only way to ask for the server's own -- but an empty box says nothing about
   * which number that is. The context window field already names its own; these
   * are the rest, off the same defaults every field was seeded from.
   */
  protected readonly placeholders = computed<Record<string, string>>(() => {
    const named: Record<string, string> = {};
    const defaults = this.defaults();
    if (defaults) {
      named['results'] = `${defaults.results}`;
      named['top'] = `${defaults.top}`;
      named['temperature'] = `${defaults.temperature}`;
      named['cache_ttl'] = `${defaults.cache_ttl}`;
    }
    // Its own sentence rather than a bare number: cleared, this one falls back to
    // whatever the server defaults to, which is a different answer per provider.
    named['num_ctx'] = this.numCtxHint();
    // Not off the defaults, because their default is `null`: an empty box here
    // is the whole answer rather than a stand-in for a number, so it says so.
    named['max_price'] = NO_LIMIT;
    named['min_rating'] = NO_LIMIT;
    named['min_reviews'] = NO_LIMIT;
    return named;
  });

  /** Nothing to shop for, or a field the page already knows the server would
   *  refuse. The second is the point: the ranges and the sources check cost no
   *  model, no network and no minute of waiting, so they are not worth a run. */
  protected readonly canSubmit = computed(
    () => this.request().trim().length > 0 && Object.keys(this.problems()).length === 0,
  );

  constructor() {
    // The server's defaults arrive after the form has already rendered, so seed
    // the fields when they land. Seeding runs untracked because it reads the very
    // fields it fills in -- tracked, the effect would depend on its own writes and
    // reset the form on every keystroke.
    effect(() => {
      const defaults = this.defaults();
      if (defaults) {
        untracked(() => this.seed(defaults));
      }
    });

    // Open the settings the first time there is something in them to read. A
    // mark on a box inside a closed panel is a mark nobody sees, which is the
    // whole of what ADR-0033 asks the form to do with a refusal -- and where the
    // mark is also what disables the button, leaving it shut is a dead end: a
    // page that will not search and will not say why. Closing it again is the
    // reader's to do, since this fires only when the marks themselves change.
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
    // A remembered value is one nobody is about to type, so nothing else would
    // ever ask about it: a browser holding a bad source would find out a run
    // later, which is the whole complaint.
    this.sourcesChanged();
  }

  protected submit(): void {
    if (!this.canSubmit() || this.running()) {
      return;
    }
    this.remember();
    this.search.emit({
      request: this.request().trim(),
      provider: this.provider(),
      model: this.model().trim(),
      base_url: this.baseUrl().trim(),
      region: this.region().trim(),
      sources: this.sources().trim(),
      results: this.results(),
      top: this.top(),
      max_price: this.maxPrice(),
      min_rating: this.minRating(),
      min_reviews: this.minReviews(),
      cache_ttl: this.cacheTtl(),
      sort_by: this.sortBy(),
      temperature: this.temperature(),
      num_ctx: this.numCtx(),
      think: fromThinking(this.thinking()),
      fetch: this.fetchPages(),
    });
  }

  protected useExample(example: string): void {
    this.request.set(example);
  }

  /**
   * Another provider was picked: its model and its address come with it.
   *
   * Left alone, the two fields would still hold the last provider's pair -- an
   * Ollama tag on port 11434 asked of a vLLM -- which is a run that fails for a
   * reason nothing on the form explains. The server is then asked what it has,
   * because the model list belongs to one server.
   */
  protected providerChanged(): void {
    const option = this.chosenProvider();
    if (option) {
      this.model.set(option.model);
      this.baseUrl.set(option.base_url);
    }
    this.serverChanged();
  }

  /**
   * The sources field was left: ask the server what it makes of what it holds.
   *
   * On leaving rather than on every keystroke. Half a spec is not a mistake --
   * `rtings.co` is a site on the way to `rtings.com` -- and a request per
   * character would mark the field for every one of them. Clicking Find products
   * leaves the field first, so the check still happens before the run.
   */
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

  /**
   * Let anything this browser remembered win over the seeded defaults.
   *
   * A key that is absent was never remembered -- a settings blob written before
   * the field existed -- and leaves the seeded default standing. A key that is
   * there but holds something its parser will not take is ignored the same way.
   */
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

/** Reads one remembered value, or undefined for anything it will not take.
 *
 * The server's defaults come with it, because two of these are about what this
 * server currently offers rather than about the shape of the value: whether a
 * provider or a sort criterion is still one of them is a question only the
 * defaults can answer, and they have landed by the time anything is restored. */
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

/**
 * A remembered name the server still offers, and nothing else.
 *
 * The same guard `asThinking` is, over a list that comes down with the defaults
 * rather than one written here: which providers exist and which criteria a run
 * can be sorted by are the server's to say, and a form holding a name it dropped
 * is a field describing something that is not there. Anything else is undefined,
 * which leaves the seeded default standing -- the answer a browser with nothing
 * remembered would have got.
 */
function amongst<T extends string>(
  offered: (defaults: AgentDefaults) => readonly string[],
): Parser<T> {
  return (raw, defaults) =>
    typeof raw === 'string' && offered(defaults).includes(raw) ? (raw as T) : undefined;
}

/** The providers this server offers, by name. */
function providerNames(defaults: AgentDefaults): string[] {
  return defaults.provider_options.map((option) => option.name);
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
