/**
 * Drive the demo UI in Chromium and write the recording out as MPEG.
 *
 * Playwright records WebM, so the run is captured once and transcoded with the
 * ffmpeg that ships beside the browser. Nothing here waits on a clock it does
 * not have to: every step waits for the thing it is about to act on, so the
 * recording has no dead air in it beyond the pacing `demo/server.py` puts there
 * on purpose.
 *
 *   node demo/record.mjs --url http://127.0.0.1:8000 --out demo/buy-agent-demo.mpg
 *
 * `--script` names the same fabricated web `demo/server.py` was started with,
 * and is what the typed request and the shop pages are read out of -- so the
 * request is not written down a second time here, and the page the recording
 * ends on says what the pipeline read. `--request` overrides the first of those.
 */
import { spawnSync, execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtemp, rm, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');

/** Playwright, from wherever it is installed -- locally, or globally as here. */
function playwright() {
  try {
    return require('playwright');
  } catch {
    const global = execFileSync('npm', ['root', '-g'], { encoding: 'utf8' }).trim();
    return require(join(global, 'playwright'));
  }
}

const { chromium } = playwright();

const args = Object.fromEntries(
  process.argv.slice(2).reduce((pairs, value, index, all) => {
    if (value.startsWith('--')) pairs.push([value.slice(2), all[index + 1] ?? '']);
    return pairs;
  }, []),
);

const url = args.url ?? 'http://127.0.0.1:8000';
const out = resolve(args.out ?? 'demo/buy-agent-demo.mpg');
const followLink = 'follow-link' in args;
const size = { width: 1280, height: 720 };

/**
 * The demo script's own request and pages, read out of Python.
 *
 * `scripts/start.ps1` reads the model and the Ollama server the same way, and
 * for the same reason: a default written down in two languages is a default
 * that will disagree with itself. Here it is the sentence that gets typed and
 * the text behind every `*.example` link on the results.
 */
function fixture(name) {
  const code =
    'import importlib, json, sys; module = importlib.import_module(sys.argv[1]); ' +
    'print(json.dumps({"request": module.REQUEST, "pages": {' +
    'result.url: {"title": result.title, "text": module.PAGE_TEXT[result.url]} ' +
    'for result in module.PAGES}}))';
  let failure;
  for (const python of ['python', 'python3']) {
    try {
      return JSON.parse(
        execFileSync(python, ['-c', code, `demo.${name}`], { encoding: 'utf8', cwd: root }),
      );
    } catch (error) {
      failure = error;
    }
  }
  throw new Error(`could not read demo.${name} with python: ${failure?.message ?? ''}`);
}

const script = fixture(args.script ?? 'books');
const request = args.request ?? script.request;

/**
 * An ffmpeg that can write an MPEG program stream.
 *
 * Playwright ships one beside its browsers, but that build is stripped down to
 * what recording needs and has neither the `mpeg` muxer nor the `mpeg1video`
 * encoder, so a system ffmpeg is preferred and the bundled one is only a last
 * resort. `--ffmpeg` names a third.
 */
function ffmpegBinary() {
  if (args.ffmpeg) return args.ffmpeg;
  for (const candidate of ['/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg']) {
    if (existsSync(candidate)) return candidate;
  }
  const browsers = process.env.PLAYWRIGHT_BROWSERS_PATH ?? '/opt/pw-browsers';
  for (const candidate of ['ffmpeg-1011/ffmpeg-linux', 'ffmpeg/ffmpeg-linux']) {
    const path = join(browsers, candidate);
    if (existsSync(path)) return path;
  }
  return 'ffmpeg';
}

const escapeHtml = (text) =>
  text.replace(
    /[&<>"]/g,
    (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[character],
  );

/**
 * One of the shops, as a page a browser can show.
 *
 * The hosts are all `*.example` and cannot resolve, so the recorder answers for
 * them -- with the very text `demo/server.py` handed the pipeline, laid out as
 * the page it is pretending to be. Nothing is added: what the shopper sees at
 * the end of the recording is what the agent read.
 *
 * The heading is the search result's own title minus its publisher credit,
 * split off the way `extraction.clean_name` splits it, because the first line
 * of the text is the site name and the second is as often a navigation bar as
 * anything worth putting in an `h1`.
 */
function shopPage({ title, text }) {
  const [site, ...lines] = text.split('\n').filter((line) => line.trim());
  const heading = title.split(' | ')[0];
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>${escapeHtml(title)}</title>
<style>
  :root { color-scheme: light; }
  body { margin: 0; font: 16px/1.6 system-ui, "Segoe UI", sans-serif; color: #1b1f24;
         background: #f6f7f9; }
  header { background: #14212e; color: #fff; padding: 18px 32px; font-weight: 600;
           letter-spacing: .02em; }
  main { max-width: 760px; margin: 32px auto; padding: 28px 34px; background: #fff;
         border: 1px solid #e3e6ea; border-radius: 10px; }
  h1 { font-size: 26px; margin: 0 0 18px; }
  p { margin: 0 0 8px; }
</style></head>
<body><header>${escapeHtml(site)}</header><main><h1>${escapeHtml(heading)}</h1>
${lines.map((line) => `<p>${escapeHtml(line)}</p>`).join('\n')}
</main></body></html>`;
}

/** Scroll smoothly to an element, so the recording pans rather than jumps. */
async function reveal(page, selector, settle = 750) {
  await page
    .locator(selector)
    .first()
    .evaluate((node) => {
      node.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
  await page.waitForTimeout(settle);
}

const videoDir = await mkdtemp(join(tmpdir(), 'buy-agent-demo-'));
const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: size,
  deviceScaleFactor: 1,
  recordVideo: { dir: videoDir, size },
});
await context.route(/^https?:\/\/[^/]+\.example\//, (route) => {
  const page = script.pages[route.request().url()];
  if (page === undefined) return route.abort();
  return route.fulfill({ contentType: 'text/html; charset=utf-8', body: shopPage(page) });
});

const page = await context.newPage();

await page.goto(url, { waitUntil: 'networkidle' });
await page.waitForTimeout(450);

// Typed rather than filled: a demo of a search box should show it being used.
const box = page.getByPlaceholder('wireless noise cancelling headphones under $200');
await box.click();
await box.pressSequentially(request, { delay: 45 });
await page.waitForTimeout(400);

await page.getByRole('button', { name: 'Find products' }).click();

// The progress panel appears with the first log line and the results with the
// last, so both waits end exactly when there is something new to look at.
await page.locator('app-progress-log').waitFor({ state: 'visible' });
await page.locator('section.results').waitFor({ state: 'visible', timeout: 60_000 });
await page.waitForTimeout(1000);

await reveal(page, 'section.results h2');
await reveal(page, 'app-product-card:nth-of-type(1)');
await reveal(page, 'app-product-card:nth-of-type(2)');
await reveal(page, 'app-product-card:nth-of-type(3)');

// The rest of what the agent found, which the page keeps folded away.
await page.locator('details.also summary').click();
await page.waitForTimeout(1000);
await reveal(page, 'details.also');
await page.mouse.wheel(0, 500);
await page.waitForTimeout(1600);

// ...and then what the shopper came for: the top product, on the page the
// agent found it on. The card opens it in a tab of its own, which records as a
// second video, so the two are stitched back together below.
const pages = [page];
if (followLink) {
  await reveal(page, 'app-product-card:nth-of-type(1)');
  const [shop] = await Promise.all([
    context.waitForEvent('page'),
    page.locator('app-product-card').first().locator('h3 a').click(),
  ]);
  await shop.waitForLoadState('domcontentloaded');
  await shop.bringToFront();
  await shop.waitForTimeout(2500);
  pages.push(shop);
}

const takes = await Promise.all(pages.map((recorded) => recorded.video().path()));
await context.close();
await browser.close();

await mkdir(dirname(out), { recursive: true });
const inputs = takes.flatMap((take) => ['-i', take]);
const stitch =
  takes.length > 1
    ? [
        '-filter_complex',
        `${takes.map((_, index) => `[${index}:v]`).join('')}concat=n=${takes.length}:v=1:a=0[v]`,
        '-map',
        '[v]',
      ]
    : [];
const encode = spawnSync(
  ffmpegBinary(),
  [
    ...['-y'],
    ...inputs,
    ...stitch,
    '-c:v',
    'mpeg1video',
    '-q:v',
    '4',
    '-r',
    '25',
    '-an',
    '-f',
    'mpeg',
    out,
  ],
  { stdio: ['ignore', 'ignore', 'pipe'] },
);
if (encode.status !== 0) {
  throw new Error(`ffmpeg failed:\n${encode.stderr?.toString() ?? ''}`);
}
await rm(videoDir, { recursive: true, force: true });
console.log(`wrote ${out}`);
