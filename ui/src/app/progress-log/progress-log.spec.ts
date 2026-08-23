import { TestBed } from '@angular/core/testing';

import { ProgressLog } from './progress-log';
import type { LogLine } from '../agent.types';

const LINES: LogLine[] = [
  { level: 'INFO', logger: 'buy_agent.agent', message: 'Refined search query: kettle price' },
  { level: 'WARNING', logger: 'buy_agent.fetch', message: 'Got usable page text from 8 of 10' },
];

async function render(lines: LogLine[], running = false): Promise<HTMLElement> {
  const fixture = TestBed.createComponent(ProgressLog);
  fixture.componentRef.setInput('lines', lines);
  fixture.componentRef.setInput('running', running);
  await fixture.whenStable();
  return fixture.nativeElement as HTMLElement;
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
