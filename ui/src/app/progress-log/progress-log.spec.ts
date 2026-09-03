import { TestBed } from '@angular/core/testing';
import { afterEach, vi } from 'vitest';

import { ProgressLog, duration, logFilename, transcript } from './progress-log';
import type { LogLine } from '../agent.types';

const LINES: LogLine[] = [
  {
    time: '18:12:17',
    level: 'INFO',
    logger: 'buy_agent.agent',
    message: 'Refined search query: kettle price',
  },
  {
    time: '18:12:20',
    level: 'WARNING',
    logger: 'buy_agent.fetch',
    message: 'Got usable page text from 8 of 10',
  },
];

async function render(
  lines: LogLine[],
  running = false,
  failure: string | null = null,
): Promise<HTMLElement> {
  const fixture = TestBed.createComponent(ProgressLog);
  fixture.componentRef.setInput('lines', lines);
  fixture.componentRef.setInput('running', running);
  fixture.componentRef.setInput('failure', failure);
  await fixture.whenStable();
  return fixture.nativeElement as HTMLElement;
}

/**
 * Catch the download instead of performing it.
 *
 * jsdom has neither object URLs nor navigation, so the two ends of the browser's
 * save-a-file dance are stubbed: what the test wants is the blob that went in
 * and the name the link asked for.
 */
function interceptDownload(): { saved: () => HTMLAnchorElement; blobs: Blob[] } {
  const blobs: Blob[] = [];
  const links: HTMLAnchorElement[] = [];
  const revoked: string[] = [];

  vi.spyOn(URL, 'createObjectURL').mockImplementation((blob) => {
    blobs.push(blob as Blob);
    return `blob:log-${blobs.length}`;
  });
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation((href) => void revoked.push(href));
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    links.push(this);
  });

  return {
    blobs,
    saved: () => {
      // The URL is handed back as soon as the click is over: a page that keeps
      // making these leaks the blob until it is reloaded.
      expect(revoked).toEqual(links.map((link) => link.getAttribute('href')));
      return links[links.length - 1];
    },
  };
}

