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
export interface ModelOption {
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

const SETTINGS_KEY = 'buy_agent.settings';

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
  /** Ask what another server is serving, when the provider or the address changes.
   *  Both, because the address alone is not the question: the same URL is asked
   *  one way for Ollama and another for vLLM. */
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
  protected readonly results = signal(10);
  protected readonly top = signal(3);
  protected readonly sortBy = signal<SortBy>('score');
  protected readonly temperature = signal(0);
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
    provider: setting(this.provider, (d) => d.provider, asText),
    model: setting(this.model, (d) => d.model, asText),
    baseUrl: setting(this.baseUrl, (d) => d.base_url, asText),
    region: setting(this.region, (d) => d.region, asText),
    // Remembered like the rest: which sites a shopper trusts is a standing
    // answer, not something they retype for every search.
    sources: setting(this.sources, (d) => d.sources, asText),
    results: setting(this.results, (d) => d.results, asNumber),
    top: setting(this.top, (d) => d.top, asNumber),
    sortBy: setting(this.sortBy, (d) => d.sort_by, asText as Parser<SortBy>),
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
   * The number fields, by the key each is sent under.
   *
   * That key is the one the server ships its ranges under and the one its
   * refusals name, so this one table answers both "what may this box hold" and
   * "which box was the run refused for".
   */
  private readonly numbers: Record<string, () => number | null> = {
    results: this.results,
    top: this.top,
    temperature: this.temperature,
    num_ctx: this.numCtx,
  };

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
    for (const [key, held] of Object.entries(this.numbers)) {
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
  }

  /** Fill the form from the server's defaults, then let anything remembered win. */
  private seed(defaults: AgentDefaults): void {
    for (const field of Object.values(this.settings)) {
      field.seed(defaults);
    }
    this.restore();
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
  private restore(): void {
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
        field.restore((saved as Record<string, unknown>)[key]);
      }
    }
  }
}

/** Reads one remembered value, or undefined for anything it will not take. */
type Parser<T> = (raw: unknown) => T | undefined;

/** One remembered setting, with the signal's own type closed over. */
interface Setting {
  seed(defaults: AgentDefaults): void;
  value(): unknown;
  restore(raw: unknown): void;
}

function setting<T>(
  target: WritableSignal<T>,
  fromDefaults: (defaults: AgentDefaults) => T,
  parse: Parser<T>,
): Setting {
  return {
    seed: (defaults) => target.set(fromDefaults(defaults)),
    value: () => target(),
    restore: (raw) => {
      const parsed = parse(raw);
      if (parsed !== undefined) {
        target.set(parsed);
      }
    },
  };
}

const asText: Parser<string> = (raw) => (typeof raw === 'string' ? raw : undefined);
const asNumber: Parser<number> = (raw) => (typeof raw === 'number' ? raw : undefined);
const asBoolean: Parser<boolean> = (raw) => (typeof raw === 'boolean' ? raw : undefined);
const asNumberOrNull: Parser<number | null> = (raw) => (raw === null ? null : asNumber(raw));

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
