/**
 * Film the benchmark's page comparing models on a CPU, narrated, as MPEG.
 *
 *   python -m benchmark.server                                   # in one terminal
 *   node demo/benchmark.mjs --models qwen3:0.6b --out demo/benchmark-on-cpu.mpg
 *
 * The models are real and served by Ollama; nothing is scripted but the two
 * reference answers the page offers anyway. What the recording claims about the
 * processor it checks: Ollama's own report of what it has loaded (`/api/ps`, what
 * `ollama ps` prints) is polled throughout and shown in a corner, and a take in
 * which any model had memory on a GPU is refused rather than written.
 *
 * The narration is spoken by `demo/narration.py` at the moment it applies, and
 * the recording waits for each line to end before moving on, so the voice and
 * the picture cannot drift apart. Each line is captioned on screen too. A model
 * on a CPU takes minutes over the three cases, so the stretches where it is only
 * working are sped up (`--fast-forward`, 8 by default; 1 turns it off), with a
 * badge saying so while they last. The page's own clock keeps real time.
 */
import { mkdtemp, rm, mkdir } from 'node:fs/promises';
import { cpus, loadavg, tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import {
  AUDIO,
  Cues,
  encode,
  ffmpegBinary,
  parseArgs,
  playwright,
  python,
  soundtrack,
  video,
  videoSeconds,
} from './recording.mjs';

const { chromium } = playwright();

const args = parseArgs();
const url = (args.url || 'http://127.0.0.1:8100').replace(/\/$/, '');
const ollama = (args.ollama || 'http://localhost:11434').replace(/\/$/, '');
const out = resolve(args.out || 'demo/benchmark-on-cpu.mpg');
const models = (args.models || 'qwen3:0.6b')
  .split(',')
  .map((name) => name.trim())
  .filter(Boolean);
const chosenCases = args.cases ? args.cases.split(',').map((name) => name.trim()) : null;
const fastForward = Math.max(1, Number(args['fast-forward'] || 8));
const size = { width: 1280, height: 720 };

/** The least quiet between two lines, so one does not run into the next. */
const BREATH = 350;

/** How long the screen may sit unchanged and unspoken before it is sped up. */
const LULL = 1500;

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

async function ask(base, path, options = {}) {
  const response = await fetch(`${base}${path}`, {
    ...options,
    headers: { Accept: 'application/json', ...(options.headers ?? {}) },
  });
  if (!response.ok) throw new Error(`${base}${path} answered ${response.status}`);
  return response.json();
}

// -- what is said, and how it sounds --------------------------------------------

/**
 * How a model's name is said: "qwen3:0.6b" as "Kwen 3, 0.6 billion".
 *
 * Read letter by letter, a tag is noise; the families below are the ones a TTS
 * engine gets wrong, spelled the way they are said.
 */
const SAID_AS = {
  qwen: 'Kwen',
  gemma: 'Jemma',
  llama: 'Lahma',
  phi: 'Fye',
  smollm: 'Small L M',
};

function spoken(model) {
  const [family, tag = ''] = model.split(':');
  const words = family
    .toLowerCase()
    .replace(/^[a-z]+/, (name) => SAID_AS[name] ?? name)
    .replace(/[-_.]/g, ' ')
    .replace(/([a-z])(\d)/gi, '$1 $2');
  const billions = tag.match(/^(\d+(?:\.\d+)?)b$/i);
  if (billions) return `${words}, ${billions[1]} billion`;
  return tag && tag !== 'latest' ? `${words}, ${tag.replace(/[-_]/g, ' ')}` : words;
}

/** A duration as the page writes it ("52.3 s", "3 min 07 s"), as it is said. */
function spokenSeconds(label) {
  const counted = (count, unit) => `${count} ${unit}${count === 1 ? '' : 's'}`;
  const minutes = label.match(/^(\d+) min (\d+) s$/);
  if (minutes) {
    return `${counted(Number(minutes[1]), 'minute')} ${counted(Number(minutes[2]), 'second')}`;
  }
  const seconds = Number.parseFloat(label);
  return Number.isNaN(seconds) ? label : counted(Math.round(seconds), 'second');
}

/** A score as it is said: two places are as many as anybody hears. */
const spokenScore = (label) =>
  Number.isNaN(Number(label))
    ? label
    : Number(label)
        .toFixed(2)
        .replace(/^1\.00$/, 'one');

// -- the overlays the recording adds to the page ----------------------------------

/**
 * Put one of the recorder's own notes on the page, over whatever is there.
 *
 * Styled inline and dark in both themes, so it reads as the recording's and not
 * the page's. Re-created when missing, because the cards replace the document.
 */
async function overlay(page, id, lines, placement) {
  await page.evaluate(
    ({ id, lines, placement }) => {
      let note = document.getElementById(id);
      if (!lines.length) {
        note?.remove();
        return;
      }
      if (!note) {
        note = document.createElement('div');
        note.id = id;
        note.setAttribute('aria-hidden', 'true');
        Object.assign(note.style, {
          position: 'fixed',
          zIndex: '2147483647',
          background: 'rgba(17, 24, 39, 0.92)',
          color: '#f9fafb',
          borderRadius: '10px',
          boxShadow: '0 6px 24px rgba(0, 0, 0, 0.35)',
          pointerEvents: 'none',
          ...placement,
        });
        document.body.append(note);
      }
      note.replaceChildren(
        ...lines.map((line) => {
          const row = document.createElement('div');
          row.textContent = line;
          return row;
        }),
      );
    },
    { id, lines, placement },
  );
}

const CAPTION = {
  left: '50%',
  bottom: '22px',
  transform: 'translateX(-50%)',
  maxWidth: '980px',
  padding: '10px 18px',
  font: '500 19px/1.4 system-ui, sans-serif',
  textAlign: 'center',
};

const PROCESSOR = {
  right: '16px',
  top: '16px',
  padding: '10px 14px',
  font: '14px/1.5 ui-monospace, "Cascadia Mono", Consolas, monospace',
};

const BADGE = {
  left: '16px',
  top: '16px',
  padding: '8px 14px',
  font: '700 15px/1.4 system-ui, sans-serif',
  background: 'rgba(67, 56, 202, 0.95)',
};

// -- the processor, as Ollama reports it -------------------------------------------

/** Every model Ollama had loaded while the recording watched, and the most VRAM each had. */
const loaded = new Map();

/** What `ollama ps` prints in its PROCESSOR column, from the same two numbers. */
function processor({ size: total, size_vram: vram }) {
  if (!vram) return '100% CPU';
  if (vram >= total) return '100% GPU';
  const onCpu = Math.round((100 * (total - vram)) / total);
  return `${onCpu}%/${100 - onCpu}% CPU/GPU`;
}

const megabytes = (bytes) => `${Math.round(bytes / 1e6).toLocaleString('en-US')} MB`;

async function watchProcessor(page) {
  let answer;
  try {
    answer = await ask(ollama, '/api/ps');
  } catch {
    return;
  }
  const running = answer.models ?? [];
  for (const model of running) {
    const seen = loaded.get(model.name) ?? { size: 0, vram: 0 };
    loaded.set(model.name, {
      size: Math.max(seen.size, model.size ?? 0),
      vram: Math.max(seen.vram, model.size_vram ?? 0),
    });
  }
  const cores = cpus().length;
  await overlay(
    page,
    'recorder-processor',
    [
      'ollama ps',
      ...(running.length
        ? running.map(
            (model) => `${model.name}  ${megabytes(model.size ?? 0)}  ${processor(model)}`,
          )
        : ['no model loaded']),
      `load ${loadavg()[0].toFixed(1)} on ${cores} cores`,
    ],
    PROCESSOR,
  ).catch(() => {});
}

// -- the recording ------------------------------------------------------------------

const config = await ask(url, '/api/config');
const caseTitles = Object.fromEntries(config.cases.map((entry) => [entry.name, entry.title]));
const cases = chosenCases ?? config.cases.map((entry) => entry.name);
for (const name of cases) {
  if (!caseTitles[name])
    throw new Error(`no case called ${name}; the page has ${Object.keys(caseTitles)}`);
}
const before = await ask(url, '/api/state');
if (before.running) throw new Error('a comparison is already running on the page');
if (before.standings.length) {
  if (!('clear' in args)) {
    throw new Error(
      `the board at ${before.board} already holds runs: start python -m benchmark.server ` +
        'with an empty $BUY_AGENT_CACHE_DIR, or pass --clear to forget them',
    );
  }
  await ask(url, '/api/clear', { method: 'POST', body: '{}' });
}
const version = (await ask(ollama, '/api/version')).version;
const held = new Map(
  ((await ask(ollama, '/api/tags')).models ?? []).map((entry) => [entry.name, entry]),
);
const missing = models.filter((name) => !held.has(name));
if (missing.length) {
  throw new Error(`Ollama at ${ollama} has no ${missing.join(', ')}: ollama pull ${missing[0]}`);
}
const cpu = cpus()[0]?.model.replace(/\s+/g, ' ').trim() ?? 'unknown CPU';

const videoDir = await mkdtemp(join(tmpdir(), 'buy-agent-benchmark-'));
const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: size,
  deviceScaleFactor: 1,
  colorScheme: 'light',
  // The overlays are the recorder's, set from outside the page's own policy.
  bypassCSP: true,
  recordVideo: { dir: videoDir, size },
});
const page = await context.newPage();
// Playwright starts the recording with the page, so this is frame one.
const cues = new Cues();
cues.start();