describe('ProgressLog', () => {
  it('shows the agent lines with the package prefix trimmed off', async () => {
    const log = await render(LINES);
    const rows = log.querySelectorAll('.line');
    expect(rows).toHaveLength(2);
    expect(rows[0].querySelector('.source')!.textContent!.trim()).toBe('agent');
    expect(rows[0].textContent).toContain('Refined search query');
  });

  it('marks the levels that mean something went sideways', async () => {
    const rows = (await render(LINES)).querySelectorAll('.line');
    expect(rows[0].classList).not.toContain('warn');
    expect(rows[1].classList).toContain('warn');
  });

  it('says it is working before the first line arrives', async () => {
    const log = await render([], true);
    expect(log.textContent).toContain('working');
    expect(log.querySelector('.idle')).not.toBeNull();
  });

  it('counts the lines once the run is over', async () => {
    expect((await render(LINES)).querySelector('.pill')!.textContent).toContain('2 lines');
  });

  it('counts the wait out while the slow step is saying nothing', async () => {
    /* Extraction logs nothing at all for as long as it takes, so without this
       the panel is a frozen list of lines under a pulsing dot -- with no way to
       tell a model still thinking from one that has stopped answering. */
    // The clock and the ticker only: Angular's own scheduler runs on timeouts
    // and microtasks, and freezing those hangs `whenStable` rather than the run.
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] });
    try {
      const fixture = TestBed.createComponent(ProgressLog);
      fixture.componentRef.setInput('lines', LINES);
      fixture.componentRef.setInput('running', true);
      await fixture.whenStable();
      const page = fixture.nativeElement as HTMLElement;

      expect(page.querySelector('.pill')!.textContent).toContain('working · 0s');

      await vi.advanceTimersByTimeAsync(75_000);
      await fixture.whenStable();
      expect(page.querySelector('.pill')!.textContent).toContain('working · 1m 15s');

      // Stopped, the clock stops with it and the total stays on the panel: how
      // long a run took is the question the next one is planned against.
      fixture.componentRef.setInput('running', false);
      await fixture.whenStable();
      await vi.advanceTimersByTimeAsync(30_000);
      await fixture.whenStable();
      expect(page.querySelector('.pill')!.textContent).toContain('2 lines · 1m 15s');
    } finally {
      vi.useRealTimers();
    }
  });

  it('stops its clock when the panel goes away', async () => {
    /* A ticker that outlives the component it was drawing ticks for the life of
       the tab -- and this one is started again by every run. */
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] });
    try {
      const fixture = TestBed.createComponent(ProgressLog);
      fixture.componentRef.setInput('lines', LINES);
      fixture.componentRef.setInput('running', true);
      await fixture.whenStable();
      expect(vi.getTimerCount()).toBe(1);

      fixture.destroy();
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it('says a wait the way somebody waiting would', () => {
    expect(duration(0)).toBe('0s');
    expect(duration(8_400)).toBe('8s');
    expect(duration(59_600)).toBe('1m 0s');
    expect(duration(134_000)).toBe('2m 14s');
    // A clock corrected mid-run is the only way this goes backwards, and a
    // negative wait is not a thing to put on screen.
    expect(duration(-5_000)).toBe('0s');
  });

  it('stops promising a first step once the run is over', async () => {
    /* A run refused before it starts -- a region the server will not take --
       logs nothing at all, and "Waiting for the first step…" under the banner
       explaining that says something is still coming. Nothing is. */
    expect((await render([], true)).querySelector('.idle')!.textContent).toContain(
      'Waiting for the first step',
    );
    expect((await render([], false)).querySelector('.idle')!.textContent).toContain(
      'ended before it logged anything',
    );
  });

  it('shows the time Python logged each line at', async () => {
    const rows = (await render(LINES)).querySelectorAll('.line');
    expect(rows[0].querySelector('.at')!.textContent!.trim()).toBe('18:12:17');
    expect(rows[1].querySelector('.at')!.textContent!.trim()).toBe('18:12:20');
  });
});

