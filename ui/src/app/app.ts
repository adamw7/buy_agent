import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import type { Subscription } from 'rxjs';

import { AgentService } from './agent';
import type {
  AgentDefaults,
  LogLine,
  ModelSource,
  ModelStatus,
  SearchOptions,
  SearchResult,
  SourcesCheck,
} from './agent.types';
import { ProductCard } from './product-card/product-card';
import { ProgressLog } from './progress-log/progress-log';
import { SearchForm } from './search-form/search-form';
import type { Rejection } from './search-form/search-form';

/**
 * The page: ask for something, watch the agent work, read the ranked answer.
 *
 * The pipeline itself stays where it was -- this is a second front end onto the
 * same `BuyAgent.run()` the CLI drives, not a second implementation of it.
 */
@Component({
  selector: 'app-root',
  imports: [SearchForm, ProgressLog, ProductCard],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  private readonly agent = inject(AgentService);

  protected readonly defaults = signal<AgentDefaults | null>(null);
  protected readonly status = signal<ModelStatus | null>(null);
  protected readonly logs = signal<LogLine[]>([]);
  protected readonly result = signal<SearchResult | null>(null);
  protected readonly failure = signal<string | null>(null);
  /** The failure again, where it was about one setting: the form marks that box.
   *  Kept beside `failure` rather than derived from it, because which field a
   *  message is about is Python's answer and not a sentence to read (ADR-0033). */
  protected readonly rejected = signal<Rejection | null>(null);
  /** What the server made of the sources field, for the form to show. */
  protected readonly sourcesCheck = signal<SourcesCheck | null>(null);
  protected readonly running = signal(false);
  protected readonly started = signal(false);

  /** The best few: the same ones the CLI logs at the end of a run. */
  protected readonly highlighted = computed(() => {
    const result = this.result();
    return result ? result.products.slice(0, result.top_n) : [];
  });

  /** Everything the agent found beyond those, kept because it was still ranked. */
  protected readonly rest = computed(() => {
    const result = this.result();
    return result ? result.products.slice(result.top_n) : [];
  });

  /**
   * What to do about a model server that did not answer, shown under the pill.
   *
   * Python's sentence, not one written here: the provider already knows what to
   * start, what key to set and what tag to pull, and a second wording in
   * TypeScript would be a second thing to keep true. Null whenever the server is
   * reachable -- and when nothing came back to ask, which is the agent server
   * itself being down and a failure the pill cannot explain.
   */
  protected readonly unreachable = computed(() => {
    const server = this.status();
    return server && !server.reachable ? (server.hint ?? null) : null;
  });

  private run: Subscription | null = null;

  constructor() {
    inject(DestroyRef).onDestroy(() => this.run?.unsubscribe());

    this.agent.defaults().subscribe({
      next: (defaults) => {
        this.defaults.set(defaults);
        this.refreshModels({ provider: defaults.provider, base_url: defaults.base_url });
      },
      error: () => this.failure.set('Could not reach the agent server. Is it still running?'),
    });
  }

  /**
   * Ask what a model server is serving: the one named, or the one already shown.
   *
   * The provider travels with the address because the two are one question --
   * the same URL is asked one way for Ollama and another for vLLM, and half an
   * answer would list the wrong server's models.
   */
  protected refreshModels(source?: ModelSource): void {
    const target = source ?? this.current();
    if (!target) {
      return;
    }
    this.agent.models(target).subscribe({
      next: (status) => this.status.set(status),
      // The agent server itself did not answer, so nothing came back to name the
      // provider with -- the defaults it served earlier are where that name is.
      error: () =>
        this.status.set({
          ...target,
          label: this.labelFor(target.provider),
          reachable: false,
          models: [],
        }),
    });
  }

  /** The server the pill is currently reporting on, for a re-ask with no argument.
   *  `ModelStatus` and `AgentDefaults` both name a provider and an address, so
   *  whichever of the two has arrived answers the same question. */
  private current(): ModelSource | null {
    const shown = this.status() ?? this.defaults();
    return shown ? { provider: shown.provider, base_url: shown.base_url } : null;
  }

  /** What to call a provider, out of the rows the server sent with the defaults. */
  private labelFor(provider: string): string {
    const option = this.defaults()?.provider_options.find((row) => row.name === provider);
    return option?.label ?? provider;
  }

  /**
   * Ask what the sources field holds, before a run is worth starting.
   *
   * The form has no rule of its own for a source, so the answer comes from the
   * same `parse_sources` a run would have used (ADR-0033). An empty field is the
   * whole web, which is nothing to ask about; a server that did not answer
   * leaves the field unmarked, since the banner already says the agent is down.
   */
  protected checkSources(sources: string): void {
    if (!sources) {
      this.sourcesCheck.set(null);
      return;
    }
    this.agent.checkSources(sources).subscribe({
      next: (check) => this.sourcesCheck.set(check),
      error: () => this.sourcesCheck.set(null),
    });
  }

  protected start(options: SearchOptions): void {
    this.run?.unsubscribe();
    this.logs.set([]);
    this.result.set(null);
    this.failure.set(null);
    this.rejected.set(null);
    this.running.set(true);
    this.started.set(true);

    this.run = this.agent.search(options).subscribe({
      next: (event) => {
        if (event.kind === 'log') {
          this.logs.update((lines) => [...lines, event.line]);
        } else if (event.kind === 'result') {
          this.result.set(event.result);
        } else {
          this.failure.set(event.message);
          // Named a field, so the form can mark the box it came out of rather
          // than leaving the banner to be read against ten settings.
          this.rejected.set(event.field ? { field: event.field, message: event.message } : null);
        }
      },
      error: (error: Error) => {
        this.failure.set(error.message);
        this.running.set(false);
      },
      complete: () => this.running.set(false),
    });
  }

  protected stop(): void {
    this.run?.unsubscribe();
    this.run = null;
    this.running.set(false);
    this.logs.update((lines) => [
      ...lines,
      // The only line the browser writes itself, so it is the only one timed off
      // the browser's clock -- in the format Python sends the rest in, since the
      // panel shows them in one column.
      { time: now(), level: 'WARNING', logger: 'buy_agent', message: 'Stopped.' },
    ]);
  }
}

/** The wall clock as Python's `%H:%M:%S` writes it, for the one line above. */
function now(): string {
  return new Date().toTimeString().slice(0, 8);
}
