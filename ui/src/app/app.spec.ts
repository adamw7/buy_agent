import { HttpErrorResponse } from '@angular/common/http';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Observable, Subject, of, throwError } from 'rxjs';
import { afterEach, vi } from 'vitest';

import { App } from './app';
import { AgentService } from './agent';
import { WEIGHTS, defaults, receipt, status } from './testing';
import type {
  ModelSource,
  ModelStatus,
  PayOptions,
  RankOptions,
  Receipt,
  SearchEvent,
  SearchResult,
  SourcesCheck,
} from './agent.types';

/** Two products highlighted rather than the server's three: these tests are
 *  about what the page does with the split, so the rest is `defaults()`. */
const DEFAULTS = defaults({ top: 2 });
const STATUS = status();

const product = (rank: number, name: string) => ({
  rank,
  score: 1 - rank / 10,
  breakdown: {
    rating: 0.9,
    popularity: 0.5,
    price: 1 - rank / 10,
    total: 1 - rank / 10,
    neutral: ['popularity'],
  },
  cannot_pay: null,
  pay_currency: 'USD',
  pay_label: `${100 * rank}.00 USD`,
  pay_merchant: 'shop.example',
  name,
  price: 100 * rank,
  currency: 'USD',
  rating: 4.5,
  review_count: 10,
  seller: null,
  url: null,
  notes: null,
  opinions: [],
  price_label: `${100 * rank}.00 USD`,
  rating_label: '4.5/5 (10 reviews)',
});

const RESULT: SearchResult = {
  request: 'kettle',
  count: 3,
  top_n: 2,
  sort_by: 'score',
  weights: WEIGHTS,
  products: [product(1, 'Best Kettle'), product(2, 'Good Kettle'), product(3, 'Other Kettle')],
};

const RECEIPT = receipt({
  merchant: 'Shop',
  title: 'kettle 1',
  price: 100,
  amount: 10000,
  price_label: '100.00 USD',
});

/** Stands in for the HTTP layer: no request leaves the page in these tests. */
class FakeAgent {
  /** Replaced rather than reused for a second run: a completed subject stays
   *  completed, and a run that has finished has completed this one. */
  stream = new Subject<SearchEvent>();
  defaultsResponse = of(DEFAULTS);
  modelsResponse: Observable<ModelStatus> = of(STATUS);
  searched: unknown[] = [];
  modelsAsked: ModelSource[] = [];
  sourcesAsked: string[] = [];
  sourcesResponse = (sources: string): Observable<SourcesCheck> => of({ sources, error: '' });
  unsubscribed = false;
  ranked: RankOptions[] = [];
  /** What `/api/rank` answers with. A function, so a test can shape the reply
   *  around what was posted -- which is the whole set, ordered again. */
  rankResponse: (options: RankOptions) => Observable<SearchResult> = (options) =>
    of({
      request: options.request,
      count: options.products.length,
      top_n: options.top,
      sort_by: options.sort_by,
      weights: WEIGHTS,
      products: [...options.products].reverse().map((entry, index) => ({
        ...entry,
        rank: index + 1,
      })),
    });

  defaults() {
    return this.defaultsResponse;
  }

  models(source: ModelSource) {
    this.modelsAsked.push(source);
    return this.modelsResponse;
  }

  checkSources(sources: string) {
    this.sourcesAsked.push(sources);
    return this.sourcesResponse(sources);
  }

  rank(options: RankOptions) {
    this.ranked.push(options);
    return this.rankResponse(options);
  }

  paid: PayOptions[] = [];
  /** What `/api/pay` answers with. A function so a test can refuse one. */
  payResponse: (options: PayOptions) => Observable<{ receipt: Receipt }> = () =>
    of({ receipt: RECEIPT });

  pay(options: PayOptions) {
    this.paid.push(options);
    return this.payResponse(options);
  }

  search(options: unknown): Observable<SearchEvent> {
    this.searched.push(options);
    // Teardown is recorded rather than just performed, because closing the
    // stream is exactly what the Stop button is supposed to do.
    return new Observable<SearchEvent>((subscriber) => {
      const inner = this.stream.subscribe(subscriber);
      return () => {
        this.unsubscribed = true;
        inner.unsubscribe();
      };
    });
  }
}

/** A rendered page, settled -- which is the App every one of these starts from. */
const render = async (): Promise<ComponentFixture<App>> => {
  const fixture = TestBed.createComponent(App);
  await fixture.whenStable();
  return fixture;
};

