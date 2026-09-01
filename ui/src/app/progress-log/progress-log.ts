import {
  Component,
  DestroyRef,
  ElementRef,
  computed,
  effect,
  inject,
  input,
  signal,
  viewChild,
} from '@angular/core';

import type { LogLine } from '../agent.types';
import { filename, saveText } from '../save';

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

  /** When the run on screen started, and a clock that moves while it runs.
   *  Signals rather than plain fields because the header reads them once a
   *  second -- see `elapsed`, which is the only thing on the panel that moves
   *  through the step that takes the longest. */
  private readonly startedAt = signal(0);
  private readonly now = signal(0);
  private ticking: ReturnType<typeof setInterval> | null = null;

  /**
   * How long the run has been going, or how long it took.
   *
   * Extraction is the slow step and it logs nothing at all while it runs, so for
   * minutes at a time the panel is a frozen list of lines under a pulsing dot,
   * with no way to tell a model thinking from a model that has stopped
   * answering. The timestamps say that afterwards; this says it while it is
   * happening. It is presentation and not judgement -- a duration off the
   * browser's own clock, written here the way `shortName` trims a logger name.
   */
  protected readonly elapsed = computed(() => {
    const started = this.startedAt();
    return started ? duration(this.now() - started) : '';
  });

  /** What the pill says once the run has stopped: how much it logged and how
   *  long that took. The time is dropped for a panel that has not seen a run --
   *  there is no duration to report before the first one. */
  protected readonly summary = computed(() => {
    const lines = `${this.lines().length} lines`;
    const took = this.elapsed();
    return took ? `${lines} · ${took}` : lines;
  });

  /** Whether new lines should pull the panel down with them. A plain field and
   *  not a signal: it is read by the effect that scrolls, and as a signal it
   *  would be a dependency of its own writes. */
  private sticking = true;

  constructor() {
    // The clock starts with the run and stops with it, leaving the total on
    // screen. This effect reads `running` and none of what it writes, so it runs
    // exactly on the two transitions and a stamp is never reset mid-run.
    effect(() => this.time(this.running()));
    inject(DestroyRef).onDestroy(() => this.idle());

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

  /** Start the clock for a run that has begun, or stop it and leave the total. */
  private time(running: boolean): void {
    this.idle();
    this.now.set(Date.now());
    if (running) {
      this.startedAt.set(Date.now());
      this.ticking = setInterval(() => this.now.set(Date.now()), TICK_MS);
    }
  }

  /** Stop the clock, wherever it had got to. */
  private idle(): void {
    if (this.ticking !== null) {
      clearInterval(this.ticking);
      this.ticking = null;
    }
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
    saveText(logFilename(when), transcript(this.lines(), this.failure(), when), 'text/plain');
  }
}

/** How near the bottom still counts as being at it: a line's height, so a panel
 *  a pixel or two off the end -- which a fractional scroll position leaves it --
 *  is not read as someone having deliberately scrolled away. */
const STICK_MARGIN = 24;

/** How often the elapsed time is redrawn. A second, because that is the unit it
 *  is shown in -- anything finer would repaint for a digit nobody is reading. */
const TICK_MS = 1000;

/**
 * A wait as a person would say it: `8s`, `2m 14s`.
 *
 * Seconds throughout rather than a `0:08` clock: this is how long something has
 * taken, not what time it is, and the two read differently at a glance.
 */
export function duration(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

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
  return filename('log', 'txt', when);
}
