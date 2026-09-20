import {
  Component,
  DestroyRef,
  ElementRef,
  afterRenderEffect,
  computed,
  effect,
  inject,
  input,
  signal,
  viewChild,
} from '@angular/core';

import type { LogLine } from '../agent.types';
import { filename, saveText } from '../save';

/** The run's log, as it happens. */
@Component({
  selector: 'app-progress-log',
  templateUrl: './progress-log.html',
  styleUrl: './progress-log.css',
})
export class ProgressLog {
  readonly lines = input.required<LogLine[]>();
  readonly running = input(false);
  /** What ended the run badly, if anything -- one of the two things the offered file is for. */
  readonly failure = input<string | null>(null);
  /** Whether the reader ended the run themselves. */
  readonly stopped = input(false);

  /** Whether this run left something worth keeping. */
  protected readonly keepable = computed(() => this.failure() !== null || this.stopped());

  private readonly scroller = viewChild<ElementRef<HTMLElement>>('scroller');

  /** When the run on screen started, and a clock that moves while it runs. */
  private readonly startedAt = signal(0);
  private readonly now = signal(0);
  private ticking: ReturnType<typeof setInterval> | null = null;

  /** How long the run has been going, or how long it took. */
  protected readonly elapsed = computed(() => {
    const started = this.startedAt();
    return started ? duration(this.now() - started) : '';
  });

  /** What the pill says once the run has stopped: how much it logged and how long that took. */
  protected readonly summary = computed(() => {
    const count = this.lines().length;
    // Counted in English, like the two other counts on the page -- the header's
    // "4 models" and the form's "1 setting to look at". A run refused before it
    // started logs one line, so "1 lines" is the reading this pill gets most often
    // when something has gone wrong.
    const lines = `${count} line${count === 1 ? '' : 's'}`;
    const took = this.elapsed();
    return took ? `${lines} · ${took}` : lines;
  });

  /** What an empty panel says, which is not the same thing twice. */
  protected readonly nothingYet = computed(() =>
    this.running() ? 'Waiting for the first step…' : 'The run ended before it logged anything.',
  );

  /** Whether new lines should pull the panel down with them. */
  private sticking = true;

  constructor() {
    // The clock starts with the run and stops with it, leaving the total on screen.
    effect(() => this.time(this.running()));
    inject(DestroyRef).onDestroy(() => this.idle());

    // Follow the tail, the way a terminal does -- but only while the reader is still at it.
    afterRenderEffect(() => {
      this.lines();
      const element = this.scroller()?.nativeElement;
      if (element && this.sticking) {
        element.scrollTop = element.scrollHeight;
      }
    });
  }

  /** Take the reader's position as the answer to "keep following?". */
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

  /** The level, where it is one this panel colours, and nothing where it is not:
   *  every line has a level and naming it on all of them is three columns saying
   *  INFO down the left edge. */
  protected marked(level: string): string {
    return COLOURED.includes(level) ? level : '';
  }

  protected shortName(logger: string): string {
    return logger.replace(/^buy_agent\.?/, '') || 'agent';
  }

  /** Hand the whole run over as a text file. */
  protected download(): void {
    const when = new Date();
    saveText(logFilename(when), transcript(this.lines(), this.failure(), when), 'text/plain');
  }
}

/** How near the bottom still counts as being at it: a line's height, so a panel
 *  a pixel or two off the end -- which a fractional scroll position leaves it --
 *  is not read as someone having deliberately scrolled away. */
const STICK_MARGIN = 24;

/** The levels the panel gives a colour to, which are the levels it names. */
const COLOURED = ['WARNING', 'ERROR'];

/** How often the elapsed time is redrawn. */
const TICK_MS = 1000;

/** A wait as a person would say it: `8s`, `2m 14s`. */
export function duration(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

/** The run as a file: the lines the panel shows, and the error that ended it. */
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

export function logFilename(when: Date): string {
  return filename('log', 'txt', when);
}