/** Type into a named field. `left` also fires `change`, which is what the fields
 *  that ask the server something wait for rather than a keystroke. */
const fill = async (fixture: ComponentFixture<App>, name: string, value: string, left = false) => {
  const field = (fixture.nativeElement as HTMLElement).querySelector<HTMLInputElement>(
    `input[name="${name}"]`,
  )!;
  field.value = value;
  field.dispatchEvent(new Event('input'));
  if (left) {
    field.dispatchEvent(new Event('change'));
  }
  await fixture.whenStable();
};

/** Type a request into the form and submit it, the way a shopper would. */
const searchFor = async (fixture: ComponentFixture<App>, request: string) => {
  await fill(fixture, 'request', request);
  (fixture.nativeElement as HTMLElement).querySelector('form')!.dispatchEvent(new Event('submit'));
  await fixture.whenStable();
};

/** One whole run on the page: ask for something, and let the stream answer.
 *  All three blocks below need it, so it takes the fake rather than closing over
 *  one -- each `describe` builds its own in `beforeEach`. */
const ran = async (
  agent: FakeAgent,
  request: string,
  result: SearchResult,
  fixture?: ComponentFixture<App>,
): Promise<ComponentFixture<App>> => {
  const page = fixture ?? (await render());
  await searchFor(page, request);
  agent.stream.next({ kind: 'result', result });
  agent.stream.complete();
  await page.whenStable();
  return page;
};

