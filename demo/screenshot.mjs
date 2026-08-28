/**
 * Take the README's picture of the search form, settings open.
 *
 * `record.mjs` records the two videos; this writes the one still. Both drive the
 * built UI through Playwright and both point at `python -m demo.server` rather
 * than at `buy_agent.server`, for the same reason: the header pill and the model
 * dropdown are answers from an Ollama, so a picture taken without one shows
 * "Ollama unreachable" over a text box -- which is what `docs/ui.png` showed
 * until this script existed, two paragraphs above a README that describes the
 * dropdown. The scripted list makes the picture the same one every time.
 *
 *   python -m demo.server --pace 0 --port 8000        # in one terminal
 *   node demo/screenshot.mjs --out docs/ui.png        # in the other
 *
 * `--pace 0` because nothing here waits on a run: the picture is of the form
 * before anyone presses the button.
 */
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { join, resolve } from 'node:path';

const require = createRequire(import.meta.url);

/** Playwright, from wherever it is installed -- locally, or globally. */
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
    if (value.startsWith('--')) pairs.push([value.slice(2), all[index + 1] ?? '']);
    return pairs;
  }, []),
);

const url = args.url ?? 'http://127.0.0.1:8000';
const out = resolve(args.out ?? 'docs/ui.png');
const width = Number(args.width ?? 1100);
// Twice the CSS width, because GitHub scales a README image down to its column
// and a 1x picture of a form is soft by the time it gets there.
const scale = Number(args.scale ?? 2);

/** The placeholder is the request, so the example is not written down twice. */
const REQUEST = 'wireless noise cancelling headphones under $200';
const request = args.request ?? REQUEST;

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width, height: 1400 },
  deviceScaleFactor: scale,
});
const page = await context.newPage();
await page.goto(url, { waitUntil: 'networkidle' });

// Typed rather than filled, as in `record.mjs`: what the box does to a long
// request is part of what the picture is of.
const box = page.getByPlaceholder(REQUEST);
await box.click();
await box.pressSequentially(request, { delay: 5 });

// Settings are folded away by default and everything inside them is hidden
// until they are opened -- the model <select> included, which is why this waits
// for it after the click rather than before.
const settings = page.locator('details.advanced');
if (!(await settings.evaluate((node) => node.open))) {
  await page.locator('details.advanced > summary').click();
}
await page.locator('input[name="sources"]').waitFor({ state: 'visible' });
await page.locator('select[name="model"]').waitFor({ state: 'visible' });
await page.waitForTimeout(300);

// Clipped to the card rather than to the viewport: the form decides how tall
// this picture is, and a fixed height leaves dead space under it -- or crops a
// field off the bottom the next time one is added.
const bottom = await settings.evaluate((node) =>
  Math.ceil(node.closest('form.card').getBoundingClientRect().bottom),
);
await page.screenshot({ path: out, clip: { x: 0, y: 0, width, height: bottom + 24 } });
console.log(`${out}: ${width * scale}x${(bottom + 24) * scale}`);

await context.close();
await browser.close();
