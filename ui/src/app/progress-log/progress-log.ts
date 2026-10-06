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
  readonly failure = input<string | null>(null);
  /** The reader ended the run themselves. */
  readonly stopped = input(false);

  /** A failed or stopped run leaves a log worth keeping. */
  protected readonly keepable = computed(() => this.failure() !== null || this.stopped());

  private readonly scroller = viewChild<ElementRef<HTMLElement>>('scroller');

  private readonly startedAt = signal(0);
  private readonly now = signal(0);
  private ticking: ReturnType<typeof setInterval> | null = null;

  protected readonly elapsed = computed(() => {
    const started = this.startedAt();
    return started ? duration(this.now() - started) : '';
  });

  /** How much the run logged and how long it took. */
  protected readonly summary = computed(() => {
    const count = this.lines().length;
    const lines = `${count} line${count === 1 ? '' : 's'}`;
    const took = this.elapsed();
    return took ? `${lines} · ${took}` : lines;
  });

  protected readonly nothingYet = computed(() =>
    this.running() ? 'Waiting for the first step…' : 'The run ended before it logged anything.',
  );

  /** Whether new lines pull the panel down with them. */
  private sticking = true;

  constructor() {
    // Stops with the run, leaving the total on screen.
    effect(() => this.time(this.running()));
    inject(DestroyRef).onDestroy(() => this.idle());

    // Follow the tail, as a terminal does, while the reader is still at it.
    afterRenderEffect(() => {
      this.lines();
      const element = this.scroller()?.nativeElement;
      if (element && this.sticking) {
        element.scrollTop = element.scrollHeight;
      }
    });
  }

  protected follow(event: Event): void {
    // The event's target: the view query may not have resolved yet.
    const element = event.target as HTMLElement;
    const fromBottom = element.scrollHeight - element.scrollTop - element.clientHeight;
    this.sticking = fromBottom <= STICK_MARGIN;
  }

  private time(running: boolean): void {
    this.idle();
    this.now.set(Date.now());
    if (running) {
      this.startedAt.set(Date.now());
      this.ticking = setInterval(() => this.now.set(Date.now()), TICK_MS);
    }
  }

  private idle(): void {
    if (this.ticking !== null) {
      clearInterval(this.ticking);
      this.ticking = null;
    }
  }

  /** Named wherever it is coloured: colour is never the only carrier. */
  protected marked(level: string): string {
    return COLOURED.includes(level) ? level : '';
  }

  protected shortName(logger: string): string {
    return logger.replace(/^buy_agent\.?/, '') || 'agent';
  }

  protected download(): void {
    const when = new Date();
    saveText(logFilename(when), transcript(this.lines(), this.failure(), when), 'text/plain');
  }
}

/** A line's height, tolerating fractional scroll. */
const STICK_MARGIN = 24;
const COLOURED = ['WARNING', 'ERROR'];
const TICK_MS = 1000;

/** `8s`, `2m 14s`. */
export function duration(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

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
