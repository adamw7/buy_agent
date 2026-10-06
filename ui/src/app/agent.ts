import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import type {
  AgentDefaults,
  BoundsCheck,
  ModelSource,
  ModelStatus,
  PayOptions,
  RankOptions,
  Receipt,
  SearchEvent,
  SearchOptions,
  SearchResult,
  SourcesCheck,
} from './agent.types';

/** The browser's half of the agent: the JSON endpoints, and a run's event stream. */
@Injectable({ providedIn: 'root' })
export class AgentService {
  private readonly http = inject(HttpClient);

  defaults(): Observable<AgentDefaults> {
    return this.http.get<AgentDefaults>('/api/config');
  }

  models(source: ModelSource): Observable<ModelStatus> {
    return this.http.get<ModelStatus>('/api/models', { params: { ...source } });
  }

  checkSources(sources: string): Observable<SourcesCheck> {
    return this.http.get<SourcesCheck>('/api/sources', { params: { sources } });
  }

  checkBounds(request: string): Observable<BoundsCheck> {
    return this.http.get<BoundsCheck>('/api/bounds', { params: { request } });
  }

  rank(options: RankOptions): Observable<SearchResult> {
    return this.http.post<SearchResult>('/api/rank', options);
  }

  pay(options: PayOptions): Observable<{ receipt: Receipt }> {
    return this.http.post<{ receipt: Receipt }>('/api/pay', options);
  }

  /** Run a search: log lines as they happen, then a `result` or a `failure`. */
  search(options: SearchOptions): Observable<SearchEvent> {
    return new Observable<SearchEvent>((subscriber) => {
      const source = new EventSource(`/api/search/stream?${toQuery(options)}`);
      let ended = false;

      const finish = (event: SearchEvent) => {
        ended = true;
        subscriber.next(event);
        source.close();
        subscriber.complete();
      };

      source.addEventListener('log', (event) => {
        subscriber.next({ kind: 'log', line: JSON.parse(event.data) });
      });

      source.addEventListener('result', (event) => {
        finish({ kind: 'result', result: JSON.parse(event.data) });
      });

      source.addEventListener('failure', (event) => {
        const payload = JSON.parse(event.data);
        // `field` names the box to mark, where there is one (ADR-0033).
        finish({
          kind: 'failure',
          message: payload.error,
          status: payload.status,
          field: payload.field ?? null,
        });
      });

      // EventSource would reconnect and rerun the search, so close it ourselves.
      source.addEventListener('error', () => {
        if (ended) {
          return;
        }
        ended = true;
        source.close();
        subscriber.error(new Error('Lost the connection to the agent. Is it still running?'));
      });

      return () => {
        ended = true;
        source.close();
      };
    });
  }
}

/** The screenshot URL for `url`, for an `<img>` to load lazily (ADR-0065). */
export function screenshotUrl(url: string): string {
  return `/api/screenshot?${new URLSearchParams({ url })}`;
}

/** Leaves out anything unset. */
export function toQuery(options: SearchOptions): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(options)) {
    if (value === null || value === undefined || value === '') {
      continue;
    }
    params.set(key, String(value));
  }
  return params.toString();
}
