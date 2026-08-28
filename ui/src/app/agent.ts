import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import type {
  AgentDefaults,
  ModelSource,
  ModelStatus,
  SearchEvent,
  SearchOptions,
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

  /**
   * Run a search, emitting the agent's log lines as they happen and finishing on
   * a `result` or a `failure`.
   *
   * A run takes tens of seconds, which is why this streams rather than answering
   * once at the end: without it the page would sit silent through the whole
   * search. Unsubscribing closes the stream, which is how the Stop button works.
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
        finish({ kind: 'failure', message: payload.error, status: payload.status });
      });

      // EventSource reports transport trouble here, and would then reconnect and
      // start the search over -- so close it ourselves and say what happened.
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

/**
 * Turn options into a query string, leaving out anything unset.
 *
 * Omitting a blank field matters: the server reads a missing key as "use the
 * default", so sending `num_ctx=` would be the same as sending nothing, while
 * sending `model=` would ask for a model with no name.
 */
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
