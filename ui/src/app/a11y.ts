/**
 * The accessibility check the four component specs run.
 *
 * Rules are enabled by name, each holding a promise already made (ADR-0033,
 * ADR-0049): a blanket run would fail on undecided rules and pass vacuously on
 * what jsdom cannot answer.
 */
import axe from 'axe-core';

/** The rules that run; each applies to at least one component, so none passes vacuously. */
export const CHECKED: Record<string, string> = {
  // Every kind of control the page draws has an accessible name.
  label: 'Every box the form draws is one somebody can be told the name of.',
  'select-name': 'The pickers -- provider, model, rail, backend, currency, criterion -- are named.',
  'button-name': 'Find products, Stop, the examples, Download, and both paying buttons are named.',
  'link-name': 'A product, an offer and a quote each link out; "source" twice over is not a name.',
  'image-alt':
    "A card's picture of its page says what it is of, and is the whole of the link around it.",
  'summary-name': 'Settings, the offers, the changes and the removals are all panels to open.',
  'aria-meter-name': 'The score bar is a meter, and a meter with no name is a number of nothing.',
  'empty-heading': 'The masthead, the results head and every card title say something.',
  'form-field-multiple-labels': 'One label per box: two is a name read twice and a box read wrong.',
  'label-title-only':
    'A box labelled only by a `title` or a description is one whose name is a hint.',

  // The marks: `aria-invalid`, `role="alert"` and `role="status"` (ADR-0033).
  'aria-roles': 'log, status, alert, meter and group are the five roles this page uses.',
  'aria-valid-attr': 'A misspelt `aria-` attribute is a mark that reaches nobody.',
  'aria-valid-attr-value': 'A value ARIA cannot read -- an id pointing at nothing -- is the same.',
  'aria-allowed-attr': 'A mark on an element whose role does not take it is not a mark.',
  'aria-conditional-attr': 'The same rule where whether it is allowed depends on the element.',
  'aria-prohibited-attr': 'A name on an element that cannot carry one is a name nobody hears.',
  'aria-required-attr': 'The score meter carries its minimum, its maximum and where it stands.',
  'aria-allowed-role': 'A role an element may not take is a role an assistive technology drops.',
  'aria-deprecated-role': 'A role that has been withdrawn is one only some readers still answer.',
  'duplicate-id-aria':
    'A marked box points at one sentence: two of an id is a mark read at random.',

  // The panels' lists keep their structure.
  list: 'The quotes, the offers, the score parts, the removals and the changes are lists.',
  listitem: 'And every row of them is in the list it belongs to.',
  'nested-interactive': 'Every box here is inside its own label; a control inside one is not.',
  'heading-order': 'The page is h1, then the results, then a card each -- in that order.',
};

/*
 * Deliberately not checked:
 *
 * - Rules needing pixels or layout (`color-contrast`, `color-contrast-enhanced`,
 *   `target-size`, `avoid-inline-spacing`): jsdom always answers "incomplete".
 * - Whole-document rules (`region`, `bypass`, `landmark-one-main`,
 *   `page-has-heading-one`, `html-has-lang`, `document-title`): the subjects are
 *   components rendered alone.
 * - Markup this app has none of (tables, frames, media, ...).
 *
 * Colour never being the only carrier, and the log panel announcing its lines, are
 * asserted in `progress-log.spec.ts`.
 */

/**
 * What is wrong with this element, as sentences, or nothing at all.
 *
 * An incomplete result counts as a failure: a rule that could not decide did not pass.
 */
export async function accessibilityProblems(
  element: HTMLElement,
  rules: string[] = Object.keys(CHECKED),
): Promise<string[]> {
  const known = new Set(axe.getRules().map((rule) => rule.ruleId));
  const unknown = rules.filter((rule) => !known.has(rule));
  if (unknown.length) {
    // `runOnly` silently drops an unknown rule, which would pass.
    throw new Error(`not rules axe knows about: ${unknown.join(', ')}`);
  }

  const run = await axe.run(element, {
    runOnly: { type: 'rule', values: rules },
    resultTypes: ['violations', 'incomplete'],
  });

  return [...run.violations, ...run.incomplete].flatMap((result) =>
    result.nodes.map((node) => `${result.id}: ${result.help} — ${node.html}`),
  );
}
