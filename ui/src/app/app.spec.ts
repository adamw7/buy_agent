import { HttpErrorResponse } from '@angular/common/http';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Observable, Subject, of, throwError } from 'rxjs';
import { afterEach, vi } from 'vitest';

import { App } from './app';
import { AgentService } from './agent';
import type {
  AgentDefaults,
  ModelSource,
  ModelStatus,
  RankOptions,
  SearchEvent,
  SearchResult,
  SourcesCheck,
} from './agent.types';

const DEFAULTS: AgentDefaults = {
  provider: 'ollama',
  provider_options: [
    {
      name: 'ollama',
      label: 'Ollama',
      model: 'llama3.2',
      base_url: 'http://localhost:11434',
      takes_num_ctx: true,
    },
    {
      name: 'vllm',
      label: 'vLLM',
      model: 'Qwen/Qwen3-8B',
      base_url: 'http://localhost:8000/v1',
      takes_num_ctx: false,
    },
  ],
  model: 'llama3.2',
  base_url: 'http://localhost:11434',
  temperature: 0,
  num_ctx: null,
  think: null,
  results: 10,
  top: 2,
  region: 'us-en',
  sources: '',
  fetch: true,
  sort_by: 'score',
  sort_options: ['score', 'price', 'rating'],
  limits: {
    results: { min: 1, max: 50 },
    top: { min: 1, max: 50 },
    temperature: { min: 0, max: 2 },
    num_ctx: { min: 1, max: 1_000_000 },
  },
};

const STATUS: ModelStatus = {
  provider: 'ollama',
  label: 'Ollama',
  base_url: 'http://localhost:11434',
  reachable: true,
  models: [{ name: 'llama3.2', completion: true }],
};

const product = (rank: number, name: string) => ({
  rank,
  score: 1 - rank / 10,
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
  products: [product(1, 'Best Kettle'), product(2, 'Good Kettle'), product(3, 'Other Kettle')],
};

/** Stands in for the HTTP layer: no request leaves the page in these tests. */
class FakeAgent {
  readonly stream = new Subject<SearchEvent>();
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

describe('App', () => {
  let agent: FakeAgent;

  const render = async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    return fixture;
  };

  /** Type a request into the form and submit it, the way a shopper would. */
  const searchFor = async (fixture: ComponentFixture<App>, request: string) => {
    const page = fixture.nativeElement as HTMLElement;
    const input = page.querySelector<HTMLInputElement>('input[name="request"]')!;
    input.value = request;
    input.dispatchEvent(new Event('input'));
    await fixture.whenStable();
    page.querySelector('form')!.dispatchEvent(new Event('submit'));
    await fixture.whenStable();
  };

  beforeEach(() => {
    localStorage.clear();
    agent = new FakeAgent();
    TestBed.configureTestingModule({ providers: [{ provide: AgentService, useValue: agent }] });
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

    const server = page.querySelector<HTMLInputElement>('input[name="baseUrl"]')!;
    server.value = 'http://10.0.0.5:11434';
    server.dispatchEvent(new Event('input'));
    server.dispatchEvent(new Event('change'));
    await fixture.whenStable();

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
    expect(page.querySelector('.banner.quiet')!.textContent).toContain('Nothing came back');
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

    const field = page.querySelector<HTMLInputElement>('input[name="sources"]')!;
    field.value = 'Marques Brownlee';
    field.dispatchEvent(new Event('input'));
    field.dispatchEvent(new Event('change'));
    await fixture.whenStable();

    expect(agent.sourcesAsked).toContain('Marques Brownlee');
    expect(page.querySelector('.problem')!.textContent).toContain('does not name a source');
    expect(page.querySelector<HTMLButtonElement>('button[type="submit"]')!.disabled).toBe(true);
  });

  it('leaves the field alone when the agent server is the one that did not answer', async () => {
    /* Nothing came back to judge it with, and the banner already says why. */
    agent.sourcesResponse = () => throwError(() => new Error('down'));
    const fixture = await render();
    const page = fixture.nativeElement as HTMLElement;

    const field = page.querySelector<HTMLInputElement>('input[name="sources"]')!;
    field.value = 'rtings.com';
    field.dispatchEvent(new Event('input'));
    field.dispatchEvent(new Event('change'));
    await fixture.whenStable();

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
  const finished = async (result: SearchResult = RESULT) => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const page = fixture.nativeElement as HTMLElement;
    const input = page.querySelector<HTMLInputElement>('input[name="request"]')!;
    input.value = 'kettle';
    input.dispatchEvent(new Event('input'));
    await fixture.whenStable();
    page.querySelector('form')!.dispatchEvent(new Event('submit'));
    await fixture.whenStable();
    agent.stream.next({ kind: 'result', result });
    agent.stream.complete();
    await fixture.whenStable();
    return fixture;
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
