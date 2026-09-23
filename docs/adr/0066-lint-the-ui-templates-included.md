# ADR-0066: Lint the UI, templates included, the way pylint reads the package

- **Status:** Accepted
- **Date:** 2026-09-23

## Context

The two halves of this project were checked the opposite way round. The Python
half has a linter and a type checker and no formatter (ADR-0048, ADR-0063); the
UI had a type checker (`npm run build`, with `strict` and `strictTemplates`) and a
formatter, Prettier, gated by `npm run format:check` -- and nothing that decided
anything but layout. ADR-0048 and ADR-0049 argue that a check is worth having
when it states a rule the code already holds everywhere, and the UI holds a good
many of those with nothing asserting them: every component is `OnPush`, every
input and output is a signal, every dependency comes through `inject()`.

One of those rules is worth more than the rest, because CLAUDE.md describes it as
invisible. `_SECURITY_HEADERS` sends `script-src 'self'`, which refuses every
inline event handler at runtime, and that is how the critical-CSS inliner's
`onload` left the page unstyled: "Neither suite can see that; it takes a
browser." An inline `onclick` in a component template compiles, passes every
spec -- jsdom enforces no CSP -- and does nothing in the only place it is run.

Run over `ui/src` with `angular-eslint`'s recommended and accessibility presets,
the template plugin found seven things. Two were `$any` in a template, each
switching `strictTemplates` off for one expression; one was an output named
`search`, which is a DOM event, so an `(search)` on the element could be answered
by a native one as well; two were unused names in specs; and two were the tool
misreading the code: a `<label>` whose control sits inside an `@if` the rule
cannot see into, and a `[style.inline-size.%]` binding read as an inline style.

## Decision

`ui/eslint.config.mjs` configures ESLint with `typescript-eslint` and
`angular-eslint` over `ui/src`, run as `npm run lint` in the `ui` job of `ci.yml`
and in the preflight skill, and it is configured by the rule `.pylintrc` is:

- **A rule is run over `src/` before it is turned on, and what it finds is fixed
  rather than configured around.** The two `$any`s became casts in the class,
  against a declared element type; the output became `run`; the unused names were
  deleted.
- **Every rule turned on beyond the presets states what the code already does**,
  and says so beside it: `OnPush`, signals, readonly outputs, `inject()`, no
  `$any`, typed buttons, control flow blocks. Every rule left off that was run and
  had findings carries its answer beside it: `no-call-expression` (a signal is
  read by calling it), `prefer-ngsrc` (the one image is somebody else's page,
  ADR-0065), and the two about attribute order and string building, which are
  layout and Prettier's side of the line.
- **The CSP is a rule.** `no-restricted-syntax` refuses an `on*` attribute and a
  `javascript:` URL in any template, `src/index.html` included, naming the CSP in
  the message. A `<script>` element is not among them because neither the Angular
  compiler nor its parser lets one through for a rule to see; that remains the one
  half of the paragraph in CLAUDE.md that is still a comment, alongside
  `inlineCritical` in `angular.json`.
- **Every suppression is on its own lines with its reason**, in an HTML comment
  directly above the element, and `reportUnusedDisableDirectives` is an error, so a
  suppression that has stopped being needed fails -- `useless-suppression` and
  `warn_unused_ignores` one tool over. `--max-warnings 0` makes a warning count.
- **Specs are linted by the same rules.** The issue asked whether they would need
  overrides the way `tests/` sits outside pylint's target; run over them, the only
  findings were two unused names, which were real, so no override is written until
  a rule needs one.

It runs after the tests and the build and before the formatting check: a job stops
at its first failing step, the tests and the build are what a change is about, and
of the two reading steps the linter says more.

## Consequences

- Four dev dependencies in `ui/package.json` (`eslint`, `@eslint/js`,
  `typescript-eslint`, `angular-eslint`), which Renovate watches like the rest, and
  a few seconds per `ui` job.
- An inline event handler, a `javascript:` URL, a `$any` or a component dropping
  `OnPush` is a red pull request rather than something only a browser notices.
- The asymmetry CLAUDE.md described is narrower rather than gone: the UI has a
  linter and a formatter, and the Python half still has no formatter.
- The rule set is a decision like `.pylintrc`'s: adding a rule means running it
  over `ui/src` first and fixing what it finds, and a rule left off gets its reason
  written beside it.
- The config lives in `ui/` beside `angular.json` and `stryker.config.mjs`, as the
  sixth of the files that configure CI; it holds no number, like `.pylintrc`.
