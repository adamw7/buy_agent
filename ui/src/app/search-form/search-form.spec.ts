import { TestBed, ComponentFixture } from '@angular/core/testing';

import { SearchForm } from './search-form';
import type { AgentDefaults, ModelSource, SearchOptions } from '../agent.types';

const OLLAMA = {
  name: 'ollama',
  label: 'Ollama',
  model: 'llama3.2',
  base_url: 'http://localhost:11434',
  takes_num_ctx: true,
};

const VLLM = {
  name: 'vllm',
  label: 'vLLM',
  model: 'Qwen/Qwen3-8B',
  base_url: 'http://localhost:8000/v1',
  takes_num_ctx: false,
};

const DEFAULTS: AgentDefaults = {
  provider: 'ollama',
  provider_options: [OLLAMA, VLLM],
  model: 'llama3.2',
  base_url: 'http://localhost:11434',
  temperature: 0,
  num_ctx: null,
  think: null,
  results: 10,
  top: 3,
  region: 'us-en',
  sources: '',
  fetch: true,
  sort_by: 'score',
  sort_options: ['score', 'price', 'rating'],
};

describe('SearchForm', () => {
  let fixture: ComponentFixture<SearchForm>;
  let submitted: SearchOptions[];

  const element = <T extends HTMLElement>(selector: string): T =>
    fixture.nativeElement.querySelector(selector) as T;

  /** A second form, rendered after storage has been set up by the test. */
  const seeded = async (): Promise<HTMLElement> => {
    const next = TestBed.createComponent(SearchForm);
    next.componentRef.setInput('defaults', DEFAULTS);
    await next.whenStable();
    return next.nativeElement as HTMLElement;
  };

  const type = async (selector: string, value: string) => {
    const input = element<HTMLInputElement>(selector);
    input.value = value;
    input.dispatchEvent(new Event('input'));
    await fixture.whenStable();
  };

  const choose = async (selector: string, value: string) => {
    const select = element<HTMLSelectElement>(selector);
    select.value = value;
    select.dispatchEvent(new Event('change'));
    await fixture.whenStable();
  };

  /** Tell the form what the server reported serving, the way the page does. */
  const pulled = async (models: string[]) => {
    fixture.componentRef.setInput('status', {
      provider: 'ollama',
      label: 'Ollama',
      base_url: 'http://localhost:11434',
      reachable: true,
      models,
    });
    await fixture.whenStable();
  };

  const modelNames = (): string[] =>
    [...fixture.nativeElement.querySelectorAll('select[name="model"] option')].map(
      (option) => (option as HTMLOptionElement).value,
    );

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

  it('names the default the context field falls back to when cleared', async () => {
    const field = () => element<HTMLInputElement>('input[name="numCtx"]');
    expect(field().placeholder).toBe("Ollama's own (4096)");

    const wide = TestBed.createComponent(SearchForm);
    wide.componentRef.setInput('defaults', { ...DEFAULTS, num_ctx: 8192 });
    await wide.whenStable();

    const placeholder = (wide.nativeElement as HTMLElement).querySelector<HTMLInputElement>(
      'input[name="numCtx"]',
    )!.placeholder;
    expect(placeholder).toBe('The default (8192)');
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
    expect(submitted[0].think).toBe(false);
  });

  it('turns the thinking selector back into the boolean the config takes', async () => {
    await type('input[name="request"]', 'kettle');
    const select = element<HTMLSelectElement>('select[name="thinking"]');
    select.value = 'on';
    select.dispatchEvent(new Event('change'));
    await fixture.whenStable();

    element<HTMLFormElement>('form').dispatchEvent(new Event('submit'));
    await fixture.whenStable();
    expect(submitted[0].think).toBe(true);
  });

  it('offers only the states the server can be told about', async () => {
    const options = Array.from(
      element<HTMLSelectElement>('select[name="thinking"]').options,
      (option) => option.value,
    );

    // 'default' would send nothing, which the server reads as its own default --
    // an option that silently did what 'off' does (ADR-0019).
    expect(options).toEqual(['off', 'on']);
  });

  it('ignores a thinking mode remembered from when the form offered three', async () => {
    localStorage.setItem('buy_agent.settings', JSON.stringify({ thinking: 'default' }));

    const form = await seeded();

    expect(form.querySelector<HTMLSelectElement>('select[name="thinking"]')!.value).toBe('off');
  });

  it('offers the models the server reported, as a dropdown', async () => {
    await pulled(['llama3.2', 'lfm2.5']);

    expect(modelNames()).toEqual(['llama3.2', 'lfm2.5']);
    expect(element<HTMLSelectElement>('select[name="model"]').value).toBe('llama3.2');
  });

  it('sends the model picked from the dropdown', async () => {
    await pulled(['llama3.2', 'lfm2.5']);
    await type('input[name="request"]', 'kettle');
    await choose('select[name="model"]', 'lfm2.5');

    element<HTMLFormElement>('form').dispatchEvent(new Event('submit'));
    await fixture.whenStable();

    expect(submitted[0].model).toBe('lfm2.5');
  });

  it('keeps a chosen model the server is not serving in the list', async () => {
    /* Dropping it would silently run the search on somebody else's model. */
    await pulled(['lfm2.5']);

    expect(modelNames()).toEqual(['llama3.2', 'lfm2.5']);
    expect(element<HTMLSelectElement>('select[name="model"]').value).toBe('llama3.2');
    expect(element('select[name="model"] option')!.textContent).toContain('not served');
  });

  it('falls back to typing a name when the server listed nothing', async () => {
    /* A dropdown holding one unusable entry is worse than a text box. */
    fixture.componentRef.setInput('status', {
      provider: 'ollama',
      label: 'Ollama',
      base_url: 'http://localhost:11434',
      reachable: false,
      models: [],
    });
    await fixture.whenStable();

    expect(element('select[name="model"]')).toBeNull();
    expect(element<HTMLInputElement>('input[name="model"]').value).toBe('llama3.2');
    expect(element('.field small')!.textContent).toContain('Ollama listed nothing');
  });

  it('asks for the model list of the server that was typed in', async () => {
    const asked: ModelSource[] = [];
    fixture.componentInstance.refresh.subscribe((source) => asked.push(source));

    const server = element<HTMLInputElement>('input[name="baseUrl"]');
    server.value = ' http://10.0.0.5:11434 ';
    server.dispatchEvent(new Event('input'));
    server.dispatchEvent(new Event('change'));
    await fixture.whenStable();

    expect(asked).toEqual([{ provider: 'ollama', base_url: 'http://10.0.0.5:11434' }]);
  });

  it('offers every provider the server named', async () => {
    const names = Array.from(
      element<HTMLSelectElement>('select[name="provider"]').options,
      (option) => option.value,
    );

    expect(names).toEqual(['ollama', 'vllm']);
  });

  it('brings the model and the address along when the provider changes', async () => {
    /* Left alone, the two would still hold an Ollama tag on Ollama's port, asked
       of a vLLM -- a run that fails for a reason nothing on the form explains. */
    await choose('select[name="provider"]', 'vllm');

    expect(element<HTMLInputElement>('input[name="model"]').value).toBe(VLLM.model);
    expect(element<HTMLInputElement>('input[name="baseUrl"]').value).toBe(VLLM.base_url);
  });

  it('asks the newly chosen provider what it is serving', async () => {
    const asked: ModelSource[] = [];
    fixture.componentInstance.refresh.subscribe((source) => asked.push(source));

    await choose('select[name="provider"]', 'vllm');

    expect(asked).toEqual([{ provider: 'vllm', base_url: VLLM.base_url }]);
  });

  it('sends the provider along with the request', async () => {
    await choose('select[name="provider"]', 'vllm');
    await type('input[name="request"]', 'kettle');
    element<HTMLFormElement>('form').dispatchEvent(new Event('submit'));
    await fixture.whenStable();

    expect(submitted[0].provider).toBe('vllm');
    expect(submitted[0].model).toBe(VLLM.model);
  });

  it('names the server the address field belongs to', async () => {
    const label = () => element<HTMLInputElement>('input[name="baseUrl"]').closest('label')!;
    expect(label().querySelector('span')!.textContent).toContain('Ollama address');

    await choose('select[name="provider"]', 'vllm');

    expect(label().querySelector('span')!.textContent).toContain('vLLM address');
  });

  it('closes the context field for a provider that does not take one', async () => {
    /* vLLM fixes its window with --max-model-len when it starts, so a box to type
       one into would be a setting that quietly does nothing. */
    const field = () => element<HTMLInputElement>('input[name="numCtx"]');
    expect(field().disabled).toBe(false);

    await choose('select[name="provider"]', 'vllm');

    expect(field().disabled).toBe(true);
    expect(field().placeholder).toBe('Fixed when vLLM starts');
  });

  it('remembers the provider with the pair that belongs to it', async () => {
    /* Saved together, so a restore can never put one provider's model against
       the other one's address. */
    await choose('select[name="provider"]', 'vllm');
    await type('input[name="request"]', 'kettle');
    element<HTMLFormElement>('form').dispatchEvent(new Event('submit'));
    await fixture.whenStable();

    const form = await seeded();

    expect(form.querySelector<HTMLSelectElement>('select[name="provider"]')!.value).toBe('vllm');
    expect(form.querySelector<HTMLInputElement>('input[name="model"]')!.value).toBe(VLLM.model);
    expect(form.querySelector<HTMLInputElement>('input[name="baseUrl"]')!.value).toBe(
      VLLM.base_url,
    );
  });

  it('does not go asking a server with no address', async () => {
    const asked: ModelSource[] = [];
    fixture.componentInstance.refresh.subscribe((source) => asked.push(source));

    const server = element<HTMLInputElement>('input[name="baseUrl"]');
    server.value = '   ';
    server.dispatchEvent(new Event('input'));
    server.dispatchEvent(new Event('change'));
    await fixture.whenStable();

    expect(asked).toEqual([]);
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

  it('falls back to the served defaults when what was remembered is unreadable', async () => {
    /* Storage is shared with whatever else the browser kept; it can be anything. */
    localStorage.setItem('buy_agent.settings', '{ not json at all');

    const form = await seeded();

    expect(form.querySelector<HTMLInputElement>('input[name="region"]')!.value).toBe('us-en');
    expect(form.querySelector<HTMLInputElement>('input[name="model"]')!.value).toBe('llama3.2');
  });

  it('ignores remembered settings that are not settings', async () => {
    localStorage.setItem('buy_agent.settings', '42');

    const form = await seeded();

    expect(form.querySelector<HTMLInputElement>('input[name="region"]')!.value).toBe('us-en');
  });

  it('ignores a remembered value of the wrong type, field by field', async () => {
    /* Storage outlives any version of this form, so a key can hold what a much
       older one wrote there. A value the field cannot take leaves the served
       default standing, rather than putting a number in a text box. */
    localStorage.setItem(
      'buy_agent.settings',
      JSON.stringify({ model: 42, results: 'ten', fetchPages: 'yes', region: 'pl-pl' }),
    );

    const form = await seeded();

    expect(form.querySelector<HTMLInputElement>('input[name="model"]')!.value).toBe('llama3.2');
    expect(form.querySelector<HTMLInputElement>('input[name="results"]')!.value).toBe('10');
    expect(form.querySelector<HTMLInputElement>('input[name="fetch"]')!.checked).toBe(true);
    expect(form.querySelector<HTMLInputElement>('input[name="region"]')!.value).toBe('pl-pl');
  });

  it('keeps a served context window that nothing was remembered against', async () => {
    /* `null` is a real remembered value for this field -- "use whatever the
       server defaults to" -- so it cannot fall back the way the others do. That
       must not turn an absent key into a remembered null: a settings blob
       written before the field existed has to leave the served default standing,
       which since gemma4 (ADR-0019) is a window the run actually needs. */
    const withCtx: AgentDefaults = { ...DEFAULTS, num_ctx: 8192 };
    const render = async (): Promise<HTMLElement> => {
      const next = TestBed.createComponent(SearchForm);
      next.componentRef.setInput('defaults', withCtx);
      await next.whenStable();
      return next.nativeElement as HTMLElement;
    };
    const numCtx = (form: HTMLElement) =>
      form.querySelector<HTMLInputElement>('input[name="numCtx"]')!.value;

    // Nothing remembered at all: every first visit.
    expect(numCtx(await render())).toBe('8192');

    // Remembered, but from before the field was one: the rest still restores.
    localStorage.setItem('buy_agent.settings', JSON.stringify({ region: 'pl-pl' }));
    const form = await render();

    expect(numCtx(form)).toBe('8192');
    expect(form.querySelector<HTMLInputElement>('input[name="region"]')!.value).toBe('pl-pl');
  });

  it('remembers a context window that was deliberately cleared', async () => {
    /* ...and the other half of the same rule: a stored null wins over the
       served default, because clearing the box is a choice -- the one the
       placeholder spells out. */
    const withCtx: AgentDefaults = { ...DEFAULTS, num_ctx: 8192 };
    localStorage.setItem('buy_agent.settings', JSON.stringify({ numCtx: null }));

    const next = TestBed.createComponent(SearchForm);
    next.componentRef.setInput('defaults', withCtx);
    await next.whenStable();
    const form = next.nativeElement as HTMLElement;

    expect(form.querySelector<HTMLInputElement>('input[name="numCtx"]')!.value).toBe('');
  });

  it('still searches in a browser that refuses to store anything', async () => {
    const setItem = Storage.prototype.setItem;
    Storage.prototype.setItem = () => {
      throw new Error('storage is disabled');
    };

    try {
      await type('input[name="request"]', 'kettle');
      element<HTMLFormElement>('form').dispatchEvent(new Event('submit'));
      await fixture.whenStable();
    } finally {
      Storage.prototype.setItem = setItem;
    }

    expect(submitted).toHaveLength(1);
    expect(submitted[0].request).toBe('kettle');
  });

  it('will not start a second run on top of the one already going', async () => {
    await type('input[name="request"]', 'kettle');
    fixture.componentRef.setInput('running', true);
    await fixture.whenStable();

    element<HTMLFormElement>('form').dispatchEvent(new Event('submit'));
    await fixture.whenStable();

    expect(submitted).toHaveLength(0);
  });

  it('keeps the settings panel open once it has been opened', async () => {
    const settings = element<HTMLDetailsElement>('details.advanced');
    expect(settings.open).toBe(false);

    settings.open = true;
    settings.dispatchEvent(new Event('toggle'));
    await fixture.whenStable();

    expect(element<HTMLDetailsElement>('details.advanced').open).toBe(true);
  });

  it('locks the settings too while a search is running', async () => {
    fixture.componentRef.setInput('running', true);
    await fixture.whenStable();

    expect(element<HTMLInputElement>('input[name="model"]').disabled).toBe(true);
    expect(element<HTMLInputElement>('input[name="region"]').disabled).toBe(true);
    expect(element<HTMLButtonElement>('.pill.example').disabled).toBe(true);
  });

  it('leaves the trusted sources empty, which is the whole web', async () => {
    expect(element<HTMLInputElement>('input[name="sources"]').value).toBe('');

    await type('input[name="request"]', 'kettle');
    element<HTMLFormElement>('form').dispatchEvent(new Event('submit'));
    await fixture.whenStable();

    expect(submitted[0].sources).toBe('');
  });

  it('sends the trusted sources as typed, for Python to make sense of', async () => {
    await type('input[name="request"]', 'kettle');
    await type('input[name="sources"]', '  rtings.com @mkbhd  ');
    element<HTMLFormElement>('form').dispatchEvent(new Event('submit'));
    await fixture.whenStable();

    expect(submitted[0].sources).toBe('rtings.com @mkbhd');
  });

  it('remembers the trusted sources, which are a standing answer', async () => {
    await type('input[name="request"]', 'kettle');
    await type('input[name="sources"]', 'rtings.com');
    element<HTMLFormElement>('form').dispatchEvent(new Event('submit'));
    await fixture.whenStable();

    const fields = await seeded();
    expect(fields.querySelector<HTMLInputElement>('input[name="sources"]')!.value).toBe(
      'rtings.com',
    );
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