describe('App', () => {
  let agent: FakeAgent;

  beforeEach(() => {
    localStorage.clear();
    agent = new FakeAgent();
    TestBed.configureTestingModule({ providers: [{ provide: AgentService, useValue: agent }] });
  });

  it('says which server it is waiting on rather than showing the last answer', async () => {
    /* `/api/models` is a call per pulled tag on a five-second budget, so a dead
       server takes the whole of it. Left showing the previous answer, the pill
       reported a server nobody had asked about and Check again did nothing
       visible at all. */
    const listing = new Subject<ModelStatus>();
    agent.modelsResponse = listing;

    const fixture = await render();
    const page = fixture.nativeElement as HTMLElement;
    expect(page.querySelector('.server')!.textContent).toContain('Asking Ollama…');
    expect(page.querySelector('.server-reason'), 'no remedy for an unasked question').toBeNull();

    listing.next(STATUS);
    await fixture.whenStable();

    expect(page.querySelector('.server')!.textContent).toContain('Ollama · 1 model');
  });

  it('names the server being asked about, not the one still on screen', async () => {
    /* The provider picker fills in its pair and asks -- and the answer on screen
       is the *last* server's, which is not what the wait is about. */
    const fixture = await render();
    const page = fixture.nativeElement as HTMLElement;
    agent.modelsResponse = new Subject<ModelStatus>();

    const picker = page.querySelector<HTMLSelectElement>('select[name="provider"]')!;
    picker.value = 'vllm';
    picker.dispatchEvent(new Event('change'));
    await fixture.whenStable();

    expect(page.querySelector('.server')!.textContent).toContain('Asking vLLM…');
  });

  it('holds the model picker shut while the list is being replaced', async () => {
    /* What it holds is the last server's models, and one of those picked a moment
       before the new list lands is a model this server never offered. */
    agent.modelsResponse = new Subject<ModelStatus>();

    const page = (await render()).nativeElement as HTMLElement;
    const field =
      page.querySelector('select[name="model"]') ?? page.querySelector('input[name="model"]');

    expect((field as HTMLInputElement).disabled).toBe(true);
    expect(page.textContent).toContain('Asking Ollama what it is serving…');
  });

  it('seeds the form from the agent defaults and shows the model server status', async () => {
    const page = (await render()).nativeElement as HTMLElement;
    expect(page.querySelector<HTMLSelectElement>('select[name="model"]')!.value).toBe('llama3.2');
    expect(page.querySelector('.server')!.textContent).toContain('Ollama · 1 model');
  });

  it('fills the model dropdown with what that server is serving', async () => {
    agent.modelsResponse = of({
      ...STATUS,
      models: ['llama3.2', 'lfm2.5', 'qwen2.5'].map((name) => ({ name, completion: true })),
    });

    const page = (await render()).nativeElement as HTMLElement;
    const options = page.querySelectorAll<HTMLOptionElement>('select[name="model"] option');

    expect([...options].map((option) => option.value)).toEqual(['llama3.2', 'lfm2.5', 'qwen2.5']);
  });

  it('re-asks when the form is pointed at another server', async () => {
    /* The dropdown lists one server's models; a different server has its own. */
    const fixture = await render();
    const page = fixture.nativeElement as HTMLElement;

    await fill(fixture, 'baseUrl', 'http://10.0.0.5:11434', true);

    expect(agent.modelsAsked).toEqual([
      { provider: 'ollama', base_url: DEFAULTS.base_url },
      { provider: 'ollama', base_url: 'http://10.0.0.5:11434' },
    ]);
  });

  it('asks the other provider when the form switches to it', async () => {
    /* Both halves change together: a vLLM asked Ollama's question answers 404,
       and Ollama's port is not where the vLLM is. */
    const fixture = await render();
    const page = fixture.nativeElement as HTMLElement;

    const picker = page.querySelector<HTMLSelectElement>('select[name="provider"]')!;
    picker.value = 'vllm';
    picker.dispatchEvent(new Event('change'));
    await fixture.whenStable();

    expect(agent.modelsAsked.at(-1)).toEqual({
      provider: 'vllm',
      base_url: 'http://localhost:8000/v1',
    });
  });

  it('names the server it could not reach, rather than always naming Ollama', async () => {
    /* The pill is over a form that may be pointed at either, so a vLLM that is
       down must not be reported as an Ollama that is. */
    agent.modelsResponse = of({ ...STATUS, provider: 'vllm', label: 'vLLM', reachable: false });

    const page = (await render()).nativeElement as HTMLElement;

    expect(page.querySelector('.server')!.textContent).toContain('vLLM unreachable');
  });

  it('says the server is unreachable rather than pretending it has no models', async () => {
    /* An empty model list and a server that never answered are different things.
       The pill still names it, out of the defaults the agent server sent. */
    agent.modelsResponse = throwError(() => new Error('connection refused'));

    const page = (await render()).nativeElement as HTMLElement;

    expect(page.querySelector('.server')!.textContent).toContain('Ollama unreachable');
    expect(page.querySelector('.server')!.classList).not.toContain('up');
  });

  it('shows what to start, as visible text rather than a hover', async () => {
    /* The pill alone says only that nothing answered. The remedy is Python's
       sentence, and a title attribute is no use on a touch screen. */
    agent.modelsResponse = of({
      ...STATUS,
      reachable: false,
      models: [],
      detail: 'connection refused',
      hint:
        'Could not reach Ollama at http://localhost:11434 (connection refused). ' +
        'Start it with:  ollama serve',
    });

    const page = (await render()).nativeElement as HTMLElement;

    expect(page.querySelector('.server-reason')!.textContent).toContain('ollama serve');
  });

  it('keeps quiet about a server that answered', async () => {
    const page = (await render()).nativeElement as HTMLElement;

    expect(page.querySelector('.server-reason')).toBeNull();
  });

  it('says nothing it was not told when the agent server is the one that is down', async () => {
    /* Nothing came back, so there is no hint to show -- and inventing one here
       would be the browser deciding what Python could not. */
    agent.modelsResponse = throwError(() => new Error('offline'));

    const page = (await render()).nativeElement as HTMLElement;

    expect(page.querySelector('.server')!.textContent).toContain('Ollama unreachable');
    expect(page.querySelector('.server-reason')).toBeNull();
  });

  it('offers a way back once the server that was down has been started', async () => {
    /* The remedy is one command, and the moment after running it there is
       nothing on the page that says "ask again" -- the pill re-asks when it is
       clicked, but nothing about a status pill says so. */
    agent.modelsResponse = of({ ...STATUS, reachable: false, models: [], hint: 'ollama serve' });
    const fixture = await render();
    const page = fixture.nativeElement as HTMLElement;

    agent.modelsResponse = of(STATUS);
    page.querySelector<HTMLButtonElement>('.server-reason .recheck')!.click();
    await fixture.whenStable();

    expect(page.querySelector('.server-reason')).toBeNull();
    expect(page.querySelector('.server')!.textContent).toContain('1 model');
  });

  it('re-asks the same server when the status pill is clicked', async () => {
    const fixture = await render();
    const page = fixture.nativeElement as HTMLElement;

    page.querySelector<HTMLButtonElement>('.server')!.click();
    await fixture.whenStable();

    expect(agent.modelsAsked).toEqual([
      { provider: 'ollama', base_url: DEFAULTS.base_url },
      { provider: STATUS.provider, base_url: STATUS.base_url },
    ]);
  });

  it('says so when the agent server itself cannot be reached', async () => {
    agent.defaultsResponse = throwError(() => new Error('offline'));
    const page = (await render()).nativeElement as HTMLElement;
    expect(page.querySelector('.banner')!.textContent).toContain('Could not reach the agent');
  });

  it('starts a run, shows its progress, then the ranked products', async () => {
    const fixture = await render();
    const page = fixture.nativeElement as HTMLElement;

    await searchFor(fixture, 'kettle');

    expect(agent.searched).toHaveLength(1);
    expect(page.querySelector('app-progress-log')).not.toBeNull();

    agent.stream.next({
      kind: 'log',
      line: {
        time: '18:12:19',
        level: 'INFO',
        logger: 'buy_agent.search',
        message: 'Search returned 10 results',
      },
    });
    await fixture.whenStable();
    expect(page.textContent).toContain('Search returned 10 results');

    agent.stream.next({ kind: 'result', result: RESULT });
    agent.stream.complete();
    await fixture.whenStable();

    expect(page.querySelector('.results h2')!.textContent).toContain('Top 2 of 3');
    expect(page.querySelectorAll('app-product-card')).toHaveLength(3);
    expect(page.querySelector('.also summary')!.textContent).toContain('1 more');
  });

  it('shows a failed run as a message, not as an empty page', async () => {
    const fixture = await render();
    const page = fixture.nativeElement as HTMLElement;
    await searchFor(fixture, 'kettle');
    agent.stream.next({
      kind: 'failure',
      message: 'Start it with: ollama serve',
      status: 503,
      field: null,
    });
    agent.stream.complete();
    await fixture.whenStable();

    expect(page.querySelector('.banner')!.textContent).toContain('ollama serve');
    expect(page.querySelector('.results')).toBeNull();
    // The log is what a bug report needs, and the panel is thrown away by the
    // next search, so a failed run offers to save it.
    expect(page.querySelector('app-progress-log .save')).not.toBeNull();
  });

  it('explains an empty result instead of showing an empty list', async () => {
    const fixture = await render();
    await searchFor(fixture, 'kettle');
    agent.stream.next({
      kind: 'result',
      result: { ...RESULT, count: 0, products: [] },
    });
    agent.stream.complete();
    await fixture.whenStable();

    const page = fixture.nativeElement as HTMLElement;
    const said = page.querySelector('.banner.quiet')!.textContent!;
    expect(said).toContain('Nothing came back');
    /* All three of the things that end a run with nothing, because naming only
       the two the pipeline does is what sent a reader looking at the web for a
       report their own Min reviews had emptied. The limits are the one of the
       three that is a box on this page. */
    expect(said).toContain('no pages worth reading');
    expect(said).toContain('survived grounding');
    expect(said).toContain('limits you set');
    /* And no guess at which: the run logs a line for whichever it was, and the
       panel holding it is on the page above this. */
    expect(said).toContain('Progress');
  });

  it('says the connection went, and stops looking busy', async () => {
    /* AgentService turns a dropped EventSource into an error rather than letting
       it reconnect and silently start the whole search again. */
    const fixture = await render();
    const page = fixture.nativeElement as HTMLElement;
    await searchFor(fixture, 'kettle');

    agent.stream.error(new Error('Lost the connection to the agent. Is it still running?'));
    await fixture.whenStable();

    expect(page.querySelector('.banner')!.textContent).toContain('Lost the connection');
    expect(page.querySelector('button[type="submit"]')).not.toBeNull();
  });

  it('marks the field a refused run names, rather than only the banner', async () => {
    /* A value the page has no rule of its own for -- a region of the wrong shape
       -- still comes back named, and the box it came out of is where the sentence
       means something. */
    const fixture = await render();
    const page = fixture.nativeElement as HTMLElement;
    await searchFor(fixture, 'kettle');

    agent.stream.next({
      kind: 'failure',
      message: "'en-US' is not a search region.",
      status: 400,
      field: 'region',
    });
    agent.stream.complete();
    await fixture.whenStable();

    expect(page.querySelector('input[name="region"]')!.classList).toContain('invalid');
    expect(page.querySelector('.field .problem')!.textContent).toContain('not a search region');
    expect(page.querySelector('.banner')!.textContent).toContain('not a search region');
  });

  it('drops the mark when the next run starts', async () => {
    /* The field was refused for what was sent, not for what is in it now. */
    const fixture = await render();
    const page = fixture.nativeElement as HTMLElement;
    await searchFor(fixture, 'kettle');
    agent.stream.next({
      kind: 'failure',
      message: "'en-US' is not a search region.",
      status: 400,
      field: 'region',
    });
    agent.stream.complete();
    await fixture.whenStable();

    await searchFor(fixture, 'kettle again');

    expect(page.querySelector('.problem')).toBeNull();
  });

  it('asks the server what a trusted source is, and shows what it said', async () => {
    /* The parse is Python's, so the page asks rather than keeping a copy of it. */
    agent.sourcesResponse = (sources) =>
      of({ sources, error: "'Marques' does not name a source." });
    const fixture = await render();
    const page = fixture.nativeElement as HTMLElement;

    await fill(fixture, 'sources', 'Marques Brownlee', true);

    expect(agent.sourcesAsked).toContain('Marques Brownlee');
    expect(page.querySelector('.problem')!.textContent).toContain('does not name a source');
    expect(page.querySelector<HTMLButtonElement>('button[type="submit"]')!.disabled).toBe(true);
  });

  it('leaves the field alone when the agent server is the one that did not answer', async () => {
    /* Nothing came back to judge it with, and the banner already says why. */
    agent.sourcesResponse = () => throwError(() => new Error('down'));
    const fixture = await render();
    const page = fixture.nativeElement as HTMLElement;

    await fill(fixture, 'sources', 'rtings.com', true);

    expect(page.querySelector('.problem')).toBeNull();
  });

  it('stops a run by closing the stream', async () => {
    const fixture = await render();
    await searchFor(fixture, 'kettle');

    const page = fixture.nativeElement as HTMLElement;
    page.querySelector<HTMLButtonElement>('.actions button')!.click();
    await fixture.whenStable();

    expect(agent.unsubscribed).toBe(true);
    expect(page.textContent).toContain('Stopped watching.');
    expect(page.querySelector('button[type="submit"]')).not.toBeNull();
  });

  it('offers the log of a run somebody stopped, as it does one that failed', async () => {
    /* A stopped run leaves no answer on the page and no banner either, and the
       reason to stop one is usually that it had gone quiet -- which is exactly
       the run worth attaching to a bug report. */
    const fixture = await render();
    await searchFor(fixture, 'kettle');
    const page = fixture.nativeElement as HTMLElement;
    expect(page.querySelector('.log .save'), 'nothing to keep while it runs').toBeNull();

    page.querySelector<HTMLButtonElement>('.actions button')!.click();
    await fixture.whenStable();

    expect(page.querySelector('.log .save')!.textContent).toContain('Download log');
  });

  it('stops offering the log once a new run is under way', async () => {
    /* The offer is about the run on screen, and the next search replaces it. */
    const fixture = await render();
    await searchFor(fixture, 'kettle');
    const page = fixture.nativeElement as HTMLElement;
    page.querySelector<HTMLButtonElement>('.actions button')!.click();
    await fixture.whenStable();

    agent.stream = new Subject<SearchEvent>();
    await searchFor(fixture, 'toaster');

    expect(page.querySelector('.log .save')).toBeNull();
  });

  it('says the run ends at the next step rather than at the click', async () => {
    /* Closing the stream is what stops it, but the server only notices at the
       pipeline's next boundary -- so a shopper who starts another search straight
       away has two on one model server. The line is the only place that is said. */
    const fixture = await render();
    await searchFor(fixture, 'kettle');

    const page = fixture.nativeElement as HTMLElement;
    page.querySelector<HTMLButtonElement>('.actions button')!.click();
    await fixture.whenStable();

    const line = page.textContent!;
    expect(line).toContain('next step');
    expect(line).toContain('before starting another search');
  });

  it('takes the newest server listing, not the one that answers last', async () => {
    /* Changing the provider fills in its address and asks, and editing the
       address asks again -- so two listings can be in flight at once. The slower
       one answering second left the pill and the model list describing a server
       the form is not pointed at. */
    const first = new Subject<ModelStatus>();
    const second = new Subject<ModelStatus>();
    agent.modelsResponse = first;
    const fixture = await render();
    const page = fixture.nativeElement as HTMLElement;

    agent.modelsResponse = second;
    await fill(fixture, 'baseUrl', 'http://10.0.0.5:11434', true);

    second.next({ ...STATUS, base_url: 'http://10.0.0.5:11434', models: [] });
    first.next({ ...STATUS, models: [{ name: 'llama3.2', completion: true }] });
    await fixture.whenStable();

    expect(page.querySelector('.server')!.textContent).toContain('Ollama · 0 models');
  });

  it('closes the stream when the page goes away', async () => {
    const fixture = await render();
    await searchFor(fixture, 'kettle');
    fixture.destroy();
    expect(agent.unsubscribed).toBe(true);
  });
});

