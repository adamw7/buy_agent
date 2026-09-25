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

/**
 * The browser's half of the agent: the JSON endpoints, and the event stream a
 * run reports its progress on.
 */
@Injectable({ providedIn: 'root' })
export class AgentService {
  private readonly http = inject(HttpClient);

  /** The form's starting values -- the same defaults the CLI shows in `--help`. */
  defaults(): Observable<AgentDefaults> {
    return this.http.get<AgentDefaults>('/api/config');
  }

  /** What that model server is serving, or why it could not be asked. */
  models(source: ModelSource): Observable<ModelStatus> {
    return this.http.get<ModelStatus>('/api/models', { params: { ...source } });
  }

  /** What the server makes of a Trusted sources field, before a run is started. */
  checkSources(sources: string): Observable<SourcesCheck> {
    return this.http.get<SourcesCheck>('/api/sources', { params: { sources } });
  }

  /** Bounds the request states in words, for the form to offer (see `BoundsCheck`). */
  checkBounds(request: string): Observable<BoundsCheck> {
    return this.http.get<BoundsCheck>('/api/bounds', { params: { request } });
  }

  /** Put a finished run's products in another order, without running it again. */
  rank(options: RankOptions): Observable<SearchResult> {
    return this.http.post<SearchResult>('/api/rank', options);
  }

  /** Buy one product of a finished run, having been shown that it was approved. */
  pay(options: PayOptions): Observable<{ receipt: Receipt }> {
    return this.http.post<{ receipt: Receipt }>('/api/pay', options);
  }

  /**
   * Run a search, emitting the agent's log lines as they happen and finishing on a `result` or a
   * `failure`.
   */
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

/** Turn options into a query string, leaving out anything unset. */
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
