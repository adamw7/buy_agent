'use strict';

/*
 * The benchmark page. Python ranks, scores and words everything (ADR-0012); this draws
 * what /api/state answers and sends what the form says. Every string reaches the page
 * through textContent, never as markup: model names and quotes are model output.
 */

const POLL_MS = 1000;

const page = { config: null, state: null, timer: 0, chosen: null, listing: 0 };

const byId = (id) => document.getElementById(id);

function element(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined && options.text !== null) node.textContent = String(options.text);
  for (const [name, value] of Object.entries(options.attrs || {})) node.setAttribute(name, value);
  for (const child of children) if (child) node.append(child);
  return node;
}

async function ask(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { Accept: 'application/json', ...(options.headers || {}) },
  });
  let body = {};
  try {
    body = await response.json();
  } catch {
    body = {};
  }
  if (!response.ok) {
    const failure = new Error(body.error || `${response.status} ${response.statusText}`);
    failure.field = body.field || null;
    throw failure;
  }
  return body;
}

function send(url, data = {}) {
  return ask(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
}

// -- the form ------------------------------------------------------------------

function providerRow() {
  const name = byId('provider').value;
  return page.config.provider_options.find((option) => option.name === name);
}

function choice(name, value, title, details, { checked = false, disabled = false } = {}) {
  const box = element('input', { attrs: { type: 'checkbox', name, value } });
  box.checked = checked;
  box.disabled = disabled;
  return element('label', { className: disabled ? 'choice unavailable' : 'choice' }, [
    box,
    element('span', {}, [
      element('strong', { text: title }),
      ...details.filter(Boolean).map((detail) => element('small', { text: detail })),
    ]),
  ]);
}

function checked(name) {
  return [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((box) => box.value);
}

function drawProviders() {
  const select = byId('provider');
  select.replaceChildren(
    ...page.config.provider_options.map((option) =>
      element('option', { text: option.label, attrs: { value: option.name } }),
    ),
  );
  select.value = page.config.provider;
  byId('base-url').value = providerRow().base_url;
}

function drawScripts() {
  byId('scripts').replaceChildren(
    ...page.config.scripts.map((script) =>
      choice('scripts', script.name, script.name, [script.means]),
    ),
  );
}

function drawCases() {
  byId('cases').replaceChildren(
    ...page.config.cases.map((entry) =>
      choice('cases', entry.name, entry.title, [`“${entry.request}”`, entry.asks], {
        checked: true,
      }),
    ),
  );
}

function drawMetrics() {
  byId('metrics').replaceChildren(
    ...page.config.metrics.map((metric) =>
      element('tr', {}, [
        element('td', { text: metric.name }),
        element('td', { className: 'number', text: metric.weight }),
        element('td', { className: 'number', text: metric.floor }),
        element('td', { text: metric.means }),
      ]),
    ),
  );
}

async function listModels() {
  const ticket = ++page.listing;
  const note = byId('models-note');
  const models = byId('models');
  const query = new URLSearchParams({
    provider: byId('provider').value,
    base_url: byId('base-url').value.trim(),
  });
  note.textContent = 'Asking the server what it holds…';
  models.replaceChildren();
  let answer;
  try {
    answer = await ask(`/api/models?${query}`);
  } catch (failure) {
    if (ticket === page.listing) note.textContent = failure.message;
    return;
  }
  // A later listing (another server, another address) has replaced this one.
  if (ticket !== page.listing) return;
  if (!answer.reachable) {
    note.textContent = answer.hint || answer.detail || '';
    return;
  }
  note.textContent = answer.models.length ? '' : `${answer.label} at ${answer.base_url}: none`;
  const usual = providerRow().model;
  models.replaceChildren(
    ...answer.models.map((model) =>
      choice(
        'models',
        model.name,
        model.name,
        [model.completion ? '' : 'Embedding only: it cannot answer a prompt'],
        {
          checked: model.completion && model.name === usual,
          disabled: !model.completion,
        },
      ),
    ),
  );
}

function clearError() {
  byId('plan-error').textContent = '';
  for (const marked of document.querySelectorAll('[aria-invalid="true"]')) {
    marked.removeAttribute('aria-invalid');
  }
}

function showError(message, field = null) {
  const target =
    field === 'base_url' ? byId('base-url') : field === 'provider' ? byId('provider') : null;
  byId('plan-error').textContent = message;
  if (target) {
    target.setAttribute('aria-invalid', 'true');
    target.focus();
  }
}

async function run(event) {
  event.preventDefault();
  clearError();
  byId('run').disabled = true;
  try {
    draw(
      await send('/api/run', {
        provider: byId('provider').value,
        base_url: byId('base-url').value.trim(),
        models: checked('models'),
        scripts: checked('scripts'),
        cases: checked('cases'),
      }),
    );
    poll();
  } catch (failure) {
    byId('run').disabled = false;
    showError(failure.message, failure.field);
  }
}

async function act(url, confirmation = '') {
  if (confirmation && !window.confirm(confirmation)) return;
  try {
    draw(await send(url));
    poll();
  } catch (failure) {
    showError(failure.message, failure.field);
  }
}

// -- what /api/state says ------------------------------------------------------

function poll() {
  window.clearTimeout(page.timer);
  page.timer = window.setTimeout(refresh, POLL_MS);
}

async function refresh() {
  try {
    draw(await ask('/api/state'));
  } catch (failure) {
    byId('status').textContent = failure.message;
  }
  if (page.state && page.state.running) poll();
}

function draw(state) {
  page.state = state;
  byId('run').disabled = state.running;
  byId('stop').disabled = !state.running || state.stopping;
  byId('clear').disabled = state.running || !state.standings.length;

  byId('progress-card').hidden = !state.status;
  byId('status').textContent = state.status;
  const bar = byId('progress');
  bar.max = Math.max(state.total, 1);
  bar.value = state.done;
  byId('problems').replaceChildren(
    ...state.problems.map((problem) => element('li', { text: problem })),
  );
  byId('board').textContent = `Kept at ${state.board}`;
  drawStandings(state);
}

function heading(text, className = '', title = '') {
  return element('th', {
    className,
    text,
    attrs: title ? { scope: 'col', title } : { scope: 'col' },
  });
}

function drawStandings(state) {
  const rows = state.standings;
  byId('standings').hidden = !rows.length;
  byId('empty').hidden = rows.length > 0;
  byId('standings-head').replaceChildren(
    heading('#', 'number'),
    heading('Model', 'model'),
    heading('Score', 'number'),
    ...state.cases.map((entry) => heading(entry.name, 'number', entry.title)),
    heading('Query', 'number'),
    heading('Time per case', 'number'),
    heading('Cases run', 'number'),
  );
  byId('standings-body').replaceChildren(...rows.map(standingRow));

  const chosen = rows.find((row) => row.key === page.chosen);
  if (chosen) {
    drawDetails(chosen);
  } else {
    byId('details').hidden = true;
  }
}

function standingRow(row) {
  const name = element('button', {
    className: 'link',
    text: row.label,
    attrs: {
      type: 'button',
      'aria-controls': 'details',
      'aria-expanded': String(row.key === page.chosen),
    },
  });
  name.addEventListener('click', () => choose(row.key));
  const classes = [row.reference ? 'reference' : '', row.key === page.chosen ? 'chosen' : ''];
  return element('tr', { className: classes.join(' ').trim() }, [
    element('td', { className: 'number', text: row.rank }),
    element('td', { className: 'model' }, [
      name,
      row.reference ? element('span', { className: 'tag', text: 'reference' }) : null,
      element('span', { className: 'where', text: row.where }),
    ]),
    element('td', { className: 'number score', text: row.score_label }),
    ...row.cells.map((cell) =>
      element('td', { className: `number ${cell.state}`, text: cell.label }),
    ),
    element('td', { className: 'number', text: row.query_label }),
    element('td', { className: 'number', text: row.seconds_label }),
    element('td', { className: 'number', text: row.ran_label }),
  ]);
}

function choose(key) {
  page.chosen = page.chosen === key ? null : key;
  drawStandings(page.state);
  if (page.chosen) byId('details-heading').focus();
}

function drawDetails(row) {
  byId('details').hidden = false;
  byId('details-heading').textContent = row.label;
  byId('details-where').textContent = row.where;
  byId('details-runs').replaceChildren(...row.runs.map(runBlock));
}

function runBlock(run) {
  const parts = [
    element('h3', { text: `${run.title}: ${run.score_label}` }),
    element('p', { className: 'note', text: `“${run.request}”` }),
  ];
  if (run.failure) parts.push(element('p', { className: 'failed', text: run.failure }));
  if (run.summary) parts.push(element('p', { text: run.summary }));
  parts.push(element('div', { className: 'run-grid' }, [metricsBlock(run), answersBlock(run)]));
  return element('article', { className: 'run' }, parts);
}

function metricsBlock(run) {
  if (!run.metrics.length) return element('div');
  return element('div', {}, [
    element('h4', { text: 'Scorecard' }),
    element('table', {}, [
      element('thead', {}, [
        element('tr', {}, [
          heading('Metric'),
          heading('Share', 'number'),
          heading('Counted', 'number'),
          heading('Floor', 'number'),
        ]),
      ]),
      element(
        'tbody',
        {},
        run.metrics.map((metric) =>
          element('tr', {}, [
            element('td', { text: metric.name, attrs: { title: metric.means } }),
            element('td', {
              className: metric.under ? 'number under' : 'number',
              text: metric.under ? `${metric.label} (under)` : metric.label,
            }),
            element('td', { className: 'number', text: metric.counts }),
            element('td', { className: 'number', text: metric.floor }),
          ]),
        ),
      ),
    ]),
  ]);
}

function answersBlock(run) {
  const query = run.query.text
    ? element('p', { className: 'query', text: run.query.text })
    : element('p', { className: 'failed', text: 'No query: the run searched with the request.' });
  return element('div', {}, [
    element('h4', { text: `Query: ${run.query.label}` }),
    query,
    element(
      'ul',
      { className: 'checks' },
      run.query.checks.map((check) =>
        element('li', { className: check.passed ? 'passed' : 'missed' }, [
          element('span', {
            className: 'mark',
            text: check.passed ? '✓' : '✗',
            attrs: { 'aria-hidden': 'true' },
          }),
          element('span', { text: check.check }),
        ]),
      ),
    ),
    element('h4', { text: 'Model time' }),
    element('p', {
      text: run.steps_label ? `${run.seconds_label} (${run.steps_label})` : run.seconds_label,
    }),
    run.products.length ? element('h4', { text: 'Reported' }) : null,
    run.products.length
      ? element(
          'ol',
          { className: 'products' },
          run.products.map((product) =>
            element('li', {}, [
              element('span', { text: `${product.rank}. ${product.line}` }),
              product.verdict === 'real'
                ? null
                : element('span', { className: 'tag', text: product.verdict }),
            ]),
          ),
        )
      : null,
    element('p', { className: 'note small', text: `Ran ${run.finished_label}` }),
  ]);
}

// -- wiring --------------------------------------------------------------------

async function start() {
  try {
    page.config = await ask('/api/config');
  } catch (failure) {
    showError(`Could not load the page: ${failure.message}`);
    return;
  }
  drawProviders();
  drawScripts();
  drawCases();
  drawMetrics();

  byId('plan').addEventListener('submit', run);
  byId('provider').addEventListener('change', () => {
    byId('base-url').value = providerRow().base_url;
    listModels();
  });
  byId('base-url').addEventListener('change', listModels);
  byId('list-models').addEventListener('click', listModels);
  byId('stop').addEventListener('click', () => act('/api/stop'));
  byId('clear').addEventListener('click', () => act('/api/clear', 'Forget every kept run?'));

  await Promise.all([listModels(), refresh()]);
}

start();
