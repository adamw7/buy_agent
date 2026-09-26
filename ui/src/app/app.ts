import {
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  afterNextRender,
  computed,
  inject,
  signal,
} from '@angular/core';
import type { Subscription } from 'rxjs';

import { AgentService } from './agent';
import type {
  AgentDefaults,
  BoundsCheck,
  LogLine,
  ModelSource,
  ModelStatus,
  RailOption,
  RankedProduct,
  Receipt,
  SearchOptions,
  SearchResult,
  SortBy,
  SourcesCheck,
} from './agent.types';
import { ProductCard } from './product-card/product-card';
import { ProgressLog } from './progress-log/progress-log';
import { filename, saveText } from './save';
import { SearchForm } from './search-form/search-form';
import type { Rejection } from './search-form/search-form';

/** The page: ask for something, watch the agent work, read the ranked answer. */
@Component({
  selector: 'app-root',
  imports: [SearchForm, ProgressLog, ProductCard],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  private readonly agent = inject(AgentService);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);

  protected readonly defaults = signal<AgentDefaults | null>(null);
  protected readonly status = signal<ModelStatus | null>(null);
  protected readonly logs = signal<LogLine[]>([]);
  protected readonly result = signal<SearchResult | null>(null);
  protected readonly failure = signal<string | null>(null);
  /** The failure again, where it was about one setting: the form marks that box. */
  protected readonly rejected = signal<Rejection | null>(null);
  /** What the server made of the sources field, for the form to show. */
  protected readonly sourcesCheck = signal<SourcesCheck | null>(null);
  /** Bounds the request states in words, for the form to offer (ADR-0059). */
  protected readonly boundsCheck = signal<BoundsCheck | null>(null);
  protected readonly running = signal(false);
  protected readonly started = signal(false);
  /** A run the reader ended themselves. */
  protected readonly stopped = signal(false);
  /** The model server currently being asked what it serves, or null for none in flight. */
  private readonly asking = signal<ModelSource | null>(null);
  /** Whether a listing is in flight. */
  protected readonly checking = computed(() => this.asking() !== null);
  /** A re-sort in flight. */
  protected readonly reordering = signal(false);
  /** A re-sort that did not happen, said beside the results it did not change. */
  protected readonly reorderFailed = signal<string | null>(null);

  /** The settings the run on screen was started with. */
  private readonly ranWith = signal<SearchOptions | null>(null);

  /** The product being paid for, by name -- one payment at a time, page-wide. */
  protected readonly paying = signal<string | null>(null);

  /** Receipts by product name. Typed with `undefined` itself: the specs compile
   *  without `noUncheckedIndexedAccess`, and the template's `?? null` needs it. */
  protected readonly receipts = signal<Record<string, Receipt | undefined>>({});

  /** A payment that did not happen. */
  protected readonly payFailed = signal<string | null>(null);

  /** Whether this page may pay at all: the server can, and the run asked it to. */
  protected readonly canPay = computed(
    () => (this.defaults()?.pay_available ?? false) && (this.ranWith()?.pay ?? false),
  );

  /** Whether cards may ask for a screenshot (the server's answer). */
  protected readonly screenshots = computed(() => this.defaults()?.screenshots ?? false);

  /** The run's rail row, so the confirmation can say whether anyone is charged. */
  protected readonly payRail = computed<RailOption | null>(() => {
    const name = this.ranWith()?.rail;
    const rows = this.defaults()?.rail_options ?? [];
    return rows.find((row) => row.name === name) ?? null;
  });

  /** The best few: the same ones the CLI logs at the end of a run. */
  protected readonly highlighted = computed(() => {
    const result = this.result();
    return result ? result.products.slice(0, result.top_n) : [];
  });

  /** The criteria a finished run may be re-sorted by, from the server. */
  protected readonly sortOptions = computed<SortBy[]>(() => this.defaults()?.sort_options ?? []);

  /** Everything the agent found beyond those, kept because it was still ranked. */
  protected readonly rest = computed(() => {
    const result = this.result();
    return result ? result.products.slice(result.top_n) : [];
  });

  /** What to do about a model server that did not answer, shown under the pill. */
  protected readonly unreachable = computed(() => {
    const server = this.status();
    // Hidden while asking: it is about the previous answer.
    if (this.checking() || !server || server.reachable) {
      return null;
    }
    return server.hint ?? null;
  });

  /** What to call the server being asked about, for the pill to say while it is being asked. */
  protected readonly serverLabel = computed(() =>
    this.labelFor(
      this.asking()?.provider ?? this.status()?.provider ?? this.defaults()?.provider ?? '',
    ),
  );

  private run: Subscription | null = null;
  /** Requests a newer ask supersedes; held so it can cancel them. */
  private reorder: Subscription | null = null;
  private listing: Subscription | null = null;
  private sources: Subscription | null = null;
  /** Likewise, for the request field's bounds reading. */
  private bounds: Subscription | null = null;
  /** A payment in flight. */
  private pay: Subscription | null = null;

  constructor() {
    inject(DestroyRef).onDestroy(() => {
      this.run?.unsubscribe();
      this.reorder?.unsubscribe();
      this.listing?.unsubscribe();
      this.sources?.unsubscribe();
      this.bounds?.unsubscribe();
      this.pay?.unsubscribe();
    });

    this.agent.defaults().subscribe({
      next: (defaults) => {
        this.defaults.set(defaults);
        this.refreshModels({ provider: defaults.provider, base_url: defaults.base_url });
      },
      error: () => this.failure.set('Could not reach the agent server. Is it still running?'),
    });
  }

  /** Ask what a model server is serving: the one named, or the one already shown. */
  protected refreshModels(source?: ModelSource): void {
    const target = source ?? this.current();
    if (!target) {
      return;
    }
    // Cancel an older listing, whose late answer would describe the wrong server.
    this.listing?.unsubscribe();
    // Set before asking; a superseded listing leaves it for the newer one to clear.
    this.asking.set(target);
    this.listing = this.agent.models(target).subscribe({
      next: (status) => {
        this.status.set(status);
        this.asking.set(null);
      },
      // The agent server did not answer; name the provider from the defaults.
      error: () => {
        this.status.set({
          ...target,
          label: this.labelFor(target.provider),
          reachable: false,
          models: [],
        });
        this.asking.set(null);
      },
    });
  }

  /** The server the pill is currently reporting on, for a re-ask with no argument. */
  private current(): ModelSource | null {
    const shown = this.status() ?? this.defaults();
    return shown ? { provider: shown.provider, base_url: shown.base_url } : null;
  }

  /** What to call a provider, out of the rows the server sent with the defaults. */
  private labelFor(provider: string): string {
    const option = this.defaults()?.provider_options.find((row) => row.name === provider);
    return option?.label ?? provider;
  }

  /** Ask what the sources field holds, before a run is worth starting. */
  protected checkSources(sources: string): void {
    this.sources?.unsubscribe();
    if (!sources) {
      this.sourcesCheck.set(null);
      return;
    }
    this.sources = this.agent.checkSources(sources).subscribe({
      next: (check) => this.sourcesCheck.set(check),
      error: () => this.sourcesCheck.set(null),
    });
  }

  /** Ask what the request itself says about the bounds, before a run is started. */
  protected checkBounds(request: string): void {
    this.bounds?.unsubscribe();
    if (!request) {
      this.boundsCheck.set(null);
      return;
    }
    this.bounds = this.agent.checkBounds(request).subscribe({
      next: (check) => this.boundsCheck.set(check),
      error: () => this.boundsCheck.set(null),
    });
  }

  /** The refused box holds something else now: drop the banner too, returning the page
   *  to how it was before the refused run. */
  protected dropRefusal(): void {
    this.failure.set(null);
    this.rejected.set(null);
    if (!this.logs().length && !this.result()) {
      this.started.set(false);
    }
  }

  protected start(options: SearchOptions): void {
    this.run?.unsubscribe();
    // Cancel a re-sort of the run being replaced.
    this.reorder?.unsubscribe();
    this.reorder = null;
    this.reordering.set(false);
    this.logs.set([]);
    this.result.set(null);
    this.failure.set(null);
    this.rejected.set(null);
    this.reorderFailed.set(null);
    this.stopped.set(false);
    this.running.set(true);
    this.started.set(true);
    // Receipts belong to the previous run's products.
    this.ranWith.set(options);
    this.receipts.set({});
    this.paying.set(null);
    this.payFailed.set(null);

    this.run = this.agent.search(options).subscribe({
      next: (event) => {
        if (event.kind === 'log') {
          // The first line, not the click: a run refused before it opens logs none,
          // and the box it marks is in the form this would scroll away from.
          if (!this.logs().length) {
            this.reveal('app-progress-log');
          }
          this.logs.update((lines) => [...lines, event.line]);
        } else if (event.kind === 'result') {
          this.result.set(event.result);
          this.showResults();
        } else {
          this.failure.set(event.message);
          // So the form can mark that box (ADR-0033).
          this.rejected.set(event.field ? { field: event.field, message: event.message } : null);
          // A refusal is said on its box, and the form opens the panel it is in.
          if (!event.field) {
            this.reveal('.banner.failed');
          }
        }
      },
      error: (error: Error) => {
        this.failure.set(error.message);
        this.running.set(false);
        this.reveal('.banner.failed');
      },
      complete: () => this.running.set(false),
    });
  }

  /** Scroll a finished run's results into view, if they start below the fold. */
  private showResults(): void {
    afterNextRender(
      () => {
        const landed = this.host.nativeElement.querySelector('.results, .banner.quiet');
        if (landed && landed.getBoundingClientRect().top > window.innerHeight) {
          landed.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
        }
      },
      { injector: this.injector },
    );
  }

  /** Scroll a panel the run just drew as little as shows it whole, which is not at all
   *  where it already is. With Settings open -- and a budget in the request opens them
   *  by itself -- the form alone is taller than a laptop's window, so the progress and
   *  the failure both landed below the fold, and a click that started a run looked like
   *  one that did nothing. */
  private reveal(selector: string): void {
    afterNextRender(
      () =>
        this.host.nativeElement
          .querySelector(selector)
          ?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' }),
      { injector: this.injector },
    );
  }

  /** Ask for the same products in another order, without searching for them again. */
  protected resort(event: Event): void {
    const control = event.target as HTMLSelectElement;
    const sortBy = control.value as SortBy;
    const found = this.result();
    if (!found || sortBy === found.sort_by || this.reordering()) {
      return;
    }
    this.reordering.set(true);
    this.reorderFailed.set(null);
    this.reorder = this.agent
      .rank({
        request: found.request,
        products: found.products,
        sort_by: sortBy,
        top: found.top_n,
        // The run's own scale, so the set does not vote again (ADR-0056).
        currency: this.ranWith()?.currency,
      })
      .subscribe({
        next: (result) => {
          // A re-sort answers these empty; keep the run's own (ADR-0035, ADR-0055,
          // ADR-0060).
          this.result.set({
            ...result,
            dropped: found.dropped,
            changes: found.changes,
            compared_with: found.compared_with,
          });
          this.reordering.set(false);
        },
        error: (failure: unknown) => {
          this.reorderFailed.set(
            `Could not re-order these by ${sortBy}; they are still ranked by ` +
              `${found.sort_by}. ${refusal(failure)}`,
          );
          // Put the control back to the order these products are actually in.
          control.value = found.sort_by;
          this.reordering.set(false);
        },
      });
  }

  /** Buy one of these products, having been shown that somebody approved it. */
  protected payFor(
    product: RankedProduct,
    approved: { title: string; price: number; currency: string },
  ): void {
    const found = this.result();
    const settings = this.ranWith();
    if (!found || !settings || this.paying() !== null) {
      return;
    }
    // The server indexes by rank; the receipt is filed by name.
    const { rank, name } = product;
    this.paying.set(name);
    this.payFailed.set(null);
    this.pay = this.agent
      .pay({
        products: found.products,
        rank,
        approved,
        rail: settings.rail,
        merchant_url: settings.merchant_url,
        spend_limit: settings.spend_limit,
        // As a re-sort sends it.
        currency: settings.currency,
      })
      .subscribe({
        next: ({ receipt }) => {
          if (!this.stillThisRun(settings)) {
            return;
          }
          this.receipts.update((held) => ({ ...held, [name]: receipt }));
          this.paying.set(null);
        },
        error: (failure: unknown) => {
          if (!this.stillThisRun(settings)) {
            return;
          }
          this.payFailed.set(`Nothing was bought. ${refusal(failure)}`);
          this.paying.set(null);
        },
      });
  }

  /** Whether this answer's run is still on screen. A payment is never cancelled (it
   *  may have moved money), so a late answer is dropped here instead (ADR-0035). */
  private stillThisRun(settings: SearchOptions): boolean {
    return this.ranWith() === settings;
  }

  /** Hand the finished run over as a file. */
  protected downloadResults(): void {
    saveText(
      filename('results', 'json', new Date()),
      JSON.stringify(this.result()?.products ?? [], null, 2),
      'application/json',
    );
  }

  /** Stop the run: close the stream, and say what that does and does not reach. */
  protected stop(): void {
    this.run?.unsubscribe();
    this.run = null;
    this.running.set(false);
    // What the log panel offers its transcript on.
    this.stopped.set(true);
    this.logs.update((lines) => [
      ...lines,
      // The one browser-written line, timed in Python's format.

      {
        time: now(),
        level: 'WARNING',
        logger: 'buy_agent',
        message:
          'Stopped watching. The run ends on the server at its next step — a call ' +
          'already under way to the model server finishes first, so give it a ' +
          'moment before starting another search.',
      },
    ]);
  }
}

/** The wall clock as Python's `%H:%M:%S` writes it, for the one line above. */
function now(): string {
  return new Date().toTimeString().slice(0, 8);
}

/** Why a request failed: the server's own sentence, or a guess where it sent none. */
function refusal(failure: unknown): string {
  const answered = (failure as { error?: { error?: unknown } } | null)?.error?.error;
  const said = typeof answered === 'string' ? answered.trim() : '';
  return said || 'Is the agent server still running?';
}