describe('App results', () => {
  let agent: FakeAgent;

  beforeEach(() => {
    localStorage.clear();
    agent = new FakeAgent();
    TestBed.configureTestingModule({ providers: [{ provide: AgentService, useValue: agent }] });
  });

  afterEach(() => vi.restoreAllMocks());

  /** A finished run on the page, which is what both of these are about. */
  const finished = (result: SearchResult = RESULT) => ran(agent, 'kettle', result);

  /** Ask for something else, with a stream of its own for the new run. */
  const searchAgain = async (fixture: ComponentFixture<App>, request: string) => {
    agent.stream = new Subject<SearchEvent>();
    await searchFor(fixture, request);
  };

  /** Pick a criterion out of the Rank by control beside the results. */
  const rankBy = async (fixture: ComponentFixture<App>, criterion: string) => {
    const select = (fixture.nativeElement as HTMLElement).querySelector<HTMLSelectElement>(
      'select[name="resort"]',
    )!;
    select.value = criterion;
    select.dispatchEvent(new Event('change'));
    await fixture.whenStable();
  };

  it('calls the two ordering controls two different things', async () => {
    /* One re-orders products already on the screen and one sets the criterion the
       next run is ranked by. Both said "Rank by", so they read as one setting
       shown twice -- and perpetually out of step with itself, since changing
       either leaves the other where it was. */
    const page = (await finished()).nativeElement as HTMLElement;

    const beside = page.querySelector('.resort span')!.textContent!.trim();
    const setting = page
      .querySelector('select[name="sortBy"]')!
      .closest('label')!
      .textContent!.trim();

    expect(beside).toBe('Re-order these');
    expect(setting).toContain('Rank by');
    expect(setting).toContain("How the next run's results come back ordered.");
  });

  it('gives every card the weights the run blended its scores by', async () => {
    /* Three shares under a total they do not add up to cannot be read at all --
       and the ones folded away are read the same way as the ones on top. */
    const page = (await finished()).nativeElement as HTMLElement;
    const weights = [...page.querySelectorAll('app-product-card .parts li .weight')];

    expect(weights.length).toBe(RESULT.products.length * 3);
    expect(weights[0].textContent).toContain('50% of the score');
  });

  it('offers the criteria the server named, showing the one the run used', async () => {
    const page = (await finished()).nativeElement as HTMLElement;
    const select = page.querySelector<HTMLSelectElement>('select[name="resort"]')!;

    expect([...select.options].map((option) => option.value)).toEqual(['score', 'price', 'rating']);
    expect(select.value).toBe('score');
  });

  it('re-orders a finished run without searching for it again', async () => {
    /* The whole point: the products are already on the page, and reordering them
       used to cost another search, ten more page fetches and another extraction
       (ADR-0035). One request, and no second run. */
    const fixture = await finished();
    const searches = agent.searched.length;

    await rankBy(fixture, 'price');

    expect(agent.ranked).toHaveLength(1);
    expect(agent.ranked[0].sort_by).toBe('price');
    expect(agent.ranked[0].products).toHaveLength(3);
    expect(agent.ranked[0].top).toBe(2);
    expect(agent.searched).toHaveLength(searches);

    const page = fixture.nativeElement as HTMLElement;
    expect(page.querySelector('app-product-card')!.textContent).toContain('Other Kettle');
  });

  it('does not ask again for the order the run already came back in', async () => {
    const fixture = await finished();

    await rankBy(fixture, 'score');

    expect(agent.ranked).toEqual([]);
  });

  it('says a re-order failed beside the results it left alone', async () => {
    /* Not the banner that means the run failed: the run did not, its products are
       still on the screen, and the log panel must not start offering a bug
       report about a search that worked. */
    agent.rankResponse = () => throwError(() => new Error('offline'));
    const fixture = await finished();

    await rankBy(fixture, 'rating');

    const page = fixture.nativeElement as HTMLElement;
    expect(page.querySelector('.results .banner')!.textContent).toContain('still ranked by score');
    expect(page.querySelector('app-progress-log .save')).toBeNull();
    expect(page.querySelector('app-product-card')!.textContent).toContain('Best Kettle');
  });

  it('says what the server said about a re-order it refused', async () => {
    /* The server names what it refused and why -- a body past its cap, a
       criterion it does not sort by -- and a sentence written here on top of
       that sent the reader after a server that had answered. */
    agent.rankResponse = () =>
      throwError(
        () =>
          new HttpErrorResponse({
            status: 413,
            error: { error: 'Request body is too large.', field: null },
          }),
      );
    const fixture = await finished();

    await rankBy(fixture, 'rating');

    const banner = (fixture.nativeElement as HTMLElement).querySelector('.results .banner')!;
    expect(banner.textContent).toContain('Request body is too large.');
    expect(banner.textContent).not.toContain('still running');
  });

  it('puts the Rank by control back when the re-order did not happen', async () => {
    /* The control is the one thing on the page saying what these are sorted by.
       Left on the criterion that failed it says the wrong thing -- and picking it
       again fires no `change`, so there was no way to retry either. */
    agent.rankResponse = () => throwError(() => new Error('offline'));
    const fixture = await finished();

    await rankBy(fixture, 'rating');

    const select = (fixture.nativeElement as HTMLElement).querySelector<HTMLSelectElement>(
      'select[name="resort"]',
    )!;
    expect(select.value).toBe('score');
  });

  it('leaves the Rank by control on the order the re-sort delivered', async () => {
    const fixture = await finished();

    await rankBy(fixture, 'price');

    const select = (fixture.nativeElement as HTMLElement).querySelector<HTMLSelectElement>(
      'select[name="resort"]',
    )!;
    expect(select.value).toBe('price');
  });

  it('drops a re-order that answers after the next search has started', async () => {
    /* The form stays usable through a re-sort, so a shopper can ask for one and
       search again before it answers. Left running, its answer landed on the
       cleared page and put the finished run's products back under a progress
       panel narrating the next one. */
    const reorder = new Subject<SearchResult>();
    agent.rankResponse = () => reorder;
    const fixture = await finished();
    await rankBy(fixture, 'price');

    await searchAgain(fixture, 'toaster');
    reorder.next({ ...RESULT, sort_by: 'price' });
    await fixture.whenStable();

    const page = fixture.nativeElement as HTMLElement;
    expect(page.querySelector('app-product-card')).toBeNull();
    expect(page.querySelector('.results')).toBeNull();
  });

  it('re-enables Download results when a new run cancels a re-order', async () => {
    /* The flag that greys the button out is cleared by the answer, and the
       cancelled request has none to give. */
    agent.rankResponse = () => new Subject<SearchResult>();
    const fixture = await finished();
    await rankBy(fixture, 'price');
    await searchAgain(fixture, 'toaster');

    agent.stream.next({ kind: 'result', result: RESULT });
    agent.stream.complete();
    await fixture.whenStable();

    const page = fixture.nativeElement as HTMLElement;
    expect(page.querySelector<HTMLButtonElement>('.results-actions .save')!.disabled).toBe(false);
  });

  it('hands the finished run over as the file the API answered with', async () => {
    /* What `--json` writes, because it is what the server sent: the browser saves
       the answer rather than composing a shape of its own. */
    const blobs: Blob[] = [];
    const links: HTMLAnchorElement[] = [];
    vi.spyOn(URL, 'createObjectURL').mockImplementation((blob) => {
      blobs.push(blob as Blob);
      return 'blob:results';
    });
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      links.push(this);
    });
    const page = (await finished()).nativeElement as HTMLElement;

    page.querySelector<HTMLButtonElement>('.results-actions .save')!.click();

    expect(JSON.parse(await blobs[0].text())).toEqual(RESULT.products);
    expect(links[0].download).toMatch(/^buy-agent-results-\d{8}-\d{6}\.json$/);
  });
});

