import { TestBed, ComponentFixture } from '@angular/core/testing';

import { SearchForm } from './search-form';
import type { AgentDefaults, SearchOptions } from '../agent.types';

const DEFAULTS: AgentDefaults = {
  model: 'llama3.2',
  base_url: 'http://localhost:11434',
  temperature: 0,
  num_ctx: null,
  think: null,
  results: 10,
  top: 3,
  region: 'us-en',
  fetch: true,
  sort_by: 'score',
  sort_options: ['score', 'price', 'rating'],
};

describe('SearchForm', () => {
  let fixture: ComponentFixture<SearchForm>;
  let submitted: SearchOptions[];

  const element = <T extends HTMLElement>(selector: string): T =>
    fixture.nativeElement.querySelector(selector) as T;

  const type = async (selector: string, value: string) => {
    const input = element<HTMLInputElement>(selector);
    input.value = value;
    input.dispatchEvent(new Event('input'));
    await fixture.whenStable();
  };

  beforeEach(async () => {
    localStorage.clear();
    submitted = [];
    fixture = TestBed.createComponent(SearchForm);
    fixture.componentRef.setInput('defaults', DEFAULTS);
    fixture.componentInstance.search.subscribe((options) => submitted.push(options));
    await fixture.whenStable();
  });

  it('starts from the agent config defaults the server served', async () => {
    expect(element<HTMLInputElement>('input[name="model"]').value).toBe('llama3.2');
    expect(element<HTMLInputElement>('input[name="results"]').value).toBe('10');
    expect(element<HTMLInputElement>('input[name="fetch"]').checked).toBe(true);
  });

  it('will not search for nothing', async () => {
    expect(element<HTMLButtonElement>('button[type="submit"]').disabled).toBe(true);
    await type('input[name="request"]', '  ');
    expect(element<HTMLButtonElement>('button[type="submit"]').disabled).toBe(true);
  });

  it('sends what was typed, trimmed, along with the settings', async () => {
    await type('input[name="request"]', '  kettle  ');
    await type('input[name="model"]', 'qwen2.5');
    element<HTMLFormElement>('form').dispatchEvent(new Event('submit'));
    await fixture.whenStable();

    expect(submitted).toHaveLength(1);
    expect(submitted[0].request).toBe('kettle');
    expect(submitted[0].model).toBe('qwen2.5');
    expect(submitted[0].sort_by).toBe('score');
    expect(submitted[0].think).toBeNull();
  });

  it('turns the thinking selector back into the tri-state the config takes', async () => {
    await type('input[name="request"]', 'kettle');
    const select = element<HTMLSelectElement>('select[name="thinking"]');
    select.value = 'off';
    select.dispatchEvent(new Event('change'));
    await fixture.whenStable();

    element<HTMLFormElement>('form').dispatchEvent(new Event('submit'));
    await fixture.whenStable();
    expect(submitted[0].think).toBe(false);
  });

  it('offers the models Ollama has pulled', async () => {
    fixture.componentRef.setInput('status', {
      base_url: 'http://localhost:11434',
      reachable: true,
      models: ['llama3.2', 'lfm2.5'],
    });
    await fixture.whenStable();
    const options = fixture.nativeElement.querySelectorAll('#installed-models option');
    expect([...options].map((option: HTMLOptionElement) => option.value)).toEqual([
      'llama3.2',
      'lfm2.5',
    ]);
  });

  it('fills the request box from an example', async () => {
    element<HTMLButtonElement>('.example').click();
    await fixture.whenStable();
    expect(element<HTMLInputElement>('input[name="request"]').value).toContain('headphones');
  });

  it('locks the form and offers Stop while a search is running', async () => {
    fixture.componentRef.setInput('running', true);
    await fixture.whenStable();
    expect(element<HTMLInputElement>('input[name="request"]').disabled).toBe(true);
    expect(element('button[type="submit"]')).toBeNull();
    expect(element<HTMLButtonElement>('.actions button').textContent).toContain('Stop');
  });

  it('keeps one field while another is edited', async () => {
    /* Seeding the form from the defaults must not re-run on every keystroke. */
    await type('input[name="region"]', 'pl-pl');
    await type('input[name="model"]', 'qwen2.5');
    expect(element<HTMLInputElement>('input[name="region"]').value).toBe('pl-pl');
  });

  it('remembers the settings but not the request', async () => {
    await type('input[name="request"]', 'kettle');
    await type('input[name="region"]', 'pl-pl');
    element<HTMLFormElement>('form').dispatchEvent(new Event('submit'));
    await fixture.whenStable();

    const next = TestBed.createComponent(SearchForm);
    next.componentRef.setInput('defaults', DEFAULTS);
    await next.whenStable();
    const fields = next.nativeElement as HTMLElement;
    expect(fields.querySelector<HTMLInputElement>('input[name="region"]')!.value).toBe('pl-pl');
    expect(fields.querySelector<HTMLInputElement>('input[name="request"]')!.value).toBe('');
  });
});
