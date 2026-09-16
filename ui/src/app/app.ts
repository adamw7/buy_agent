import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import type { Subscription } from 'rxjs';

import { AgentService } from './agent';
import type {
  AgentDefaults,
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

  protected readonly defaults = signal<AgentDefaults | null>(null);
  protected readonly status = signal<ModelStatus | null>(null);
  protected readonly logs = signal<LogLine[]>([]);
  protected readonly result = signal<SearchResult | null>(null);
  protected readonly failure = signal<string | null>(null);
  /** The failure again, where it was about one setting: the form marks that box. */
  protected readonly rejected = signal<Rejection | null>(null);
  /** What the server made of the sources field, for the form to show. */
  protected readonly sourcesCheck = signal<SourcesCheck | null>(null);
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

  /** What came of each payment, by the name of the product it bought. */
  protected readonly receipts = signal<Record<string, Receipt>>({});

  /** A payment that did not happen. */
  protected readonly payFailed = signal<string | null>(null);

  /** Whether this page may pay at all: the server can, and the run asked it to. */
  protected readonly canPay = computed(
    () => (this.defaults()?.pay_available ?? false) && (this.ranWith()?.pay ?? false),
  );

  /** The rail the run was started with, as the row Python sent for it -- so the
   *  confirmation says whether anybody is about to be charged without the page
   *  deciding that from a name. */
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

  /** What the finished run may be re-ordered by: the server's own list, which is
   *  the same one the form's Rank by field is built from -- offered in two
   *  places and chosen in neither. */
  protected readonly sortOptions = computed<SortBy[]>(() => this.defaults()?.sort_options ?? []);

  /** Everything the agent found beyond those, kept because it was still ranked. */
  protected readonly rest = computed(() => {
    const result = this.result();
    return result ? result.products.slice(result.top_n) : [];
  });

  /** What to do about a model server that did not answer, shown under the pill. */
  protected readonly unreachable = computed(() => {
    const server = this.status();
    // Nothing while a listing is in flight: the remedy under the pill is about the last
    // answer, and leaving it up beside "Asking Ollama…" tells somebody who has just run
    // that command that it did not work, before anything has been asked.
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
  /** The two requests whose answer is about a question the page can have moved on from:
   *  a re-sort of products the next search is about to replace, and a listing of a
   *  server the form is no longer pointed at. Held so the newer ask cancels the older,
   *  an answer that arrives second not being the answer to the second question. */
  private reorder: Subscription | null = null;
  private listing: Subscription | null = null;
  /** A payment in flight. */
  private pay: Subscription | null = null;

  constructor() {
    inject(DestroyRef).onDestroy(() => {
      this.run?.unsubscribe();
      this.reorder?.unsubscribe();
      this.listing?.unsubscribe();
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
    // Two of these can be in flight at once -- the provider picker fills in the
    // address and both ask -- and the slower one answering last would leave the
    // pill and the model list describing a server the form is not pointed at.
    this.listing?.unsubscribe();
    // Said before the request rather than after it: this is the wait, and a
    // superseded listing leaves it standing for the newer one to clear, which is
    // what it is -- still asking, about a different server.
    this.asking.set(target);
    this.listing = this.agent.models(target).subscribe({
      next: (status) => {
        this.status.set(status);
        this.asking.set(null);
      },
      // The agent server itself did not answer, so nothing came back to name the
      // provider with -- the defaults it served earlier are where that name is.
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
    // A re-sort still in flight is about the run being replaced: left running, its answer lands on
    // a cleared page and puts the last search's products back under a progress panel narrating the
    // next one.
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
    // A new run is a new set of products: last run's receipts belong to
    // products that are no longer on the page.
    this.ranWith.set(options);
    this.receipts.set({});
    this.paying.set(null);
    this.payFailed.set(null);

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

  /** Ask for the same products in another order, without searching for them again. */
  protected resort(control: HTMLSelectElement): void {
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
      })
      .subscribe({
        next: (result) => {
          this.result.set(result);
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
    // The rank is where this product sits in the list being sent, which is what
    // the server indexes by; the name is what the answer is filed under here.
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

  /** Whether the run this answer is about is still the one on screen. A payment in
   *  flight is not cancelled the way a re-sort is -- unsubscribing aborts the request,
   *  and a request that may have moved money is nobody's to abandon halfway -- so it
   *  runs to the end and its answer is dropped here instead. Receipts are filed by
   *  product name (ADR-0035), and a second search for the same thing finds the same
   *  names, so a late receipt left to land marks a product this run never bought. */
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
      // The only line the browser writes itself, so it is the only one timed off the browser's
      // clock -- in the format Python sends the rest in, since the panel shows them in one column.
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
