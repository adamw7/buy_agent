// The UI's linter, configured by the rule `.pylintrc` is (ADR-0048, ADR-0049): a
// rule is run over `src/` before it is turned on and what it finds is fixed rather
// than configured around, every rule left off carries the answer this project has
// already given, and every line suppressed carries its reason beside it. Prettier
// decides the layout; nothing here does, so no stylistic preset is loaded.
import eslint from '@eslint/js';
import tseslint from 'typescript-eslint';
import angular from 'angular-eslint';

// The CSP `_SECURITY_HEADERS` sends is `script-src 'self'`, which refuses every
// inline handler at runtime -- the failure CLAUDE.md calls invisible to both suites
// ("it takes a browser"), since the page renders and the handler silently does
// nothing. These are that paragraph in a form a machine reads. A `<script>` element
// is not among them because nothing could see one: Angular's compiler strips it
// from a component template, and its parser drops it before a rule could look, in
// `src/index.html` too -- so that file's one remaining trap stays a comment.
const refusedByTheCsp = [
  {
    selector: 'TextAttribute[name=/^on/i]',
    message:
      "An inline handler is refused by the CSP's script-src 'self' at runtime; bind the event with (event) instead.",
  },
  {
    selector: 'TextAttribute[name=/^(href|src|action|formaction)$/i][value=/^\\s*javascript:/i]',
    message: "A javascript: URL is an inline script, which the CSP's script-src 'self' refuses.",
  },
];

export default tseslint.config(
  {
    // A suppression nothing needs any more fails the run: `useless-suppression`
    // for pylint and `warn_unused_ignores` for mypy, one tool over (ADR-0063).
    linterOptions: { reportUnusedDisableDirectives: 'error' },
  },
  {
    files: ['**/*.ts'],
    extends: [
      eslint.configs.recommended,
      ...tseslint.configs.recommended,
      ...angular.configs.tsRecommended,
    ],
    processor: angular.processInlineTemplates,
    rules: {
      // Each of these states what every component here already does, which is the
      // bar ADR-0049 sets for a checker: signals rather than decorators and zone
      // tracking, so change detection only needs asking when an input or a signal
      // moves, and `inject()` rather than constructor parameters.
      '@angular-eslint/prefer-on-push-component-change-detection': 'error',
      '@angular-eslint/prefer-signals': 'error',
      '@angular-eslint/prefer-output-readonly': 'error',
      '@angular-eslint/prefer-inject': 'error',
    },
  },
  {
    files: ['**/*.html'],
    extends: [...angular.configs.templateRecommended, ...angular.configs.templateAccessibility],
    rules: {
      'no-restricted-syntax': ['error', ...refusedByTheCsp],
      // `$any` switches `strictTemplates` off for one expression, which is the
      // promise `ui/tsconfig.json` makes over the bindings; cast in the class,
      // where the cast is at least written against a declared type.
      '@angular-eslint/template/no-any': 'error',
      // A `style` attribute is a second place a rule of the stylesheet is written.
      // A `[style.x]` binding is allowed: it goes through CSSOM, which no CSP
      // refuses, and it is how a figure off a payload becomes a width.
      '@angular-eslint/template/no-inline-styles': ['error', { allowBindToStyle: true }],
      // A `<button>` inside the form with no type submits it, starting a run.
      '@angular-eslint/template/button-has-type': 'error',
      // `[name]` beside `[attr.name]` is the one pair written twice on purpose
      // (search-form.html says why), and it is suppressed there, on its lines.
      '@angular-eslint/template/no-duplicate-attributes': 'error',
      '@angular-eslint/template/prefer-control-flow': 'error',
      '@angular-eslint/template/prefer-self-closing-tags': 'error',
      // Left off, each with its answer:
      // - `no-call-expression`: a signal is read by calling it, so every binding
      //   in these templates is a call.
      // - `prefer-ngsrc`: `NgOptimizedImage` is for a site's own assets. The one
      //   `<img>` is a picture of somebody else's page, already `loading="lazy"`
      //   with its 640 x 400 reserved (ADR-0065), and the directive would only add
      //   warnings about a JPEG whose size is whatever the camera made.
      // - `attributes-order`, `prefer-template-literal`: preferences about how a
      //   template reads, which is Prettier's side of the line and not this one's.
    },
  },
);
