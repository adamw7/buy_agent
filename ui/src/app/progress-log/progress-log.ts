import { Component, ElementRef, effect, input, viewChild } from '@angular/core';

import type { LogLine } from '../agent.types';

/**
 * The run's log, as it happens.
 *
 * A search takes tens of seconds -- refining the query, fetching ten pages, then
 * the slow part, extraction -- so the same lines the CLI prints are what tells
 * the page it is still working.
 */
@Component({
  selector: 'app-progress-log',
  templateUrl: './progress-log.html',
  styleUrl: './progress-log.css',
})
export class ProgressLog {
  readonly lines = input.required<LogLine[]>();
  readonly running = input(false);

  private readonly scroller = viewChild<ElementRef<HTMLElement>>('scroller');

  constructor() {
    // Follow the tail, the way a terminal does.
    effect(() => {
      this.lines();
      const element = this.scroller()?.nativeElement;
      if (element) {
        element.scrollTop = element.scrollHeight;
      }
    });
  }

  protected shortName(logger: string): string {
    return logger.replace(/^buy_agent\.?/, '') || 'agent';
  }
}
