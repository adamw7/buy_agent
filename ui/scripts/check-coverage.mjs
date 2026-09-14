/**
 * Fail the build when the UI's coverage drops, the way `coverage report` does
 * for the Python side. Run it through `npm run test:coverage`, which produces the
 * summary this reads.
 *
 * Statements and lines only. v8 attributes branches inside a compiled Angular
 * template to positions no test can reach -- search-form.html reports 100% of
 * its statements and about 30% of its branches -- so a branch floor would be a
 * number about the instrumentation rather than about the tests.
 *
 * The floor lives here rather than in vitest's own `thresholds`: the Angular
 * unit-test builder does not fail the run on one, so a threshold in a vitest
 * config would look like a floor while letting a regression through.
 */
import { readFileSync } from 'node:fs';

const FLOOR = { statements: 98, lines: 98 };
const SUMMARY = new URL('../coverage/ui/coverage-summary.json', import.meta.url);

let total;
try {
  total = JSON.parse(readFileSync(SUMMARY, 'utf8')).total;
} catch (error) {
  console.error(`No coverage summary at ${SUMMARY.pathname}. Run: npm run test:coverage`);
  console.error(error.message);
  process.exit(1);
}

// One pass: what each kind reads and whether it clears its floor are the same
// question, and asked twice they are two places for the floor to be read wrong.
const short = [];
for (const [kind, floor] of Object.entries(FLOOR)) {
  const { pct, covered, total: count } = total[kind];
  const under = pct < floor;
  if (under) {
    short.push(kind);
  }
  const verdict = under ? `below ${floor}%` : 'ok';
  console.log(`${kind.padEnd(11)} ${String(pct).padStart(6)}%  (${covered}/${count})  ${verdict}`);
}

if (short.length) {
  console.error(`\nCoverage fell below the floor for: ${short.join(', ')}`);
  process.exit(1);
}
