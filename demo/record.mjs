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
 *
 * `--sound` adds a soundtrack. Chromium records no audio, so there is none to
 * capture: what is written instead is a *cue* per thing that happened -- a key,
 * the button, each line arriving in the progress panel, the results landing --
 * and `demo/sound.py` turns those into a WAV that is muxed in below. The track
 * is the run's own timing rather than a clip laid over it, and a log line that
 * took something away is the one cue given a note of its own.
 */
import { mkdtemp, rm, mkdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import {
  AUDIO,
  Cues,
  VIDEO,
  encode,
  ffmpegBinary,
  parseArgs,
  playwright,
  python,
  soundtrack,
  videoSeconds,
} from './recording.mjs';

const { chromium } = playwright();

const args = parseArgs();

const url = args.url ?? 'http://127.0.0.1:8000';
const out = resolve(args.out ?? 'demo/buy-agent-demo.mpg');
const followLink = 'follow-link' in args;
const sound = 'sound' in args;
const size = { width: 1280, height: 720 };

/**
 * The log lines that are the pipeline catching the fake model out.
 *
 * `demo/README.md` has the six of them in a table with what each was wrong
 * about. Only the verb is matched: the counts move with the script, and a
 * seventh heuristic added to `buy_agent/` announces itself in one of these three
 * words or it is not one that took anything away.
 */
const TOOK_SOMETHING_AWAY = /^(Discarded|Dropped|Merged) /;

const cues = new Cues();
const cue = (kind) => cues.add(kind);

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
  return JSON.parse(python(['-c', code, `demo.${name}`]));
}

const script = fixture(args.script ?? 'books');
const request = args.request ?? script.request;

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
  cue('scroll');
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
  return route.fulfill({
    contentType: 'text/html; charset=utf-8',
    body: shopPage(page),
  });
});

const page = await context.newPage();
// Playwright starts the recording with the page, so this is frame one.
cues.start();

await page.goto(url, { waitUntil: 'networkidle' });
await page.waitForTimeout(450);

// Typed rather than filled: a demo of a search box should show it being used.
// A character at a time rather than through `pressSequentially`'s own delay, so
// each keystroke is a cue at the moment it happened rather than one worked out
// afterwards from a nominal rate.
const box = page.getByPlaceholder('wireless noise cancelling headphones under $200');
await box.click();
for (const character of request) {
  cue('key');
  await box.pressSequentially(character, { delay: 0 });
  await page.waitForTimeout(45);
}
await page.waitForTimeout(400);

cue('click');
const find = page.getByRole('button', { name: 'Find products' });
await find.click();

// A budget written in the request is offered in its box before any run goes out
// (ADR-0059), so a press may only show the offer -- the laptops' "below 1000 USD"
// does -- and the next one searches with it. Held on screen a moment in between,
// the offer being part of what the page does.
await page.locator('app-progress-log, small.noticed').first().waitFor();
if (!(await page.locator('app-progress-log').count())) {
  await page.waitForTimeout(900);
  cue('click');
  await find.click();
}

// The progress panel appears with the first log line and the results with the
// last, so both waits end exactly when there is something new to look at. The
// panel is read while it fills rather than afterwards: a line's cue has to be
// the moment it arrived, and a finished panel no longer says when that was.
await page.locator('app-progress-log').waitFor({ state: 'visible' });
const messages = page.locator('app-progress-log p.line span.message');
let seen = 0;
const watching = setInterval(async () => {
  try {
    const lines = await messages.allInnerTexts();
    for (const line of lines.slice(seen)) {
      cue(TOOK_SOMETHING_AWAY.test(line.trim()) ? 'caught' : 'step');
    }
    seen = lines.length;
  } catch {
    // The page went away mid-poll, which means the run is over and the cues
    // for it are complete. Nothing here is worth failing a recording for.
  }
}, 40);
await page.locator('section.results').waitFor({ state: 'visible', timeout: 60_000 });
clearInterval(watching);
cue('arrival');
await page.waitForTimeout(1000);

await reveal(page, 'section.results h2');
await reveal(page, 'app-product-card:nth-of-type(1)');
await reveal(page, 'app-product-card:nth-of-type(2)');
await reveal(page, 'app-product-card:nth-of-type(3)');

// The rest of what the agent found, which the page keeps folded away.
cue('click');
await page.locator('details.also summary').first().click();
await page.waitForTimeout(1000);
await reveal(page, 'details.also');
cue('scroll');
await page.mouse.wheel(0, 500);
await page.waitForTimeout(1200);

// ...and then what the shopper came for: the top product, on the page the
// agent found it on. The card opens it in a tab of its own, which records as a
// second video, so the two are stitched back together below.
const pages = [page];
if (followLink) {
  await reveal(page, 'app-product-card:nth-of-type(1)');
  cue('click');
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

const ffmpeg = ffmpegBinary(args.ffmpeg);

await mkdir(dirname(out), { recursive: true });
const seconds = videoSeconds(ffmpeg, takes);
const track = sound ? soundtrack(cues.list, seconds, videoDir) : null;
const inputs = [...takes, ...(track ? [track] : [])].flatMap((input) => ['-i', input]);
const stitch =
  takes.length > 1
    ? [
        '-filter_complex',
        `${takes.map((_, index) => `[${index}:v]`).join('')}concat=n=${takes.length}:v=1:a=0[v]`,
        '-map',
        '[v]',
      ]
    : ['-map', '0:v'];
const audio = track ? ['-map', `${takes.length}:a`, ...AUDIO, '-shortest'] : ['-an'];
encode(ffmpeg, [...inputs, ...stitch, ...audio, ...VIDEO, out]);
await rm(videoDir, { recursive: true, force: true });
console.log(
  `wrote ${out} -- ${seconds.toFixed(1)}s, ${sound ? `${cues.list.length} cues` : 'silent'}`,
);
