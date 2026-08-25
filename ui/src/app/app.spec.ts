import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Observable, Subject, of, throwError } from 'rxjs';

import { App } from './app';
import { AgentService } from './agent';
import type { AgentDefaults, ModelStatus, SearchEvent, SearchResult } from './agent.types';

const DEFAULTS: AgentDefaults = {
  model: 'llama3.2',
  base_url: 'http://localhost:11434',
  temperature: 0,
  num_ctx: null,
  think: null,
  results: 10,
  top: 2,
  region: 'us-en',
  fetch: true,
  sort_by: 'score',
  sort_options: ['score', 'price', 'rating'],
};

const STATUS: ModelStatus = {
  base_url: 'http://localhost:11434',
  reachable: true,
  models: ['llama3.2'],
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
  modelsAsked: string[] = [];
  unsubscribed = false;

  defaults() {
    return this.defaultsResponse;
  }

  models(baseUrl: string) {
    this.modelsAsked.push(baseUrl);
    return this.modelsResponse;
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

  it('seeds the form from the agent defaults and shows the Ollama status', async () => {
    const page = (await render()).nativeElement as HTMLElement;
    expect(page.querySelector<HTMLSelectElement>('select[name="model"]')!.value).toBe('llama3.2');
    expect(page.querySelector('.ollama')!.textContent).toContain('1 model');
  });

  it('fills the model dropdown with what that Ollama has pulled', async () => {
    agent.modelsResponse = of({ ...STATUS, models: ['llama3.2', 'lfm2.5', 'qwen2.5'] });

    const page = (await render()).nativeElement as HTMLElement;
    const options = page.querySelectorAll<HTMLOptionElement>('select[name="model"] option');

    expect([...options].map((option) => option.value)).toEqual(['llama3.2', 'lfm2.5', 'qwen2.5']);
  });

  it('re-asks when the form is pointed at another Ollama', async () => {
    /* The dropdown lists one server's models; a different server has its own. */
    const fixture = await render();
    const page = fixture.nativeElement as HTMLElement;

    const server = page.querySelector<HTMLInputElement>('input[name="baseUrl"]')!;
    server.value = 'http://10.0.0.5:11434';
    server.dispatchEvent(new Event('input'));
    server.dispatchEvent(new Event('change'));
    await fixture.whenStable();

    expect(agent.modelsAsked).toEqual([DEFAULTS.base_url, 'http://10.0.0.5:11434']);
  });

  it('says Ollama is unreachable rather than pretending it has no models', async () => {
    /* An empty model list and a server that never answered are different things. */
    agent.modelsResponse = throwError(() => new Error('connection refused'));

    const page = (await render()).nativeElement as HTMLElement;

    expect(page.querySelector('.ollama')!.textContent).toContain('Ollama unreachable');
    expect(page.querySelector('.ollama')!.classList).not.toContain('up');
  });

  it('re-asks the same server when the Ollama pill is clicked', async () => {
    const fixture = await render();
    const page = fixture.nativeElement as HTMLElement;

    page.querySelector<HTMLButtonElement>('.ollama')!.click();
    await fixture.whenStable();

    expect(agent.modelsAsked).toEqual([DEFAULTS.base_url, STATUS.base_url]);
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
      line: { level: 'INFO', logger: 'buy_agent.search', message: 'Search returned 10 results' },
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
    agent.stream.next({ kind: 'failure', message: 'Start it with: ollama serve', status: 503 });
    agent.stream.complete();
    await fixture.whenStable();

    expect(page.querySelector('.banner')!.textContent).toContain('ollama serve');
    expect(page.querySelector('.results')).toBeNull();
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

  it('stops a run by closing the stream', async () => {
    const fixture = await render();
    await searchFor(fixture, 'kettle');

    const page = fixture.nativeElement as HTMLElement;
    page.querySelector<HTMLButtonElement>('.actions button')!.click();
    await fixture.whenStable();

    expect(agent.unsubscribed).toBe(true);
    expect(page.textContent).toContain('Stopped.');
    expect(page.querySelector('button[type="submit"]')).not.toBeNull();
  });

  it('closes the stream when the page goes away', async () => {
    const fixture = await render();
    await searchFor(fixture, 'kettle');
    fixture.destroy();
    expect(agent.unsubscribed).toBe(true);
  });
});
