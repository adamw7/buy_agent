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

/** Every cue so far, in seconds from the first frame. */
const cues = [];
let firstFrame = 0;
const cue = (kind) => cues.push({ kind, at: (Date.now() - firstFrame) / 1000 });

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

/**
 * How the picture is written: MPEG-2 video in a program stream, still a `.mpg`.
 *
 * MPEG-1 is what the first two recordings used and it is the wrong format for
 * this picture. 1280x720 is far outside MPEG-1's constrained parameters, so the
 * encoder declares a video buffer smaller than a single one of its own
 * keyframes and every pack the muxer writes violates the system target decoder.
 * A lenient player ignores all of that and shows the film; a player that has to
 * schedule an audio track against the same model gives up and opens nothing.
 *
 * So the rate and the buffer are stated rather than left to `-q:v`, and the
 * codec is the one whose levels this frame size is inside. An MPEG-2 program
 * stream is the DVD lineage -- the format with the widest player support there
 * is -- and it is what `.mpg` means to everything that reads one.
 */
const VIDEO = [
  '-c:v',
  'mpeg2video',
  '-b:v',
  '3000k',
  '-maxrate',
  '3500k',
  '-bufsize',
  '1835008',
  '-r',
  '25',
  '-f',
  'mpeg',
];

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
  return route.fulfill({ contentType: 'text/html; charset=utf-8', body: shopPage(page) });
});

const page = await context.newPage();
// Playwright starts the recording with the page, so this is frame one.
firstFrame = Date.now();

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
await page.getByRole('button', { name: 'Find products' }).click();

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
await page.locator('details.also summary').click();
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

const ffmpeg = ffmpegBinary();

/**
 * How long the takes run for, decoded rather than read off a header.
 *
 * A soundtrack has to be exactly as long as the picture it goes under, and a
 * WebM that Playwright is still writing when the context closes carries a
 * duration that is anywhere from wrong to absent. Decoding to nowhere costs a
 * second and answers with the timestamp of the last frame, which is the clock
 * the cues were taken against.
 */
function videoSeconds(paths) {
  return paths.reduce((total, path) => {
    const probe = spawnSync(ffmpeg, ['-i', path, '-f', 'null', '-'], {
      stdio: ['ignore', 'ignore', 'pipe'],
    });
    const stamps = [...(probe.stderr?.toString() ?? '').matchAll(/time=(\d+):(\d+):([\d.]+)/g)];
    const last = stamps.at(-1);
    if (last === undefined) throw new Error(`could not measure ${path}`);
    return total + Number(last[1]) * 3600 + Number(last[2]) * 60 + Number(last[3]);
  }, 0);
}

/**
 * The cues, as a WAV of that same length -- synthesised by `demo/sound.py`.
 *
 * Python again, for the reason this file already reads the script with it: what
 * the demo sounds like is the demo's to say, and there is one interpreter here
 * that already has to be on PATH.
 */
function soundtrack(seconds) {
  const track = join(videoDir, 'track.wav');
  let failure;
  for (const python of ['python', 'python3']) {
    const made = spawnSync(
      python,
      ['-m', 'demo.sound', '--duration', String(seconds), '--out', track],
      { cwd: root, input: JSON.stringify(cues), stdio: ['pipe', 'ignore', 'pipe'] },
    );
    if (made.status === 0) return track;
    failure = made.stderr?.toString() || made.error?.message;
  }
  throw new Error(`demo.sound failed:\n${failure ?? ''}`);
}

await mkdir(dirname(out), { recursive: true });
const seconds = videoSeconds(takes);
const track = sound ? soundtrack(seconds) : null;
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
// MP2 is the audio an MPEG program stream carries, so a recording with sound in
// it is still the one format that plays anywhere.
const audio = track
  ? ['-map', `${takes.length}:a`, '-c:a', 'mp2', '-b:a', '192k', '-ar', '44100', '-shortest']
  : ['-an'];
const encode = spawnSync(ffmpeg, ['-y', ...inputs, ...stitch, ...audio, ...VIDEO, out], {
  stdio: ['ignore', 'ignore', 'pipe'],
});
if (encode.status !== 0) {
  throw new Error(`ffmpeg failed:\n${encode.stderr?.toString() ?? ''}`);
}
await rm(videoDir, { recursive: true, force: true });
console.log(`wrote ${out} -- ${seconds.toFixed(1)}s, ${sound ? `${cues.length} cues` : 'silent'}`);
