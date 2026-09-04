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
  SortBy,
  SourcesCheck,
} from './agent.types';
import { ProductCard } from './product-card/product-card';
import { ProgressLog } from './progress-log/progress-log';
import { filename, saveText } from './save';
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
  /** A re-sort in flight. Its own flag and not `running`: the form stays usable
   *  through it, because nothing is being searched -- this is one request over
   *  products the page already has. */
  protected readonly reordering = signal(false);
  /** A re-sort that did not happen, said beside the results it did not change.
   *  Not in `failure`: that one means the run failed, and the log panel offers a
   *  bug report on the strength of it -- while this leaves a finished run on the
   *  screen, in the order it was already in. */
  protected readonly reorderFailed = signal<string | null>(null);

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
  /** The two requests whose answer is about a question the page can have moved
   *  on from: a re-sort of products the next search is about to replace, and a
   *  listing of a server the form is no longer pointed at. Held so the newer ask
   *  cancels the older, since an answer that arrives second is not the answer to
   *  the second question -- which is the same care `sourcesProblem` takes in the
   *  form, by comparing what was asked with what the box now holds. */
  private reorder: Subscription | null = null;
  private listing: Subscription | null = null;

  constructor() {
    inject(DestroyRef).onDestroy(() => {
      this.run?.unsubscribe();
      this.reorder?.unsubscribe();
      this.listing?.unsubscribe();
    });

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
    // Two of these can be in flight at once -- the provider picker fills in the
    // address and both ask -- and the slower one answering last would leave the
    // pill and the model list describing a server the form is not pointed at.
    this.listing?.unsubscribe();
    this.listing = this.agent.models(target).subscribe({
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
    // A re-sort still in flight is about the run being replaced: left running,
    // its answer lands on a cleared page and puts the last search's products
    // back under a progress panel narrating the next one. The form stays usable
    // through a re-sort on purpose, so this is reachable by asking for one and
    // searching again before it answers.
    this.reorder?.unsubscribe();
    this.reorder = null;
    this.reordering.set(false);
    this.logs.set([]);
    this.result.set(null);
    this.failure.set(null);
    this.rejected.set(null);
    this.reorderFailed.set(null);
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

  /**
   * Ask for the same products in another order, without searching for them again.
   *
   * "Rank by" was a search option and nothing else, so changing it after a run
   * spent the minute a second time -- another search, ten more pages fetched,
   * another extraction -- to reorder products already on the screen. The
   * ordering is still Python's: the products go back and come back ranked by
   * the function every run ends with, which is the line ADR-0035 draws between
   * skipping the search and letting the browser decide the answer.
   */
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
        // Nothing was lost -- the run is still on the screen in the order it was
        // already in -- so this is said beside those results rather than in the
        // banner that means the run itself failed.
        error: (failure: unknown) => {
          this.reorderFailed.set(
            `Could not re-order these by ${sortBy}; they are still ranked by ` +
              `${found.sort_by}. ${refusal(failure)}`,
          );
          // Put the control back to the order these products are actually in.
          // Angular cannot: the reader moved the select, `found.sort_by` never
          // moved with it, so every `selected` binding still evaluates to what it
          // did and nothing is written. Left alone, the one control on the page
          // saying what these are sorted by names an order they are not in -- and
          // choosing that criterion again fires no `change`, so there was no way
          // to ask a second time either.
          control.value = found.sort_by;
          this.reordering.set(false);
        },
      });
  }

  /**
   * Hand the finished run over as a file.
   *
   * The page is thrown away by the next question and the run took a minute, so
   * a shopper comparing two searches had nothing to compare with. What is
   * written is what the server sent, which is what `--json` writes: the shape is
   * `results_payload`'s, so the browser saves the answer rather than composing
   * one of its own.
   */
  protected downloadResults(): void {
    saveText(
      filename('results', 'json', new Date()),
      JSON.stringify(this.result()?.products ?? [], null, 2),
      'application/json',
    );
  }

  /**
   * Stop the run: close the stream, and say what that does and does not reach.
   *
   * Closing the stream is what stops the run -- the server notices the reader has
   * gone and ends the pipeline at its next step (ADR-0034) -- but "at its next
   * step" is the part a shopper has to be told. A call already in flight to the
   * model server finishes first, so someone who hits Stop and starts another
   * search straight away has two runs on one model server and both are slower
   * than either would have been alone. The line says so, because nothing else on
   * the page can.
   */
  protected stop(): void {
    this.run?.unsubscribe();
    this.run = null;
    this.running.set(false);
    this.logs.update((lines) => [
      ...lines,
      // The only line the browser writes itself, so it is the only one timed off
      // the browser's clock -- in the format Python sends the rest in, since the
      // panel shows them in one column.
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

/**
 * Why a request failed: the server's own sentence, or a guess where it sent none.
 *
 * The browser decides nothing, and that includes the diagnosis. `POST /api/rank`
 * refuses things it can name -- fifty products with six quotes each is a body
 * past the server's cap, and the answer says so -- and writing "Is the agent
 * server still running?" over the top of that told a shopper to go looking for a
 * server that had answered, in a sentence explaining exactly what was wrong. The
 * guess is kept for the one case with nothing to read: a request that reached
 * nothing at all.
 */
function refusal(failure: unknown): string {
  const answered = (failure as { error?: { error?: unknown } } | null)?.error?.error;
  const said = typeof answered === 'string' ? answered.trim() : '';
  return said || 'Is the agent server still running?';
}
