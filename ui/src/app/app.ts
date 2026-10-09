import {
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  afterNextRender,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { Title } from '@angular/platform-browser';
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
import type { PaySettings, Rejection } from './search-form/search-form';

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
  protected readonly sourcesCheck = signal<SourcesCheck | null>(null);
  /** Bounds the request states in words, for the form to offer (ADR-0059). */
  protected readonly boundsCheck = signal<BoundsCheck | null>(null);
  protected readonly running = signal(false);
  protected readonly started = signal(false);
  /** A run the reader ended themselves. */
  protected readonly stopped = signal(false);
  /** The model server being asked what it serves, while in flight. */
  private readonly asking = signal<ModelSource | null>(null);
  protected readonly checking = computed(() => this.asking() !== null);
  protected readonly reordering = signal(false);
  protected readonly reorderFailed = signal<string | null>(null);

  /** The settings the run on screen was started with. */
  private readonly ranWith = signal<SearchOptions | null>(null);

  /** The form's paying settings as they stand: paying runs no pipeline (ADR-0046). */
  protected readonly paySettings = signal<PaySettings>(NOT_PAYING);

  /** The product being paid for, by name: one payment at a time. */
  protected readonly paying = signal<string | null>(null);

  /** By product name; `undefined` for the template's `?? null`. */
  protected readonly receipts = signal<Record<string, Receipt | undefined>>({});

  protected readonly payFailed = signal<string | null>(null);

  /** The server can pay, and the form's switch, as it stands now, says to. */
  protected readonly canPay = computed(
    () => (this.defaults()?.pay_available ?? false) && this.paySettings().pay,
  );

  protected readonly screenshots = computed(() => this.defaults()?.screenshots ?? false);

  /** So the confirmation can say whether anyone is charged. */
  protected readonly payRail = computed<RailOption | null>(() => {
    const name = this.paySettings().rail;
    const rows = this.defaults()?.rail_options ?? [];
    return rows.find((row) => row.name === name) ?? null;
  });

  /** The ones the CLI logs at the end of a run. */
  protected readonly highlighted = computed<RankedProduct[]>(() => {
    const result = this.result();
    return result ? result.products.slice(0, result.top_n) : [];
  });

  /** Each criterion named by the order it gives ("cheapest first"). */
  protected readonly sortOptions = computed<{ name: SortBy; label: string }[]>(() => {
    const defaults = this.defaults();
    return (defaults?.sort_options ?? []).map((name) => ({
      name,
      // A server older than the page sends no labels.
      label: defaults?.sort_labels?.[name] ?? name,
    }));
  });

  protected readonly rest = computed<RankedProduct[]>(() => {
    const result = this.result();
    return result ? result.products.slice(result.top_n) : [];
  });

  /** How many products moved: the unchanged are listed too, and are not changes. */
  protected readonly moved = computed(
    () =>
      (this.result()?.changes ?? []).filter((change) => !UNMOVED.includes(change.movement)).length,
  );

  /** Python's remedy for a model server that did not answer, hidden while asking. */
  protected readonly unreachable = computed(() => {
    const server = this.status();
    if (this.checking() || !server || server.reachable) {
      return null;
    }
    return server.hint ?? null;
  });

  protected readonly serverLabel = computed(() =>
    this.labelFor(
      this.asking()?.provider ?? this.status()?.provider ?? this.defaults()?.provider ?? '',
    ),
  );

  /** Held so a newer ask can cancel them. */
  private run: Subscription | null = null;
  private reorder: Subscription | null = null;
  private listing: Subscription | null = null;
  private sources: Subscription | null = null;
  private bounds: Subscription | null = null;
  private pay: Subscription | null = null;

  /** The run's state in the browser tab, which may sit in the background for minutes. */
  private readonly tabTitle = computed(() => {
    const state = this.tabState();
    return state ? `${state} — ${NAME}` : NAME;
  });

  constructor() {
    const title = inject(Title);
    effect(() => title.setTitle(this.tabTitle()));

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

  private tabState(): string | null {
    if (this.running()) {
      return 'Searching…';
    }
    // The agent server failing to answer on load is no run.
    if (!this.started()) {
      return null;
    }
    const result = this.result();
    if (result) {
      return result.count ? `${result.count} found` : 'Nothing found';
    }
    if (this.stopped()) {
      return 'Stopped';
    }
    return this.failure() === null ? null : 'Failed';
  }

  /** Ask what a model server is serving: the one named, or the one already shown. */
  protected refreshModels(source?: ModelSource): void {
    const target = source ?? this.current();
    if (!target) {
      return;
    }
    // A late answer would describe the wrong server.
    this.listing?.unsubscribe();
    this.asking.set(target);
    this.listing = this.agent.models(target).subscribe({
      next: (status) => {
        this.status.set(status);
        this.asking.set(null);
      },
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

  private current(): ModelSource | null {
    const shown = this.status() ?? this.defaults();
    return shown ? { provider: shown.provider, base_url: shown.base_url } : null;
  }

  private labelFor(provider: string): string {
    const option = this.defaults()?.provider_options.find((row) => row.name === provider);
    return option?.label ?? provider;
  }

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

  protected checkBounds(request: string): void {
    this.bounds?.unsubscribe();
    if (!request) {
      this.boundsCheck.set(null);
      return;
    }
    this.bounds = this.agent.checkBounds(request).subscribe({
      next: (check) => this.boundsCheck.set(check),
      // As a request asking for nothing, so a submit waiting on it goes on.
      error: () => this.boundsCheck.set({ request, noticed: [] }),
    });
  }

  /** The refused box holds something else now: drop the banner too. */
  protected dropRefusal(): void {
    this.failure.set(null);
    this.rejected.set(null);
    this.payFailed.set(null);
    if (!this.logs().length && !this.result()) {
      this.started.set(false);
    }
  }

  protected start(options: SearchOptions): void {
    this.run?.unsubscribe();
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
    this.ranWith.set(options);
    this.receipts.set({});
    this.paying.set(null);
    this.payFailed.set(null);

    this.run = this.agent.search(options).subscribe({
      next: (event) => {
        if (event.kind === 'log') {
          // On the first line: a run refused before it opens logs none, and stays put.
          if (!this.logs().length) {
            this.reveal('app-progress-log');
          }
          this.logs.update((lines) => [...lines, event.line]);
        } else if (event.kind === 'result') {
          this.result.set(event.result);
          this.showResults();
          this.recheckAfter(true);
        } else {
          this.failure.set(event.message);
          // The form marks that box and opens its panel (ADR-0033).
          this.rejected.set(event.field ? { field: event.field, message: event.message } : null);
          if (!event.field) {
            this.reveal('.banner.failed');
          }
          // 503 is the model server's own failure (`api._STATUS`).
          if (event.status === 503) {
            this.recheckAfter(false);
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

  /** Ask the model server again where a run just contradicted the pill. */
  private recheckAfter(answered: boolean): void {
    const server = this.status();
    if (server && !this.checking() && server.reachable !== answered) {
      this.refreshModels();
    }
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

  /** Scroll a panel the run just drew as little as shows it whole: with Settings open,
   *  the form alone is taller than a laptop's window. */
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
        scale: found.scale ?? undefined,
      })
      .subscribe({
        next: (result) => {
          // A re-sort answers these empty; keep the run's own (ADR-0035).
          this.result.set({
            ...result,
            dropped: found.dropped,
            changes: found.changes,
            compared_with: found.compared_with,
            alert: found.alert,
          });
          this.reordering.set(false);
        },
        error: (failure: unknown) => {
          this.reorderFailed.set(
            `Could not re-order these: they are still ${this.ordering(found.sort_by)}, ` +
              `not ${this.ordering(sortBy)}. ${refusal(failure)}`,
          );
          control.value = found.sort_by;
          this.reordering.set(false);
        },
      });
  }

  /** A criterion in the control's own words, for a sentence: "cheapest first". */
  private ordering(name: SortBy): string {
    const label = this.defaults()?.sort_labels?.[name];
    return label ? label.charAt(0).toLowerCase() + label.slice(1) : `by ${name}`;
  }

  protected payFor(
    product: RankedProduct,
    approved: { title: string; price: number; currency: string },
  ): void {
    const found = this.result();
    const settings = this.ranWith();
    const paying = this.paySettings();
    // Paying off, or held on a marked box: a late approval is not one to send.
    if (!found || !settings || !paying.pay || paying.held !== null || this.paying() !== null) {
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
        rail: paying.rail,
        merchant_url: paying.merchant_url,
        spend_limit: paying.spend_limit,
        // The run's own, as a re-sort sends them.
        currency: settings.currency,
        scale: found.scale ?? undefined,
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
          // A refused endpoint is marked on its box, held against what was sent and the
          // rail (ADR-0033); a refused spend limit is one cart over it, said beside them.
          this.rejected.set(
            refusedField(failure) === 'merchant_url'
              ? {
                  field: 'merchant_url',
                  message: refusal(failure),
                  payment: { sent: paying.merchant_url, rail: paying.rail },
                }
              : null,
          );
          this.paying.set(null);
        },
      });
  }

  /** A payment is never cancelled (it may have moved money), so a late answer for a
   *  replaced run is dropped instead. */
  private stillThisRun(settings: SearchOptions): boolean {
    return this.ranWith() === settings;
  }

  protected downloadResults(): void {
    saveText(
      filename('results', 'json', new Date()),
      JSON.stringify(this.result()?.products ?? [], null, 2),
      'application/json',
    );
  }

  /** Close the stream, and say what that does and does not reach. */
  protected stop(): void {
    this.run?.unsubscribe();
    this.run = null;
    this.running.set(false);
    this.stopped.set(true);
    this.logs.update((lines) => [
      ...lines,
      // The one browser-written line.
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

const NOT_PAYING: PaySettings = {
  pay: false,
  rail: '',
  merchant_url: '',
  spend_limit: null,
  held: null,
};

const NAME = 'buy_agent';

/** The two movements that are not one (ADR-0060). */
const UNMOVED = ['steady', 'unplaced'];

/** As Python's `%H:%M:%S` writes it. */
function now(): string {
  return new Date().toTimeString().slice(0, 8);
}

function refusedField(failure: unknown): string | null {
  const field = (failure as { error?: { field?: unknown } } | null)?.error?.field;
  return typeof field === 'string' ? field : null;
}

/** The server's own sentence, or a guess where it sent none. */
function refusal(failure: unknown): string {
  const answered = (failure as { error?: { error?: unknown } } | null)?.error?.error;
  const said = typeof answered === 'string' ? answered.trim() : '';
  return said || 'Is the agent server still running?';
}