/** The sped-up stretches, in seconds of the recording as it was taken. */
const fast = [];

function speedUp(on) {
  const open = fast.at(-1);
  if (on && (!open || open.to !== undefined)) {
    fast.push({ from: cues.now() });
    return overlay(page, 'recorder-fast', [`▶▶ ${fastForward}× while the model works`], BADGE);
  }
  if (!on && open && open.to === undefined) {
    open.to = cues.now();
    return overlay(page, 'recorder-fast', [], BADGE);
  }
  return Promise.resolve();
}

let lineCount = 0;

/**
 * Say a line, captioned, and resolve when it has been said.
 *
 * The page writes counts as "7/9", which an engine reads as a date or a slash;
 * the caption shows the words the voice says.
 */
async function say(line) {
  const text = line.replace(/(\d+)\/(\d+)/g, '$1 of $2');
  await speedUp(false);
  const clip = join(videoDir, `line-${++lineCount}.wav`);
  const seconds = Number(python(['-m', 'demo.narration', '--out', clip], { input: text }));
  await overlay(page, 'recorder-caption', [text], CAPTION).catch(() => {});
  cues.add('say', { clip });
  await sleep(seconds * 1000);
  await overlay(page, 'recorder-caption', [], CAPTION).catch(() => {});
  await sleep(BREATH);
}

