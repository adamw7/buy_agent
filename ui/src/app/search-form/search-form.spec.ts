import { TestBed, ComponentFixture } from '@angular/core/testing';

import { SearchForm } from './search-form';
import type { AgentDefaults, InstalledModel, ModelSource, SearchOptions } from '../agent.types';

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
  max_price: null,
  min_rating: null,
  min_reviews: null,
  cache_ttl: 86400,
  region: 'us-en',
  sources: '',
  fetch: true,
  sort_by: 'score',
  sort_options: ['score', 'price', 'rating'],
  limits: {
    results: { min: 1, max: 50 },
    top: { min: 1, max: 50 },
    temperature: { min: 0, max: 2 },
    num_ctx: { min: 1, max: 1_000_000 },
    max_price: { min: 1, max: 10_000_000 },
    min_rating: { min: 0, max: 5 },
    min_reviews: { min: 0, max: 10_000_000 },
    cache_ttl: { min: 0, max: 2_592_000 },
  },
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

  /** Tell the form what the server reported serving, the way the page does.
   *  A name alone is a model that can answer a prompt; the ones that cannot are
   *  given as objects, since that is the half the picker has to mark. */
  const pulled = async (models: (string | InstalledModel)[]) => {
    fixture.componentRef.setInput('status', {
      provider: 'ollama',
      label: 'Ollama',
      base_url: 'http://localhost:11434',
      reachable: true,
      models: models.map((model) =>
        typeof model === 'string' ? { name: model, completion: true } : model,
      ),
    });
    await fixture.whenStable();
  };

  const modelNames = (): string[] =>
    [...fixture.nativeElement.querySelectorAll('select[name="model"] option')].map(
      (option) => (option as HTMLOptionElement).value,
    );

  /** What each entry reads as, which is where anything wrong with it is said. */
  const modelLabels = (): string[] =>
    [...fixture.nativeElement.querySelectorAll('select[name="model"] option')].map(
      (option) => (option as HTMLOptionElement).textContent ?? '',
    );

  /** The submit button, which is where a field the server would refuse shows up. */
  const submit = () => element<HTMLButtonElement>('button[type="submit"]');

  /** What is said under the field with this name, if anything is. */
  const problem = (name: string): string =>
    element(`input[name="${name}"]`).closest('label')!.querySelector('.problem')?.textContent ?? '';

  /** Tell the form what the server made of the sources it was asked about. */
  const checked = async (sources: string, error: string) => {
    fixture.componentRef.setInput('checked', { sources, error });
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

  it('names the default the context field falls back to when cleared', async () => {
    const field = () => element<HTMLInputElement>('input[name="num_ctx"]');
    expect(field().placeholder).toBe("Ollama's own (4096)");

    const wide = TestBed.createComponent(SearchForm);
    wide.componentRef.setInput('defaults', { ...DEFAULTS, num_ctx: 8192 });
    await wide.whenStable();

    const placeholder = (wide.nativeElement as HTMLElement).querySelector<HTMLInputElement>(
      'input[name="num_ctx"]',
    )!.placeholder;
    expect(placeholder).toBe('The default (8192)');
  });

  it('sends the shopper bounds it was given, and null for the ones it was not', async () => {
    /* Null is a value here: "no bound" is what a shopper who set none asked for,
       and what the server reads an empty field as (ADR-0039). */
    await type('input[name="request"]', 'headphones');
    await type('input[name="max_price"]', '200');
    await type('input[name="min_rating"]', '4.5');
    element<HTMLFormElement>('form').dispatchEvent(new Event('submit'));
    await fixture.whenStable();

    expect(submitted[0].max_price).toBe(200);
    expect(submitted[0].min_rating).toBe(4.5);
    expect(submitted[0].min_reviews).toBeNull();
  });

  it('sends how long pages may be cached for', async () => {
    await type('input[name="request"]', 'headphones');
    await type('input[name="cache_ttl"]', '0');
    element<HTMLFormElement>('form').dispatchEvent(new Event('submit'));
    await fixture.whenStable();

    expect(submitted[0].cache_ttl).toBe(0);
  });

  it('holds each bound to the range the server shipped for it', async () => {
    /* Every one of them, not the first: each is a field of its own, and a mark
       that lands on none of them is a greyed-out button with no visible reason. */
    await type('input[name="request"]', 'headphones');
    await type('input[name="min_rating"]', '9');
    await type('input[name="max_price"]', '0');
    await type('input[name="min_reviews"]', '-1');
    await type('input[name="cache_ttl"]', '99999999');

    expect(problem('min_rating')).toContain('Between 0 and 5');
    expect(problem('max_price')).toContain('Between 1 and 10000000');
    expect(problem('min_reviews')).toContain('Between 0 and 10000000');
    expect(problem('cache_ttl')).toContain('Between 0 and 2592000');
    expect(submit().disabled).toBe(true);
  });

  it('says a cleared bound is no bound rather than naming a number', async () => {
    /* Every other cleared box falls back to a number the server named; these
       fall back to no bound at all, and a grey "10" would say otherwise. */
    expect(element<HTMLInputElement>('input[name="max_price"]').placeholder).toBe('No limit');
    expect(element<HTMLInputElement>('input[name="min_reviews"]').placeholder).toBe('No limit');
    expect(element<HTMLInputElement>('input[name="cache_ttl"]').placeholder).toBe('86400');
  });

  it('remembers the bounds a shopper set, the way it remembers the rest', async () => {
    /* A budget is a standing answer -- shopped under for weeks -- not something
       retyped per search. */
    await type('input[name="request"]', 'headphones');
    await type('input[name="max_price"]', '200');
    element<HTMLFormElement>('form').dispatchEvent(new Event('submit'));
    await fixture.whenStable();

    const next = await seeded();

    expect(next.querySelector<HTMLInputElement>('input[name="max_price"]')!.value).toBe('200');
  });

  it('will not search for nothing', async () => {
    expect(element<HTMLButtonElement>('button[type="submit"]').disabled).toBe(true);
    await type('input[name="request"]', '  ');
    expect(element<HTMLButtonElement>('button[type="submit"]').disabled).toBe(true);
  });

  it('holds a number to the range the server shipped, before a run starts', async () => {
    /* The server refuses 51 products; finding that out costs a stream, a
       progress panel and an error banner, and the range was on the page all
       along (ADR-0033). */
    await type('input[name="request"]', 'kettle');
    await type('input[name="results"]', '51');

    expect(problem('results')).toContain('Between 1 and 50');
    expect(submit().disabled).toBe(true);

    element<HTMLFormElement>('form').dispatchEvent(new Event('submit'));
    await fixture.whenStable();
    expect(submitted).toHaveLength(0);
  });

  it('marks every number field out of range, not only the first', async () => {
    await type('input[name="request"]', 'kettle');
    await type('input[name="top"]', '0');
    await type('input[name="temperature"]', '3');
    await type('input[name="num_ctx"]', '0');

    expect(problem('top')).toContain('Between 1 and 50');
    expect(problem('temperature')).toContain('Between 0 and 2');
    expect(problem('num_ctx')).toContain('Between 1 and 1000000');
    expect(submit().disabled).toBe(true);
  });

  it('takes the bounds from the server rather than from the markup', async () => {
    /* A range written into the template is a second copy of `config.LIMITS`, and
       the one nobody would notice going stale. */
    const bounds = (name: string) => {
      const field = element<HTMLInputElement>(`input[name="${name}"]`);
      return [field.getAttribute('min'), field.getAttribute('max')];
    };

    expect(bounds('results')).toEqual(['1', '50']);
    expect(bounds('temperature')).toEqual(['0', '2']);
    expect(bounds('num_ctx')).toEqual(['1', '1000000']);
  });

  it('lets a cleared number field mean the default, not a number out of range', async () => {
    /* A blank field is "unset" (ADR-0012), which is how the context window asks
       for the server's own -- and is nothing to hold to a range. */
    await type('input[name="request"]', 'kettle');
    await type('input[name="num_ctx"]', '');

    expect(problem('num_ctx')).toBe('');
    expect(submit().disabled).toBe(false);
  });

  it('asks the server what the sources field holds, when it is left', async () => {
    const asked: string[] = [];
    fixture.componentInstance.check.subscribe((spec) => asked.push(spec));

    const field = element<HTMLInputElement>('input[name="sources"]');
    field.value = '  Marques Brownlee  ';
    field.dispatchEvent(new Event('input'));
    field.dispatchEvent(new Event('change'));
    await fixture.whenStable();

    expect(asked).toEqual(['Marques Brownlee']);
  });

  it('asks about a remembered source too, which nobody is about to type', async () => {
    /* Left unasked, a browser holding a bad one finds out a run later. */
    localStorage.setItem('buy_agent.settings', JSON.stringify({ sources: 'Marques Brownlee' }));
    const next = TestBed.createComponent(SearchForm);
    const asked: string[] = [];
    next.componentInstance.check.subscribe((spec) => asked.push(spec));
    next.componentRef.setInput('defaults', DEFAULTS);
    await next.whenStable();

    expect(asked).toEqual(['Marques Brownlee']);
  });

  it('refuses a source the server said names no site', async () => {
    await type('input[name="request"]', 'kettle');
    await type('input[name="sources"]', 'Marques Brownlee');
    await checked('Marques Brownlee', "'Marques' does not name a source.");

    expect(problem('sources')).toContain('does not name a source');
    expect(submit().disabled).toBe(true);
  });

  it('drops an answer about text the field no longer holds', async () => {
    /* The answer arrives after the request, and the shopper has kept typing. */
    await type('input[name="request"]', 'kettle');
    await type('input[name="sources"]', 'rtings.com');
    await checked('Marques', "'Marques' does not name a source.");

    expect(problem('sources')).toBe('');
    expect(submit().disabled).toBe(false);
  });

  it('marks the field a refused run named, without deciding it itself', async () => {
    /* The region has no rule the page could apply -- ADR-0031 keeps that shape
       in Python -- so the mark is the server's own sentence, and it does not
       gate the button: what is in the box now may well be right. */
    await type('input[name="request"]', 'kettle');
    fixture.componentRef.setInput('rejected', {
      field: 'region',
      message: "'en-US' is not a search region.",
    });
    await fixture.whenStable();

    expect(problem('region')).toContain('not a search region');
    expect(element('input[name="region"]').classList).toContain('invalid');
    expect(submit().disabled).toBe(false);
  });

  it('says what the field holds now rather than what a run was refused for', async () => {
    await type('input[name="request"]', 'kettle');
    await type('input[name="results"]', '51');
    fixture.componentRef.setInput('rejected', {
      field: 'results',
      message: 'results must be between 1 and 50; got 99.',
    });
    await fixture.whenStable();

    expect(problem('results')).toContain('Between 1 and 50');
    expect(problem('results')).not.toContain('99');
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

  it('ignores a rank criterion the server no longer offers', async () => {
    /* The same rule the thinking mode gets, on the field that most needed it:
       `sort_options` is the server's list and it is free to change, while
       storage outlives every version of it. Restored unchecked, the Rank by
       select matched no option and showed nothing, and the run was refused by
       Python for a value nobody could see on the page. */
    localStorage.setItem('buy_agent.settings', JSON.stringify({ sortBy: 'cheapness' }));

    const form = await seeded();

    expect(form.querySelector<HTMLSelectElement>('select[name="sortBy"]')!.value).toBe('score');
  });

  it('keeps a rank criterion the server does still offer', async () => {
    localStorage.setItem('buy_agent.settings', JSON.stringify({ sortBy: 'price' }));

    const form = await seeded();

    expect(form.querySelector<HTMLSelectElement>('select[name="sortBy"]')!.value).toBe('price');
  });

  it('ignores a provider the server no longer offers, and its pair with it', async () => {
    /* A provider dropped from the table -- or a name this build never had --
       leaves the picker matching nothing, which takes the model and address
       fields with it: they describe a server that is not there, and the context
       field goes back to guessing because `takes_num_ctx` has no row to read. */
    localStorage.setItem(
      'buy_agent.settings',
      JSON.stringify({ provider: 'llamacpp', model: 'ggml', baseUrl: 'http://localhost:9999' }),
    );

    const form = await seeded();

    expect(form.querySelector<HTMLSelectElement>('select[name="provider"]')!.value).toBe('ollama');
  });

  it('keeps a provider the server does still offer', async () => {
    localStorage.setItem('buy_agent.settings', JSON.stringify({ provider: 'vllm' }));

    const form = await seeded();

    expect(form.querySelector<HTMLSelectElement>('select[name="provider"]')!.value).toBe('vllm');
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

  it('marks a model that cannot answer a prompt rather than hiding it', async () => {
    /* An embedding model is pulled the same way a chat one is and lists the
       same. Dropped, a pull made on purpose would silently vanish; offered
       unmarked, it is a run that fails a minute in. */
    await pulled(['llama3.2', { name: 'nomic-embed-text', completion: false }]);

    expect(modelNames()).toEqual(['llama3.2', 'nomic-embed-text']);
    expect(modelLabels()[1]).toContain('embedding only');
    expect(modelLabels()[0]).not.toContain('embedding only');
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
    const field = () => element<HTMLInputElement>('input[name="num_ctx"]');
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
      form.querySelector<HTMLInputElement>('input[name="num_ctx"]')!.value;

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

    expect(form.querySelector<HTMLInputElement>('input[name="num_ctx"]')!.value).toBe('');
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

  it('opens the settings on a setting it has marked', async () => {
    /* Every mark this form makes is on a field inside that panel, and the panel
       is shut until somebody opens it -- so the whole of what is on screen is a
       Find products button that will not press. */
    expect(element<HTMLDetailsElement>('details.advanced').open).toBe(false);

    await type('input[name="request"]', 'kettle');
    await type('input[name="results"]', '51');

    expect(element<HTMLDetailsElement>('details.advanced').open).toBe(true);
    expect(element('summary .flagged').textContent).toContain('1 setting to look at');
    expect(submit().disabled).toBe(true);

    await type('input[name="top"]', '0');
    expect(element('summary .flagged').textContent).toContain('2 settings to look at');
  });

  it('opens them for a remembered value nobody is about to retype', async () => {
    /* The one that arrives without a keystroke: a value stored by an older
       build, or by a server whose ranges have since moved, is restored and
       marked before the form is first drawn. */
    localStorage.setItem('buy_agent.settings', JSON.stringify({ results: 99 }));
    const form = await seeded();

    expect(form.querySelector<HTMLDetailsElement>('details.advanced')!.open).toBe(true);
    expect(form.querySelector('summary .flagged')!.textContent).toContain('1 setting');
  });

  it('opens them for the field a refused run named', async () => {
    /* Marking the box a refusal came out of is the whole of what ADR-0033 asks
       the form to do with one, and a mark inside a closed panel is no mark. */
    fixture.componentRef.setInput('rejected', {
      field: 'region',
      message: "'en-us' is not a search region.",
    });
    await fixture.whenStable();

    expect(element<HTMLDetailsElement>('details.advanced').open).toBe(true);
    expect(problem('region')).toContain('not a search region');
  });

  it('names the default each cleared number box falls back to', async () => {
    /* A cleared box means "use the default" (ADR-0012) -- an answer, and not a
       mistake -- but an empty box says nothing about which number that is. */
    const placeholder = (name: string) =>
      element<HTMLInputElement>(`input[name="${name}"]`).placeholder;

    expect(placeholder('results')).toBe('10');
    expect(placeholder('top')).toBe('3');
    expect(placeholder('temperature')).toBe('0');
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
