import { Component, computed, effect, input, output, signal, untracked } from '@angular/core';
import type { WritableSignal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import type { AgentDefaults, ModelStatus, SearchOptions, SortBy } from '../agent.types';

/** The three states of Ollama's thinking mode, as a `<select>` can hold them. */
type Thinking = 'default' | 'on' | 'off';

/** One entry in the model dropdown. `installed` is false for a name Ollama has
 *  not pulled -- kept in the list so a remembered setting is never silently
 *  swapped for someone else's model. */
export interface ModelOption {
  name: string;
  installed: boolean;
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

  readonly search = output<SearchOptions>();
  readonly stop = output<void>();
  /** Ask for the model list of another Ollama, when the server field changes. */
  readonly refresh = output<string>();

  protected readonly examples = EXAMPLES;

  protected readonly request = signal('');
  protected readonly model = signal('');
  protected readonly baseUrl = signal('');
  protected readonly region = signal('us-en');
  protected readonly results = signal(10);
  protected readonly top = signal(3);
  protected readonly sortBy = signal<SortBy>('score');
  protected readonly temperature = signal(0);
  protected readonly numCtx = signal<number | null>(null);
  protected readonly thinking = signal<Thinking>('default');
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
    model: setting(this.model, (d) => d.model, asText),
    baseUrl: setting(this.baseUrl, (d) => d.base_url, asText),
    region: setting(this.region, (d) => d.region, asText),
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
    thinking: setting(this.thinking, (d) => toThinking(d.think), asText as Parser<Thinking>),
    fetchPages: setting(this.fetchPages, (d) => d.fetch, asBoolean),
  };

  protected readonly sortOptions = computed<SortBy[]>(
    () => this.defaults()?.sort_options ?? ['score', 'price', 'rating'],
  );

  /** Cleared, the field means "whatever the server defaults to" -- so name it. */
  protected readonly numCtxHint = computed(() => {
    const fallback = this.defaults()?.num_ctx;
    return fallback ? `The default (${fallback})` : "Ollama's own (4096)";
  });

  /**
   * What the model dropdown offers: everything `ollama list` reported, plus the
   * name currently chosen if that is not among them.
   *
   * Empty means there is nothing to pick from -- Ollama was unreachable, or has
   * pulled nothing -- and the field falls back to a text box, because a dropdown
   * with one unusable entry would be worse than typing.
   */
  protected readonly modelOptions = computed<ModelOption[]>(() => {
    const installed = this.status()?.models ?? [];
    if (!installed.length) {
      return [];
    }
    const options = installed.map((name) => ({ name, installed: true }));
    const chosen = this.model().trim();
    if (chosen && !installed.includes(chosen)) {
      options.unshift({ name: chosen, installed: false });
    }
    return options;
  });

  protected readonly canSubmit = computed(() => this.request().trim().length > 0);

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
  }

  protected submit(): void {
    if (!this.canSubmit() || this.running()) {
      return;
    }
    this.remember();
    this.search.emit({
      request: this.request().trim(),
      model: this.model().trim(),
      base_url: this.baseUrl().trim(),
      region: this.region().trim(),
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

  /** The Ollama server field was left: whatever is pulled there is a new list. */
  protected serverChanged(): void {
    const url = this.baseUrl().trim();
    if (url) {
      this.refresh.emit(url);
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

function toThinking(value: boolean | null): Thinking {
  if (value === null) {
    return 'default';
  }
  return value ? 'on' : 'off';
}

function fromThinking(value: Thinking): boolean | null {
  if (value === 'default') {
    return null;
  }
  return value === 'on';
}
