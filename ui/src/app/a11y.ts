/**
 * The accessibility check the four component specs run, and the rules it runs.
 *
 * The form's whole design is a claim about what somebody can perceive and reach:
 * ADR-0033 puts every refusal on the box it is about, `ApiError.field` exists so
 * the `failure` event can carry a mark to a control, and a mark on a box nobody
 * can type into is one nobody can act on. The specs assert the wiring -- a class
 * on an element, a sentence in a `<small>` -- and a mark rendered as a colour
 * alone, a control with no accessible name and an error that never reaches an
 * assistive technology are all green under that.
 *
 * So the rules are turned on by name, one at a time, each holding a promise this
 * project has already made: `.pylintrc`'s argument (ADR-0049), one language over.
 * A blanket run of all hundred-odd of axe's rules would fail on rules nobody
 * here has decided about, and pass vacuously on the ones jsdom cannot answer.
 */
import axe from 'axe-core';

/** The rules that run, and which promise each one holds. Every one of them is
 *  applicable to at least one of the four components as they stand, so none of
 *  them is a rule that passes by matching nothing. */
export const CHECKED: Record<string, string> = {
  // Every control has a label or an accessible name. Each of these is one kind
  // of control this page draws, and the page draws no other kind.
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

  // The marks themselves. ADR-0033 puts a refusal on the box it is about, which
  // is an ARIA claim before it is a CSS one: `aria-invalid` on the box, a live
  // `role="alert"` under it, `role="status"` on what the page is waiting for.
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

  // What the panels are made of. The opinions, the offers, the removals and the
  // changes are lists, and a list read as four paragraphs loses the count.
  list: 'The quotes, the offers, the score parts, the removals and the changes are lists.',
  listitem: 'And every row of them is in the list it belongs to.',
  'nested-interactive': 'Every box here is inside its own label; a control inside one is not.',
  'heading-order': 'The page is h1, then the results, then a card each -- in that order.',
};

/*
 * What is deliberately not checked, which is a decision rather than an oversight.
 *
 * - Anything needing a browser. `color-contrast` and `color-contrast-enhanced`
 *   want pixels, `target-size` and `avoid-inline-spacing` want layout, and jsdom
 *   has neither -- a run here answers "incomplete" for all four, forever. They
 *   are a browser's job, the way the CSP and the critical-CSS inliner already
 *   are.
 * - Anything about a whole document. `region`, `bypass`, `landmark-one-main`,
 *   `page-has-heading-one`, `html-has-lang` and `document-title` are about the
 *   page `index.html` serves, and three of the four subjects here are one
 *   component of it rendered on its own, where every one of those is wrong
 *   either way.
 * - Anything about markup this app has none of. Tables, frames, media, definition
 *   lists, `<marquee>`: a rule matching nothing passes, and a rule that has never
 *   once been applicable says nothing about the next change. The one image is a
 *   card's picture of its page (ADR-0065), and `image-alt` is on for it above.
 *
 * Two of the promises above have no rule at all, here or in a browser: that a
 * colour never carries something on its own, and that a panel a run takes a
 * minute to fill announces what lands in it. Both are asserted where they are
 * made, in `progress-log.spec.ts`.
 */

/**
 * What is wrong with this element, as sentences, or nothing at all.
 *
 * An **incomplete** result counts, which is the other half of naming the rules:
 * a rule that ran and could not decide is not a rule that passed, and the one
 * defect this check found on the day it was written -- an `aria-label` on a
 * `<div>`, which is a name the element's role forbids it to carry -- was reported
 * that way rather than as a violation.
 */
export async function accessibilityProblems(
  element: HTMLElement,
  rules: string[] = Object.keys(CHECKED),
): Promise<string[]> {
  const known = new Set(axe.getRules().map((rule) => rule.ruleId));
  const unknown = rules.filter((rule) => !known.has(rule));
  if (unknown.length) {
    // A rule axe has never heard of is one `runOnly` quietly drops, which is a
    // check that has stopped checking and says so by passing. The same argument
    // the architecture tests make about a rule whose subject matches nothing.
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