describe('ProgressLog scrolling', () => {
  /**
   * Give the panel a real overflow, which jsdom lays nothing out to produce.
   *
   * The three numbers are all the component reads, and they are what a browser
   * would have measured: a scroller a third the height of its contents.
   */
  function measure(element: HTMLElement, scrollTop: number): void {
    for (const [name, value] of Object.entries({
      scrollHeight: 900,
      clientHeight: 300,
    })) {
      Object.defineProperty(element, name, { value, configurable: true });
    }
    let position = scrollTop;
    Object.defineProperty(element, 'scrollTop', {
      configurable: true,
      get: () => position,
      set: (value: number) => void (position = value),
    });
  }

  async function panel(): Promise<{ scroller: HTMLElement; add: () => Promise<void> }> {
    const fixture = TestBed.createComponent(ProgressLog);
    fixture.componentRef.setInput('lines', LINES);
    fixture.componentRef.setInput('running', true);
    await fixture.whenStable();
    const scroller = (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>(
      '.scroller',
    )!;
    return {
      scroller,
      add: async () => {
        fixture.componentRef.setInput('lines', [
          ...LINES,
          { time: '18:12:24', level: 'INFO', logger: 'buy_agent', message: 'Extracting' },
        ]);
        await fixture.whenStable();
      },
    };
  }

  it('follows the tail while the reader is at the bottom', async () => {
    const { scroller, add } = await panel();
    measure(scroller, 600); // 900 - 300: the end
    scroller.dispatchEvent(new Event('scroll'));

    await add();

    expect(scroller.scrollTop).toBe(900);
  });

  it('leaves the view alone once the reader has scrolled up', async () => {
    const { scroller, add } = await panel();
    measure(scroller, 120); // reading something further back
    scroller.dispatchEvent(new Event('scroll'));

    await add();

    expect(scroller.scrollTop).toBe(120);
  });

  it('picks the tail back up when the reader returns to the bottom', async () => {
    const { scroller, add } = await panel();
    measure(scroller, 120);
    scroller.dispatchEvent(new Event('scroll'));
    scroller.scrollTop = 600;
    scroller.dispatchEvent(new Event('scroll'));

    await add();

    expect(scroller.scrollTop).toBe(900);
  });

  it('measures the panel after the new lines are in it', async () => {
    // The one thing `measure` above cannot say, its height being a constant: a
    // browser's grows as lines land in it. Measured before the render -- which
    // is when a plain `effect` runs -- the height is the one from before the
    // line that woke it, and the panel comes to rest a line short of the bottom
    // every time. The scroll event for that stale position then arrives after
    // the render, reads the gap as the reader having scrolled away, and the
    // panel stops following for the rest of the run.
    const fixture = TestBed.createComponent(ProgressLog);
    fixture.componentRef.setInput('lines', LINES);
    fixture.componentRef.setInput('running', true);
    await fixture.whenStable();
    const scroller = (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>(
      '.scroller',
    )!;

    const ROW = 30;
    Object.defineProperty(scroller, 'clientHeight', { value: 30, configurable: true });
    Object.defineProperty(scroller, 'scrollHeight', {
      configurable: true,
      get: () => scroller.querySelectorAll('.line').length * ROW,
    });
    let position = LINES.length * ROW - 30;
    Object.defineProperty(scroller, 'scrollTop', {
      configurable: true,
      get: () => position,
      set: (value: number) => void (position = value),
    });
    scroller.dispatchEvent(new Event('scroll'));

    fixture.componentRef.setInput('lines', [
      ...LINES,
      { time: '18:12:24', level: 'INFO', logger: 'buy_agent', message: 'Extracting' },
      { time: '18:12:25', level: 'INFO', logger: 'buy_agent', message: 'Extracted 10' },
    ]);
    await fixture.whenStable();

    expect(scroller.scrollTop).toBe(4 * ROW);
  });
});

describe('ProgressLog download', () => {
  afterEach(() => vi.restoreAllMocks());

  it('offers the log as a file only once the run has failed', async () => {
    expect((await render(LINES)).querySelector('.save')).toBeNull();
    expect(
      (await render(LINES, false, 'Ollama is not running')).querySelector('.save'),
    ).not.toBeNull();
  });

  it('saves the lines and the error that ended the run', async () => {
    const download = interceptDownload();
    const log = await render(LINES, false, 'Ollama is not running');

    log.querySelector<HTMLButtonElement>('.save')!.click();

    const text = await download.blobs[0].text();
    expect(text).toContain('Refined search query: kettle price');
    expect(text).toContain('buy_agent.fetch');
    expect(text).toContain('FAILED: Ollama is not running');
    expect(download.saved().download).toMatch(/^buy-agent-log-\d{8}-\d{6}\.txt$/);
  });

  it('keeps the whole logger name, which the panel trims', () => {
    const written = transcript(LINES, null, new Date('2026-08-25T14:03:11Z'));
    expect(written).toContain('buy_agent run — 2026-08-25T14:03:11.000Z');
    expect(written).toContain('WARNING  buy_agent.fetch');
    expect(written).not.toContain('FAILED');
  });

  it('times every line, which is what says where a slow run went', () => {
    const written = transcript(LINES, null, new Date('2026-08-25T14:03:11Z'));
    expect(written).toContain('18:12:17 INFO');
    expect(written).toContain('18:12:20 WARNING');
  });

  it('says so rather than writing an empty file when a run failed before logging', () => {
    const written = transcript([], 'Could not reach Ollama', new Date());
    expect(written).toContain('(the run produced no log lines)');
    expect(written).toContain('FAILED: Could not reach Ollama');
  });

  it('names the file after the moment it was taken', () => {
    expect(logFilename(new Date('2026-08-25T14:03:11.500Z'))).toBe(
      'buy-agent-log-20260825-140311.txt',
    );
  });
});
