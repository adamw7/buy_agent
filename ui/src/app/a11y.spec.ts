import { CHECKED, accessibilityProblems } from './a11y';

/** A piece of page, in the document, where axe can see it: a rule's answer about
 *  an element nothing has rendered is not the answer it would give about a page. */
async function about(html: string, rules?: string[]): Promise<string[]> {
  const holder = document.createElement('div');
  holder.innerHTML = html;
  document.body.append(holder);
  try {
    return await accessibilityProblems(holder, rules);
  } finally {
    holder.remove();
  }
}

describe('the accessibility check', () => {
  it('says what is wrong and which element it is wrong about', async () => {
    /* The failure path, run on purpose: a check whose report has never once been
       written is a check that may not be able to write one, and four green
       assertions elsewhere would say nothing either way. */
    const problems = await about('<label>Name</label><input type="text" />');

    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain('label:');
    expect(problems[0]).toContain('<input type="text">');
  });

  it('counts a rule that could not decide as a rule that did not pass', async () => {
    /* `aria-label` on a `<div>` is a name the element's role forbids it to carry.
       axe reports it as incomplete rather than as a violation, and an incomplete
       result left out is the one defect this check found on the day it was
       written going unreported. */
    expect(await about('<div aria-label="Rank 1">#1</div>')).toHaveLength(1);
  });

  it('refuses a rule axe has never heard of', async () => {
    /* `runOnly` drops a name it does not know, so a misspelt rule is a check that
       has quietly stopped checking -- and a check that runs no rules passes. */
    await expect(about('<p>fine</p>', ['no-such-rule'])).rejects.toThrow('no-such-rule');
  });

  it('is a list of rules, each with the promise it holds', () => {
    /* The reasons are the point: a rule turned on without one is the blanket run
       this check exists instead of (ADR-0049). */
    expect(Object.keys(CHECKED).length).toBeGreaterThan(0);
    for (const [rule, why] of Object.entries(CHECKED)) {
      expect(why, rule).not.toBe('');
    }
  });
});
