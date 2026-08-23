import { TestBed } from '@angular/core/testing';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideHttpClient } from '@angular/common/http';

import { AgentService, toQuery } from './agent';
import type { ModelStatus, SearchEvent } from './agent.types';

/** A stand-in for the browser's EventSource, so no test opens a connection. */
class FakeEventSource {
  static last: FakeEventSource | null = null;

  readonly listeners = new Map<string, ((event: MessageEvent) => void)[]>();
  closed = false;

  constructor(readonly url: string) {
    FakeEventSource.last = this;
  }

  addEventListener(name: string, handler: (event: MessageEvent) => void): void {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), handler]);
  }

  close(): void {
    this.closed = true;
  }

  send(name: string, data?: unknown): void {
    for (const handler of this.listeners.get(name) ?? []) {
      handler({ data: JSON.stringify(data) } as MessageEvent);
    }
  }
}

describe('toQuery', () => {
  it('leaves out anything unset, so the server falls back to its own defaults', () => {
    const query = toQuery({ request: 'kettle', model: '', num_ctx: null, think: undefined });
    expect(query).toBe('request=kettle');
  });

  it('keeps values that are meaningfully false or zero', () => {
    const query = new URLSearchParams(toQuery({ request: 'kettle', fetch: false, temperature: 0 }));
    expect(query.get('fetch')).toBe('false');
    expect(query.get('temperature')).toBe('0');
  });
});

describe('AgentService', () => {
  let service: AgentService;
  let original: unknown;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(AgentService);
    original = (globalThis as Record<string, unknown>)['EventSource'];
    (globalThis as Record<string, unknown>)['EventSource'] = FakeEventSource;
    FakeEventSource.last = null;
  });

  afterEach(() => {
    (globalThis as Record<string, unknown>)['EventSource'] = original;
  });

  it('asks the agent for the form defaults', () => {
    service.defaults().subscribe();
    const http = TestBed.inject(HttpTestingController);
    http.expectOne('/api/config').flush({});
    http.verify();
  });

  it('asks which models are pulled, naming the server to ask', () => {
    /* The picker is per-server: the form can point at an Ollama elsewhere. */
    let answered: ModelStatus | null = null;
    service.models('http://elsewhere:11434').subscribe((status) => (answered = status));

    const http = TestBed.inject(HttpTestingController);
    const asked = http.expectOne((request) => request.url === '/api/models');
    expect(asked.request.params.get('base_url')).toBe('http://elsewhere:11434');

    asked.flush({ base_url: 'http://elsewhere:11434', reachable: true, models: ['lfm2.5'] });
    expect(answered!.models).toEqual(['lfm2.5']);
    http.verify();
  });

  it('relays progress and finishes on the result', () => {
    const seen: SearchEvent[] = [];
    let completed = false;
    service.search({ request: 'kettle' }).subscribe({
      next: (event) => seen.push(event),
      complete: () => (completed = true),
    });

    const source = FakeEventSource.last!;
    expect(source.url).toBe('/api/search/stream?request=kettle');

    source.send('log', { level: 'INFO', logger: 'buy_agent.search', message: 'Searching' });
    source.send('result', {
      request: 'kettle',
      count: 0,
      top_n: 3,
      sort_by: 'score',
      products: [],
    });

    expect(seen[0]).toEqual({
      kind: 'log',
      line: { level: 'INFO', logger: 'buy_agent.search', message: 'Searching' },
    });
    expect(seen[1].kind).toBe('result');
    expect(completed).toBe(true);
    expect(source.closed).toBe(true);
  });

  it('reports a failed run as an event, not as a broken stream', () => {
    const seen: SearchEvent[] = [];
    let failed: unknown = null;
    service.search({ request: 'kettle' }).subscribe({
      next: (event) => seen.push(event),
      error: (error) => (failed = error),
    });

    FakeEventSource.last!.send('failure', { error: 'ollama serve', status: 503 });

    expect(seen).toEqual([{ kind: 'failure', message: 'ollama serve', status: 503 }]);
    expect(failed).toBeNull();
  });

  it('turns a dropped connection into an error instead of silently restarting', () => {
    /* EventSource reconnects on its own, which would run the whole search again. */
    let failed: Error | null = null;
    service.search({ request: 'kettle' }).subscribe({ error: (error) => (failed = error) });

    FakeEventSource.last!.send('error');

    expect(failed).toBeInstanceOf(Error);
    expect(FakeEventSource.last!.closed).toBe(true);
  });

  it('ignores the transport error EventSource fires as the stream closes', () => {
    let failed: Error | null = null;
    service.search({ request: 'kettle' }).subscribe({ error: (error) => (failed = error) });

    const source = FakeEventSource.last!;
    source.send('result', {
      request: 'kettle',
      count: 0,
      top_n: 3,
      sort_by: 'score',
      products: [],
    });
    source.send('error');

    expect(failed).toBeNull();
  });

  it('closes the stream when the caller unsubscribes, which is what Stop does', () => {
    const run = service.search({ request: 'kettle' }).subscribe();
    run.unsubscribe();
    expect(FakeEventSource.last!.closed).toBe(true);
  });
});