/** Scroll smoothly to an element, so the recording pans rather than jumps. */
async function reveal(selector, block = 'center', settle = 800) {
  cues.add('scroll');
  await page
    .locator(selector)
    .first()
    .evaluate((node, where) => node.scrollIntoView({ behavior: 'smooth', block: where }), block);
  await sleep(settle);
}

/** Point at something: the cursor goes there, as a viewer's eye should. */
async function point(locator) {
  const box = await locator.boundingBox();
  if (box)
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, {
      steps: 18,
    });
}

async function click(locator) {
  await point(locator);
  cues.add('click');
  await locator.click();
}

function card(eyebrow, title, rows) {
  const escape = (text) =>
    String(text).replace(
      /[&<>"]/g,
      (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c],
    );
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><style>
  body { margin: 0; height: 100vh; display: grid; place-items: center; background: #0e1116;
         color: #e6edf3; font: 20px/1.5 system-ui, "Segoe UI", sans-serif; }
  main { width: 900px; }
  p.eyebrow { color: #8b83ff; font: 16px ui-monospace, monospace; margin: 0 0 8px; }
  h1 { font-size: 46px; margin: 0 0 30px; letter-spacing: -0.01em; }
  dl { display: grid; grid-template-columns: 210px 1fr; gap: 10px 22px; margin: 0; }
  dt { color: #99a3b0; } dd { margin: 0; }
</style></head><body><main><p class="eyebrow">${escape(eyebrow)}</p><h1>${escape(title)}</h1>
<dl>${rows.map(([name, value]) => `<dt>${escape(name)}</dt><dd>${escape(value)}</dd>`).join('')}</dl>
</main></body></html>`;
}

const describe = (name) => {
  const details = held.get(name)?.details ?? {};
  return [
    name,
    details.parameter_size,
    details.quantization_level,
    megabytes(held.get(name)?.size ?? 0),
  ]
    .filter(Boolean)
    .join(' · ');
};

// 1. What this is, and what it runs on.
await page.setContent(
  card('buy_agent', 'Local model benchmark, on a CPU', [
    ['Model server', `Ollama ${version} at ${ollama}`],
    ...models.map((name, index) => [
      index ? '' : models.length > 1 ? 'Models' : 'Model',
      describe(name),
    ]),
    ['Processor', `${cpu}, ${cpus().length} cores`],
    ['GPU', 'none used: checked against ollama ps throughout'],
  ]),
);
await sleep(600);
await say(
  `This is buy agent's local model benchmark, recorded as it ran. ` +
    `${models.length > 1 ? 'The models are' : 'The model is'} served by Ollama on this ` +
    'machine, and every answer is computed on the CPU alone. No GPU is used.',
);

// 2. The page.
await page.goto(url, { waitUntil: 'networkidle' });
await sleep(500);
await say(
  'The benchmark page scores a model on the two things the shopping agent asks of one: ' +
    "turning a shopper's request into a search query, and reading products, prices, ratings " +
    'and quotes off a fixed set of pages. Nothing touches the web, so every model reads the same pages.',
);

await point(page.locator('#provider'));
const listing = say(
  'First, the model server. Ollama is listening on its usual address, and List models asks it what it holds.',
);
await sleep(2600);
await click(page.locator('#list-models'));
await page.locator('#models input[type=checkbox]').first().waitFor();
await listing;

const shown = await page
  .locator('#models input[type=checkbox]')
  .evaluateAll((boxes) => boxes.map((box) => box.value));
const ticking = say(
  `It holds ${shown.length === 1 ? 'one model' : `${shown.length} models`}. ` +
    `We tick ${models.map(spoken).join(' and ')}.`,
);
for (const box of await page.locator('#models input[type=checkbox]:checked').all()) {
  if (!models.includes(await box.inputValue())) await click(box);
}
for (const name of models) {
  const box = page.locator(`#models input[value="${name}"]`);
  if (!(await box.isChecked())) await click(box);
  await sleep(300);
}
await ticking;

await reveal('#scripts', 'center');
const references = say(
  'Two reference answers are written by hand and need no model. Perfect is the answer key ' +
    'itself, and scores one. Sloppy makes the mistakes small models make. Together they ' +
    'are a ceiling and a floor to read the models against.',
);
await sleep(1800);
for (const script of ['perfect', 'sloppy']) {
  await click(page.locator(`#scripts input[value="${script}"]`));
  await sleep(500);
}
await references;

await reveal('#cases', 'center');
for (const name of Object.keys(caseTitles)) {
  const box = page.locator(`#cases input[value="${name}"]`);
  if ((await box.isChecked()) !== cases.includes(name)) await click(box);
}
await say(
  cases.length === Object.keys(caseTitles).length
    ? 'Then the cases: headphones for a flight, priced in dollars; a gaming laptop to carry; ' +
        'and an espresso machine, priced in euros. Each hides traps, like a headline posing as ' +
        'a product, a monthly payment posing as a price, or a decimal comma.'
    : `Then the ${cases.length === 1 ? 'case' : 'cases'}: ${cases.map((name) => caseTitles[name]).join('; ')}.`,
);

// 3. The run.
await click(page.getByRole('button', { name: 'Run', exact: true }));
await page.locator('#progress-card').waitFor({ state: 'visible' });
await reveal('#progress-card', 'start', 900);
const processorWatch = setInterval(() => watchProcessor(page), 1000);
await watchProcessor(page);
await say(
  'Run. The reference answers finish at once. Then each model takes every case in turn, ' +
    "so Ollama loads it only once. The box in the corner is Ollama's own report of what " +
    'it has loaded, and where.',
);

const seen = new Set();
const announced = new Set();
let current = '';
let explainedSpeed = false;
let quietSince = Date.now();

async function screen() {
  return page.evaluate(() => ({
    status: document.getElementById('status')?.textContent ?? '',
    done: Number(document.getElementById('progress')?.value ?? 0),
  }));
}

let lastDone = -1;
for (;;) {
  const { status, done } = await screen();
  const lines = [];

  if (done !== lastDone) {
    lastDone = done;
    cues.add('step');
    const state = await ask(url, '/api/state');
    if (state.problems.length)
      throw new Error(`the comparison hit a problem: ${state.problems.join('; ')}`);
    for (const row of state.standings) {
      for (const run of row.runs) {
        const key = `${row.key} ${run.case} ${run.finished}`;
        if (seen.has(key)) continue;
        seen.add(key);
        if (row.reference) continue;
        lines.push(
          run.failure
            ? `On ${run.case}, ${spoken(row.label)}'s answer could not be read, so the case scores nothing.`
            : `${spoken(row.label)} scores ${spokenScore(run.score_label)} on ${run.case}, in ${spokenSeconds(run.seconds_label)}.`,
        );
      }
    }
  }

  const running = status.match(/^Running (.+?) on (\S+): /);
  if (running && !running[1].endsWith('(scripted)') && `${running[1]} ${running[2]}` !== current) {
    current = `${running[1]} ${running[2]}`;
    const [, label, name] = running;
    if (!announced.has(label)) {
      announced.add(label);
      lines.push(
        `Now ${spoken(label)} takes the ${name} case. It turns the request into a search query, ` +
          'then reads the products off the pages, one token at a time, on the CPU.',
      );
      if (fastForward > 1 && !explainedSpeed) {
        explainedSpeed = true;
        lines.push(
          `That takes a while, so the video runs ${fastForward} times faster while the model ` +
            'works. The clock on the page keeps real time.',
        );
      }
    } else {
      lines.push(`Now the ${name} case.`);
    }
  }

  if (lines.length) {
    await say(lines.join(' '));
    quietSince = Date.now();
    continue;
  }
  if (!/^(Running|Starting|Stopping)/.test(status) && status) break;
  if (fastForward > 1 && Date.now() - quietSince > LULL) await speedUp(true);
  await sleep(200);
}
await speedUp(false);
cues.add('arrival');
const final = await ask(url, '/api/state');
await say(`${final.status.replace(/ in (.+)\.$/, (_, took) => ` in ${spokenSeconds(took)}.`)}`);

// 4. The standings.
await reveal('#standings', 'center', 1000);
const ranked = final.standings;
const floor = ranked.find((row) => row.key === 'script:sloppy');
const describeRow = (row) => {
  if (row.reference) {
    return `${row.label.replace(' (scripted)', '')}, the ${row.key === 'script:perfect' ? 'ceiling' : 'floor'}, ${spokenScore(row.score_label)}`;
  }
  const versus =
    floor && row.score_label !== floor.score_label
      ? Number(row.score_label) > Number(floor.score_label)
        ? ', above the sloppy floor'
        : ', below the sloppy floor'
      : '';
  return `${spoken(row.label)}, ${spokenScore(row.score_label)}${versus}, taking ${spokenSeconds(row.seconds_label)} a case on the CPU`;
};
await say(
  `The standings rank by the mean score over the cases. In order: ${ranked.map(describeRow).join('; ')}.`,
);
await point(page.locator('#standings thead th').nth(4));
await say(
  'Each case has its own column. The query column scores the search query on its own, ' +
    'and the time is how long the model took per case.',
);

// 5. One model, opened.
const first = ranked.find((row) => !row.reference);
if (first) {
  await click(page.getByRole('button', { name: first.label, exact: true }));
  await page.locator('#details').waitFor({ state: 'visible' });
  await reveal('#details', 'start', 900);
  await say(
    "Opening a model's row shows each case it ran: the scorecard, metric by metric, against " +
      'its floor; the query it searched with, and the checks that query passed; and every ' +
      'product it reported, tagged where it invented or repeated one.',
  );
  const run = first.runs[0];
  if (run?.summary) {
    await reveal('#details-runs article:nth-of-type(1) .run-grid', 'center', 900);
    await say(`On ${run.case}: ${run.summary}`);
  }
  for (let index = 2; index <= first.runs.length; index += 1) {
    await reveal(`#details-runs article:nth-of-type(${index})`, 'start', 2200);
  }
}

// 6. How it is scored.
await reveal('details summary', 'center', 600);
await click(page.locator('details summary'));
await sleep(500);
await reveal('table.metrics', 'center', 900);
await say(
  'Each metric is a share of something, weighed into the score: whether the products are real, ' +
    'whether their figures were printed on the pages, whether each links to its page, and ' +
    'whether its quotes were really said. The query is scored apart.',
);

// 7. What the processor did, as Ollama reported it.
clearInterval(processorWatch);
await watchProcessor(page);
const report = [...loaded.entries()];
const onGpu = report.filter(([, seenModel]) => seenModel.vram > 0);
if (onGpu.length) {
  throw new Error(
    `not a CPU-only take: Ollama counted ${onGpu.map(([name, seenModel]) => `${megabytes(seenModel.vram)} of ${name}`).join(', ')} as GPU memory. ` +
      'Hide the GPUs from it (CUDA_VISIBLE_DEVICES=-1); on a CPU with AMX, that memory is ' +
      "llama.cpp's weights repacked for AMX, which LLAMA_ARG_NO_REPACK=1 keeps in RAM",
  );
}
await page.setContent(
  card('buy_agent', 'Every answer came from the CPU', [
    ['Processor', `${cpu}, ${cpus().length} cores`],
    ...(report.length
      ? report.map(([name, seenModel], index) => [
          index ? '' : 'ollama ps reported',
          `${name}: ${megabytes(seenModel.size)} loaded, 0 MB on a GPU, 100% CPU`,
        ])
      : [['ollama ps reported', 'no model loaded while it was watched']]),
    ['Runs', final.status],
  ]),
);
await sleep(500);
await say(
  'Throughout the run, Ollama reported no GPU memory in use for any model. Every answer ' +
    "came from the CPU. That was buy agent's local model benchmark.",
);
await sleep(800);

const take = await page.video().path();
await context.close();
await browser.close();

/** Where a moment of the take lands in the finished video, once the fast stretches shrink. */
function finishedAt(at) {
  return fast.reduce(
    (moment, { from, to }) => moment - Math.max(0, Math.min(at, to) - from) * (1 - 1 / fastForward),
    at,
  );
}

const ffmpeg = ffmpegBinary(args.ffmpeg);
await mkdir(dirname(out), { recursive: true });
const taken = videoSeconds(ffmpeg, [take]);
const seconds = finishedAt(taken);
const track = soundtrack(
  cues.list.map((cue) => ({ ...cue, at: finishedAt(cue.at) })),
  seconds,
  videoDir,
);
// One timestamp expression shrinks every fast stretch: a frame's new time is its
// old one less what the stretches before it gave up. Frames that land closer
// together than 1/25 s are dropped by the output rate.
const shrink = fast
  .map(
    ({ from, to }) =>
      `-${(1 - 1 / fastForward).toFixed(6)}*clip(T-${from.toFixed(3)},0,${(to - from).toFixed(3)})`,
  )
  .join('');
encode(ffmpeg, [
  '-i',
  take,
  '-i',
  track,
  '-filter_complex',
  `[0:v]setpts='(T${shrink})/TB'[v]`,
  '-map',
  '[v]',
  '-map',
  '1:a',
  ...AUDIO,
  '-shortest',
  // A still page for minutes: a keyframe every ten seconds, and 1.2 Mbit/s on average.
  ...video({ rate: '1200k', keyframes: 250 }),
  out,
]);
await rm(videoDir, { recursive: true, force: true });
console.log(
  `wrote ${out} -- ${seconds.toFixed(1)}s (${taken.toFixed(1)}s as taken), ${lineCount} lines spoken`,
);
