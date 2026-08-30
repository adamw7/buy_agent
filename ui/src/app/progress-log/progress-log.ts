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
  /** What ended the run badly, if anything -- what the offered file is for. */
  readonly failure = input<string | null>(null);

  private readonly scroller = viewChild<ElementRef<HTMLElement>>('scroller');

  /** Whether new lines should pull the panel down with them. A plain field and
   *  not a signal: it is read by the effect that scrolls, and as a signal it
   *  would be a dependency of its own writes. */
  private sticking = true;

  constructor() {
    // Follow the tail, the way a terminal does -- but only while the reader is
    // still at the tail. A run logs for a minute, so scrolling up to re-read the
    // refined query used to last until the next line arrived and yanked the panel
    // back down; there is no reading a finished step out of a live run that way.
    effect(() => {
      this.lines();
      const element = this.scroller()?.nativeElement;
      if (element && this.sticking) {
        element.scrollTop = element.scrollHeight;
      }
    });
  }

  /**
   * Take the reader's position as the answer to "keep following?".
   *
   * Scrolled back up, they are reading something and new lines must not move it;
   * scrolled to the bottom -- including by this component's own scrolling, which
   * fires this too -- they are watching the run and the tail should follow.
   */
  protected follow(event: Event): void {
    // The element that scrolled, rather than the view query: it is the panel
    // either way, and an event has one where a query may not have resolved yet.
    const element = event.target as HTMLElement;
    const fromBottom = element.scrollHeight - element.scrollTop - element.clientHeight;
    this.sticking = fromBottom <= STICK_MARGIN;
  }

  protected shortName(logger: string): string {
    return logger.replace(/^buy_agent\.?/, '') || 'agent';
  }

  /**
   * Hand the whole run over as a text file.
   *
   * The panel scrolls and is thrown away by the next search, so a run that
   * failed leaves nothing to attach to a bug report; this is that attachment.
   */
  protected download(): void {
    const when = new Date();
    const blob = new Blob([transcript(this.lines(), this.failure(), when)], {
      type: 'text/plain;charset=utf-8',
    });
    const href = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = href;
    link.download = logFilename(when);
    link.click();
    URL.revokeObjectURL(href);
  }
}

/** How near the bottom still counts as being at it: a line's height, so a panel
 *  a pixel or two off the end -- which a fractional scroll position leaves it --
 *  is not read as someone having deliberately scrolled away. */
const STICK_MARGIN = 24;

/**
 * The run as a file: the lines the panel shows, and the error that ended it.
 *
 * The error is the part the panel never had -- a failure arrives as its own
 * event rather than as a log line -- and it is the first thing anyone reading
 * the file will want, so it is written where the run stopped.
 *
 * Logger names are kept whole here, unlike on screen: the fixed column is worth
 * trimming a prefix for, a bug report is not. Each line keeps the time Python
 * logged it at, which is what says where a slow run spent its minutes.
 */
export function transcript(lines: LogLine[], failure: string | null, when: Date): string {
  const body = lines.length
    ? lines.map(
        (line) => `${line.time} ${line.level.padEnd(8)} ${line.logger.padEnd(22)} ${line.message}`,
      )
    : ['(the run produced no log lines)'];
  return [
    `buy_agent run — ${when.toISOString()}`,
    '',
    ...body,
    ...(failure ? ['', `FAILED: ${failure}`] : []),
    '',
  ].join('\n');
}

/** A name that sorts by when the file was taken, and survives every filesystem. */
export function logFilename(when: Date): string {
  const stamp = when.toISOString().slice(0, 19).replace(/[-:]/g, '').replace('T', '-');
  return `buy-agent-log-${stamp}.txt`;
}