describe('App paying', () => {
  let agent: FakeAgent;

  beforeEach(() => {
    localStorage.clear();
    agent = new FakeAgent();
    TestBed.configureTestingModule({ providers: [{ provide: AgentService, useValue: agent }] });
  });

  afterEach(() => vi.restoreAllMocks());

  /** A finished run, started with paying either on or off. */
  const finished = async (pay: boolean) => {
    const fixture = await render();
    if (pay) {
      const box = (fixture.nativeElement as HTMLElement).querySelector<HTMLInputElement>(
        'input[name="pay"]',
      )!;
      box.checked = true;
      box.dispatchEvent(new Event('change'));
      await fixture.whenStable();
    }
    return ran(agent, 'kettle', RESULT, fixture);
  };

  /** Click Pay on the first card, then confirm it. */
  const buyTheTopOne = async (fixture: ComponentFixture<App>) => {
    const page = fixture.nativeElement as HTMLElement;
    page.querySelector<HTMLButtonElement>('app-product-card .pay')!.click();
    await fixture.whenStable();
    page.querySelector<HTMLButtonElement>('app-product-card .confirm .pay')!.click();
    await fixture.whenStable();
  };

  it('offers no payment on a run that did not ask to pay', async () => {
    const page = (await finished(false)).nativeElement as HTMLElement;
    expect(page.querySelector('app-product-card .pay')).toBeNull();
  });

  it('sends the run, the rank and the approval, and nothing else', async () => {
    const fixture = await finished(true);
    await buyTheTopOne(fixture);

    expect(agent.paid).toHaveLength(1);
    expect(agent.paid[0].rank).toBe(1);
    expect(agent.paid[0].products).toHaveLength(RESULT.products.length);
    expect(agent.paid[0].approved).toEqual({
      title: RESULT.products[0].name,
      price: RESULT.products[0].price,
      currency: 'USD',
    });
    expect(agent.paid[0].rail).toBe('dry-run');
  });

  it('shows the receipt on the card that was bought', async () => {
    const fixture = await finished(true);
    await buyTheTopOne(fixture);

    const page = fixture.nativeElement as HTMLElement;
    expect(page.textContent).toContain('ref-abc');
    expect(page.textContent).toContain('Nothing was charged.');
  });

  it('says a payment failed beside the products and not in the run banner', async () => {
    /* The banner at the top means the *run* failed, and this run did not -- it
       found these. */
    agent.payResponse = () => throwError(() => ({ error: { error: 'Card declined' } }));
    const fixture = await finished(true);
    await buyTheTopOne(fixture);

    const page = fixture.nativeElement as HTMLElement;
    expect(page.textContent).toContain('Nothing was bought');
    expect(page.textContent).toContain('Card declined');
    expect(page.querySelectorAll('app-product-card').length).toBeGreaterThan(0);
  });

  it('pays for one thing at a time', async () => {
    /* A purchase abandoned halfway is not a question the page has moved on
       from; it is money in the air. */
    agent.payResponse = () => new Subject<{ receipt: Receipt }>();
    const fixture = await finished(true);
    await buyTheTopOne(fixture);

    const page = fixture.nativeElement as HTMLElement;
    const buttons = [...page.querySelectorAll<HTMLButtonElement>('app-product-card .pay')];
    expect(buttons.every((button) => button.disabled)).toBe(true);
  });

  /** Ask for the same products in another order, the way the control beside the
   *  results does. The fake answers with them reversed and ranked again from 1,
   *  which is what `rank_products` does to any set it is handed. */
  const reorder = async (fixture: ComponentFixture<App>, criterion: string) => {
    const select = (fixture.nativeElement as HTMLElement).querySelector<HTMLSelectElement>(
      'select[name="resort"]',
    )!;
    select.value = criterion;
    select.dispatchEvent(new Event('change'));
    await fixture.whenStable();
  };

  it('leaves the receipt on the product that was bought when the run is re-ordered', async () => {
    /* A re-sort ranks the same products again from 1 (ADR-0035), so a receipt
       filed under a rank moved to whatever came up that rank next: the page
       showed a purchase against a product nobody had bought, and offered the one
       that had been bought a Pay button for a second go. */
    const fixture = await finished(true);
    await buyTheTopOne(fixture);

    await reorder(fixture, 'price');

    const cards = [...(fixture.nativeElement as HTMLElement).querySelectorAll('app-product-card')];
    const bought = cards.filter((card) => card.textContent!.includes('ref-abc'));
    expect(bought).toHaveLength(1);
    expect(bought[0].textContent).toContain('Best Kettle');
    expect(bought[0].querySelector('.pay'), 'nothing left to buy on this one').toBeNull();
  });

  it('does not carry a confirmation over to whatever a re-order puts in its place', async () => {
    /* The second click is the one that spends the money, so it has to be about
       the product the first click was about. Tracked by rank, the card kept its
       component and swapped the product underneath it. */
    const fixture = await finished(true);
    const page = fixture.nativeElement as HTMLElement;
    page.querySelector<HTMLButtonElement>('app-product-card .pay')!.click();
    await fixture.whenStable();
    expect(page.querySelector('app-product-card .confirm')).not.toBeNull();

    await reorder(fixture, 'price');

    expect(page.querySelector('app-product-card .confirm')).toBeNull();
  });

  it('forgets the receipts when a new run replaces the products', async () => {
    const fixture = await finished(true);
    await buyTheTopOne(fixture);
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('ref-abc');

    agent.stream = new Subject<SearchEvent>();
    await ran(agent, 'toaster', RESULT, fixture);

    expect((fixture.nativeElement as HTMLElement).textContent).not.toContain('ref-abc');
  });
});
