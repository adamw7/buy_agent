import { Component, computed, effect, input, output, signal, untracked } from '@angular/core';
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
    this.model.set(defaults.model);
    this.baseUrl.set(defaults.base_url);
    this.region.set(defaults.region);
    this.results.set(defaults.results);
    this.top.set(defaults.top);
    this.sortBy.set(defaults.sort_by);
    this.temperature.set(defaults.temperature);
    this.numCtx.set(defaults.num_ctx);
    this.thinking.set(toThinking(defaults.think));
    this.fetchPages.set(defaults.fetch);
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
    const settings = {
      model: this.model(),
      baseUrl: this.baseUrl(),
      region: this.region(),
      results: this.results(),
      top: this.top(),
      sortBy: this.sortBy(),
      temperature: this.temperature(),
      numCtx: this.numCtx(),
      thinking: this.thinking(),
      fetchPages: this.fetchPages(),
    };
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    } catch {
      // A browser that refuses storage still gets a working form.
    }
  }

  private restore(): void {
    let saved: Partial<Record<string, unknown>>;
    try {
      saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? 'null') ?? {};
    } catch {
      return;
    }
    if (typeof saved !== 'object') {
      return;
    }
    const text = (key: string) => (typeof saved[key] === 'string' ? (saved[key] as string) : null);
    const number = (key: string) =>
      typeof saved[key] === 'number' ? (saved[key] as number) : null;

    this.model.set(text('model') ?? this.model());
    this.baseUrl.set(text('baseUrl') ?? this.baseUrl());
    this.region.set(text('region') ?? this.region());
    this.results.set(number('results') ?? this.results());
    this.top.set(number('top') ?? this.top());
    this.sortBy.set((text('sortBy') as SortBy | null) ?? this.sortBy());
    this.temperature.set(number('temperature') ?? this.temperature());
    this.numCtx.set(number('numCtx'));
    this.thinking.set((text('thinking') as Thinking | null) ?? this.thinking());
    if (typeof saved['fetchPages'] === 'boolean') {
      this.fetchPages.set(saved['fetchPages']);
    }
  }
}

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
