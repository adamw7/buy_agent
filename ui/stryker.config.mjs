// Settings for Stryker, the mutation tester `.github/workflows/mutation-ui.yml`
// runs against the front end every Saturday. It is ADR-0016's argument applied to
// the other half of the project -- coverage says a line ran, not that anything
// would notice if it stopped working -- and ADR-0061 is why it is shaped like
// this. `setup.cfg` is this file for the package.
/** @type {import('@stryker-mutator/api/core').PartialStrykerOptions} */
export default {
  // What the specs are run with, one mutant at a time. Stryker instruments the
  // sources with every mutant at once and switches one on per run, so a run is
  // the ordinary `ng test` with an environment variable set -- and 1031 of them,
  // which is what makes this a job of its own. There is no cheaper runner here:
  // Stryker's vitest runner drives vitest itself, and what compiles an Angular
  // component is the builder rather than vitest.
  testRunner: 'command',
  commandRunner: { command: 'npx ng test --ts-config=tsconfig.mutation.json' },

  // The four components, the service and the download helper: the code a
  // surviving mutant says something about. `agent.types.ts` is the payloads
  // written down as types, `testing.ts` is the ones the specs are written
  // against, `a11y.ts` is the accessibility rules they are held to and the
  // sentence saying what each one is for, and `app.config.ts` is what `main.ts`
  // boots the app with rather than anything a spec judges.
  mutate: [
    'src/app/**/*.ts',
    '!src/app/**/*.spec.ts',
    '!src/app/a11y.ts',
    '!src/app/agent.types.ts',
    '!src/app/app.config.ts',
    '!src/app/testing.ts',
  ],

  // A run is a whole `ng test`, which is ten seconds warm and slower still with
  // four of them sharing a runner. Stryker's own default -- five seconds plus
  // half the dry run again -- would report a loaded runner as a killed mutant,
  // which is a score that goes up the busier the machine is.
  timeoutMS: 120_000,
  timeoutFactor: 2,

  // `json` is what scripts/mutation_report.py reads for the job summary; `html`
  // is the run read one mutant at a time, which the job uploads.
  reporters: ['progress', 'json', 'html'],

  // Nothing here fails the run: the floor is the report's, for the reason
  // ADR-0016 gives the package's -- an exit code says a run failed and a report
  // says which half slipped. `high` and `low` only colour the HTML.
  thresholds: { break: null },
};
