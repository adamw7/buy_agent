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
 */
import { spawnSync, execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtemp, readdir, rm, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';

const require = createRequire(import.meta.url);

/** Playwright, from wherever it is installed -- locally, or globally as here. */
function playwright() {
  try {
    return require('playwright');
  } catch {
    const root = execFileSync('npm', ['root', '-g'], { encoding: 'utf8' }).trim();
    return require(join(root, 'playwright'));
  }
}

const { chromium } = playwright();

const args = Object.fromEntries(
  process.argv.slice(2).reduce((pairs, value, index, all) => {
    if (value.startsWith('--')) pairs.push([value.slice(2), all[index + 1]]);
    return pairs;
  }, []),
);

const url = args.url ?? 'http://127.0.0.1:8000';
const out = resolve(args.out ?? 'demo/buy-agent-demo.mpg');
const request = args.request ?? 'wwii books about war in Europe 1944-45';
const size = { width: 1280, height: 720 };

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
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH ?? '/opt/pw-browsers';
  for (const candidate of ['ffmpeg-1011/ffmpeg-linux', 'ffmpeg/ffmpeg-linux']) {
    const path = join(root, candidate);
    if (existsSync(path)) return path;
  }
  return 'ffmpeg';
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
const page = await context.newPage();

await page.goto(url, { waitUntil: 'networkidle' });
await page.waitForTimeout(450);

// Typed rather than filled: a demo of a search box should show it being used.
const box = page.getByPlaceholder('wireless noise cancelling headphones under $200');
await box.click();
await box.pressSequentially(request, { delay: 55 });
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

await context.close();
await browser.close();

const [recorded] = (await readdir(videoDir)).filter((name) => name.endsWith('.webm'));
if (!recorded) throw new Error(`no video written to ${videoDir}`);

await mkdir(dirname(out), { recursive: true });
const encode = spawnSync(
  ffmpegBinary(),
  [
    '-y',
    '-i',
    join(videoDir, recorded),
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
