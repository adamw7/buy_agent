/**
 * Take the README's pictures: the search form with its settings open, or --
 * given `--script` -- the results that script's run ends on.
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
 *
 *   python -m demo.server --script laptops --pace 0 --port 8000
 *   node demo/screenshot.mjs --script laptops --out docs/results.png
 *
 * With `--script` it does press it: the script's own request is typed, read out
 * of Python as `record.mjs` reads it, and the picture is the results section
 * once the run lands -- the top 3, the rest folded away as the page leaves them.
 * The name has to be the one the server was started with, since the server
 * searches that fabricated web and this only types the sentence.
 */
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/** Playwright, from wherever it is installed -- locally, or globally. */
function playwright() {
  try {
    return require("playwright");
  } catch {
    const root = execFileSync("npm", ["root", "-g"], {
      encoding: "utf8",
    }).trim();
    return require(join(root, "playwright"));
  }
}

const { chromium } = playwright();

const args = Object.fromEntries(
  process.argv.slice(2).reduce((pairs, value, index, all) => {
    if (value.startsWith("--"))
      pairs.push([value.slice(2), all[index + 1] ?? ""]);
    return pairs;
  }, []),
);

const url = args.url ?? "http://127.0.0.1:8000";
const out = resolve(args.out ?? "docs/ui.png");
const width = Number(args.width ?? 1100);
// Twice the CSS width, because GitHub scales a README image down to its column
// and a 1x picture of a form is soft by the time it gets there.
const scale = Number(args.scale ?? 2);

/** A demo script's request, read out of Python so it is written down once. */
function scriptedRequest(name) {
  const code =
    "import importlib, sys; print(importlib.import_module(sys.argv[1]).REQUEST)";
  let failure;
  for (const python of ["python", "python3"]) {
    try {
      return execFileSync(python, ["-c", code, `demo.${name}`], {
        encoding: "utf8",
        cwd: root,
      }).trim();
    } catch (error) {
      failure = error;
    }
  }
  throw new Error(
    `could not read demo.${name} with python: ${failure?.message ?? ""}`,
  );
}

/** The placeholder is the request, so the example is not written down twice. */
const REQUEST = "wireless noise cancelling headphones under $200";
const results = "script" in args;
const request =
  args.request ?? (results ? scriptedRequest(args.script) : REQUEST);

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width, height: 1400 },
  deviceScaleFactor: scale,
});
const page = await context.newPage();
await page.goto(url, { waitUntil: "networkidle" });

// Typed rather than filled, as in `record.mjs`: what the box does to a long
// request is part of what the picture is of.
const box = page.getByPlaceholder(REQUEST);
await box.click();
await box.pressSequentially(request, { delay: 5 });

let top = 0;
let bottom;
if (results) {
  await page.getByRole("button", { name: "Find products" }).click();
  const section = page.locator("section.results");
  await section.waitFor({ state: "visible", timeout: 60_000 });
  await page.waitForTimeout(300);
  // Clipped to the results alone: the form above them is the other picture, and
  // the progress panel is scrolled to its last lines by then, which are the
  // report the cards below say better.
  const box = await section.evaluate((node) => {
    const rect = node.getBoundingClientRect();
    return {
      top: Math.floor(rect.top + window.scrollY),
      bottom: Math.ceil(rect.bottom + window.scrollY),
    };
  });
  top = Math.max(0, box.top - 12);
  bottom = box.bottom;
} else {
  // Settings are folded away by default and everything inside them is hidden
  // until they are opened -- the model <select> included, which is why this waits
  // for it after the click rather than before.
  const settings = page.locator("details.advanced");
  if (!(await settings.evaluate((node) => node.open))) {
    await page.locator("details.advanced > summary").click();
  }
  await page.locator('input[name="sources"]').waitFor({ state: "visible" });
  await page.locator('select[name="model"]').waitFor({ state: "visible" });
  await page.waitForTimeout(300);

  // Clipped to the card rather than to the viewport: the form decides how tall
  // this picture is, and a fixed height leaves dead space under it -- or crops a
  // field off the bottom the next time one is added.
  bottom = await settings.evaluate((node) =>
    Math.ceil(node.closest("form.card").getBoundingClientRect().bottom),
  );
}
const height = bottom + 24 - top;
await page.screenshot({
  path: out,
  fullPage: true,
  clip: { x: 0, y: top, width, height },
});
console.log(`${out}: ${width * scale}x${height * scale}`);

await context.close();
await browser.close();
