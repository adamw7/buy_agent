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

  it('asks what a server is serving, naming both the provider and the address', () => {
    /* The picker is per-server, and the address alone is not the question: the
       same URL is asked one way for Ollama and another for vLLM. */
    let answered: ModelStatus | null = null;
    service
      .models({ provider: 'vllm', base_url: 'http://elsewhere:8000/v1' })
      .subscribe((status) => (answered = status));

    const http = TestBed.inject(HttpTestingController);
    const asked = http.expectOne((request) => request.url === '/api/models');
    expect(asked.request.params.get('provider')).toBe('vllm');
    expect(asked.request.params.get('base_url')).toBe('http://elsewhere:8000/v1');

    asked.flush({
      provider: 'vllm',
      label: 'vLLM',
      base_url: 'http://elsewhere:8000/v1',
      reachable: true,
      models: [{ name: 'Qwen/Qwen3-8B', completion: true }],
    });
    expect(answered!.models).toEqual([{ name: 'Qwen/Qwen3-8B', completion: true }]);
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

    source.send('log', {
      time: '18:12:19',
      level: 'INFO',
      logger: 'buy_agent.search',
      message: 'Searching',
    });
    source.send('result', {
      request: 'kettle',
      count: 0,
      top_n: 3,
      sort_by: 'score',
      products: [],
    });

    expect(seen[0]).toEqual({
      kind: 'log',
      line: {
        time: '18:12:19',
        level: 'INFO',
        logger: 'buy_agent.search',
        message: 'Searching',
      },
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

    FakeEventSource.last!.send('failure', { error: 'ollama serve', status: 503, field: null });

    expect(seen).toEqual([{ kind: 'failure', message: 'ollama serve', status: 503, field: null }]);
    expect(failed).toBeNull();
  });

  it('carries the field a refused value came out of, where Python named one', () => {
    /* Which box the sentence belongs under is Python's answer, not one read back
       out of the message (ADR-0033). */
    const seen: SearchEvent[] = [];
    service.search({ request: 'kettle' }).subscribe((event) => seen.push(event));

    FakeEventSource.last!.send('failure', {
      error: 'results must be between 1 and 50; got 51.',
      status: 400,
      field: 'results',
    });

    expect(seen[0]).toMatchObject({ kind: 'failure', field: 'results' });
  });

  it('asks the server what a trusted sources field holds', () => {
    /* Rather than parsing a source in TypeScript, which is the drift ADR-0031
       refused for the region. */
    let answered = '';
    service.checkSources('Marques Brownlee').subscribe((check) => (answered = check.error));

    const http = TestBed.inject(HttpTestingController);
    const asked = http.expectOne((request) => request.url === '/api/sources');
    expect(asked.request.params.get('sources')).toBe('Marques Brownlee');

    asked.flush({ sources: 'Marques Brownlee', error: "'Marques' does not name a source." });
    expect(answered).toContain('does not name a source');
    http.verify();
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
