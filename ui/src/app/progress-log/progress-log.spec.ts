import { TestBed } from '@angular/core/testing';
import { afterEach, vi } from 'vitest';

import { ProgressLog, logFilename, transcript } from './progress-log';
import type { LogLine } from '../agent.types';

const LINES: LogLine[] = [
  { level: 'INFO', logger: 'buy_agent.agent', message: 'Refined search query: kettle price' },
  { level: 'WARNING', logger: 'buy_agent.fetch', message: 'Got usable page text from 8 of 10' },
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
