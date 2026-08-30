import { TestBed } from '@angular/core/testing';
import { afterEach, vi } from 'vitest';

import { ProgressLog, logFilename, transcript } from './progress-log';
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
