import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import type { Subscription } from 'rxjs';

import { AgentService } from './agent';
import type {
  AgentDefaults,
  LogLine,
  ModelStatus,
  SearchOptions,
  SearchResult,
} from './agent.types';
import { ProductCard } from './product-card/product-card';
import { ProgressLog } from './progress-log/progress-log';
import { SearchForm } from './search-form/search-form';

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

  private run: Subscription | null = null;

  constructor() {
    inject(DestroyRef).onDestroy(() => this.run?.unsubscribe());

    this.agent.defaults().subscribe({
      next: (defaults) => {
        this.defaults.set(defaults);
        this.refreshModels(defaults.base_url);
      },
      error: () => this.failure.set('Could not reach the agent server. Is it still running?'),
    });
  }

  protected refreshModels(baseUrl?: string): void {
    const target = baseUrl ?? this.status()?.base_url ?? this.defaults()?.base_url;
    if (!target) {
      return;
    }
    this.agent.models(target).subscribe({
      next: (status) => this.status.set(status),
      error: () => this.status.set({ base_url: target, reachable: false, models: [] }),
    });
  }

  protected start(options: SearchOptions): void {
    this.run?.unsubscribe();
    this.logs.set([]);
    this.result.set(null);
    this.failure.set(null);
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
      { level: 'WARNING', logger: 'buy_agent', message: 'Stopped.' },
    ]);
  }
}
